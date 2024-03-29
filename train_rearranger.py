from dataclasses import dataclass, field
import struct
import zlib
import os
import sys
import traceback


LATEST_SUPPORTED_SAVE_VERSION = 42


@dataclass
class SaveHeader:
    save_header_type: int
    save_version: int
    build_version: int
    map_name: bytes
    map_options: bytes
    session_name: bytes
    play_duration_seconds: int
    save_date_time: int
    session_visibility: int
    f_editor_object_version: int
    mod_metadata: bytes
    is_modded_save: int
    save_identifier: bytes


@dataclass
class String:
    string: bytes
    encoding: str

    def decode(self):
        return self.string.decode(encoding=self.encoding)


@dataclass
class ObjectReference:
    level_name: bytes
    path_name: bytes


@dataclass
class Property:
    name: bytes
    index: int


@dataclass
class BoolProperty(Property):
    value: int
    guid: bytes | None


@dataclass
class IntProperty(Property):
    guid: bytes | None
    value: int


class Int8Property(IntProperty):
    pass


class UInt32Property(IntProperty):
    pass


class Int64Property(IntProperty):
    pass


class UInt64Property(IntProperty):
    pass


@dataclass
class FloatProperty(Property):
    guid: bytes | None
    value: float


class DoubleProperty(FloatProperty):
    pass


@dataclass
class StrProperty(Property):
    guid: bytes | None
    value: String


class NameProperty(StrProperty):
    pass


@dataclass
class ObjectProperty(Property):
    guid: bytes | None
    value: ObjectReference


class InterfaceProperty(ObjectProperty):
    pass


@dataclass
class EnumProperty(Property):
    enum_name: bytes
    guid: bytes | None
    enum_value: bytes


@dataclass
class ByteProperty(Property):
    enum_name: bytes
    guid: bytes | None
    value: bytes | int


@dataclass
class TextProperty(Property):
    guid: bytes | None
    prop_dict: dict


@dataclass
class ArrayProperty(Property):
    value_type: bytes
    values: list


@dataclass
class StructProperty(Property):
    value_type: bytes
    value_dict: dict


@dataclass
class Object:
    class_name: bytes
    path_name: bytes
    outer_path_name: bytes
    properties: list[Property] = field(default_factory=list)
    missing: bytes = b""


@dataclass
class Actor:
    class_name: bytes
    path_name: bytes
    entity: ObjectReference = None
    children: list[ObjectReference] = field(default_factory=list)
    properties: list[Property] = field(default_factory=list)
    missing: bytes = b""


class DataCursor:
    def __init__(self, data):
        self.data: bytes = data
        self.cursor = 0

    def __len__(self):
        return len(self.data)

    def read(self, length: int | None = None):
        if length is None:
            result = self.data[self.cursor:]
            self.cursor = len(self.data)
        else:
            result = self.data[self.cursor:self.cursor+length]
            self.cursor += length
        return result

    def peek(self, length):
        return self.data[self.cursor:self.cursor+length]

    def read_single_type(self, struct_format):
        length = struct.calcsize(struct_format)
        data = self.read(length)
        return struct.unpack(struct_format, data)[0]

    def read_array(self, struct_format):
        length = struct.calcsize(struct_format)
        data = self.read(length)
        return struct.unpack(struct_format, data)

    def read_int8(self) -> int:
        return self.read_single_type("<b")

    def read_uint8(self) -> int:
        return self.read_single_type("<B")

    def read_int16(self) -> int:
        return self.read_single_type("<h")

    def read_uint16(self) -> int:
        return self.read_single_type("<H")

    def read_int32(self) -> int:
        return self.read_single_type("<i")

    def read_uint32(self) -> int:
        return self.read_single_type("<I")

    def read_int64(self) -> int:
        return self.read_single_type("<q")

    def read_uint64(self) -> int:
        return self.read_single_type("<Q")

    def read_float(self) -> float:
        return self.read_single_type("<f")

    def read_double(self) -> float:
        return self.read_single_type("<d")

    def read_array_uint8(self, length) -> tuple[int, ...]:
        return self.read_array(f"<{length}b")

    def read_array_int32(self, length) -> tuple[int, ...]:
        return self.read_array(f"<{length}i")

    def read_array_int64(self, length) -> tuple[int, ...]:
        return self.read_array(f"<{length}q")

    def read_array_float(self, length) -> tuple[int, ...]:
        return self.read_array(f"<{length}f")

    def seek(self, position):
        if 0 <= position <= len(self.data):
            self.cursor = position
        else:
            raise IndexError("Position out of data range")

    def skip(self, length):
        self.cursor += length


class SaveDataCursor(DataCursor):
    def __init__(self, data, save_file_version=None):
        super().__init__(data)
        self.save_file_version = save_file_version
        # Keep track of the name, start and end position and number of elements in each array that is found
        self.array_cursors: dict[bytes, tuple[int, int, int]] = {}

    def read_string(self) -> String:
        length = self.read_int32()
        if length < 0:
            result = String(self.read(-length * 2 - 2), "utf-16")
            self.skip(2)
            return result
        result = String(self.read(length-1), "utf-8")
        self.skip(1)
        return result

    def skip_string(self):
        length = self.read_int32()
        if length < 0:
            self.skip(-length * 2)
        self.skip(length)

    def read_object_minimal(self) -> Object:
        class_name = self.read_string().string
        self.skip_string()  # level_name
        path_name = self.read_string().string
        outer_path_name = self.read_string().string
        return Object(class_name, path_name, outer_path_name)

    def read_object_reference(self) -> ObjectReference:
        return ObjectReference(self.read_string().string, self.read_string().string)

    def skip_object_property(self):
        self.skip_string()  # level_name
        self.skip_string()  # path_name

    def read_actor_minimal(self) -> Actor:
        class_name = self.read_string().string
        self.skip_string()  # level_name
        path_name = self.read_string().string
        self.skip(48)
        return Actor(class_name, path_name)

    def skip_entity(self):
        if self.save_file_version >= 41:
            self.skip(8)  # entity save version + int32
        entity_length = self.read_int32()
        self.skip(entity_length)

    def read_entity(self, obj: Object | Actor):
        if self.save_file_version >= 41:
            self.skip(8)  # entity save version + int32
        entity_length = self.read_int32()
        start_cursor = self.cursor
        if isinstance(obj, Actor):
            obj.entity = self.read_object_reference()
            child_count = self.read_int32()
            for _ in range(child_count):
                obj.children.append(self.read_object_reference())

        # Read properties
        while True:
            prop = self.read_property()
            if prop is None:
                break
            if prop.name != b"CachedActorTransform":
                obj.properties.append(prop)

        missing_bytes = start_cursor + entity_length - self.cursor
        if missing_bytes > 4:
            obj.missing = self.read(missing_bytes)
        else:
            self.skip(4)

    def read_property(self) -> Property | None:
        name = self.read_string().string
        if name == b"None":
            return None

        if self.peek(1) == b"\x00":
            self.skip(1)

        property_type = self.read_string().string
        self.skip(4)  # Byte length of the property

        index = self.read_int32()

        match property_type:
            case b"BoolProperty":
                return BoolProperty(name, index, self.read_uint8(), self.read_property_guid())
            case b"Int8Property":
                return Int8Property(name, index, self.read_property_guid(), self.read_int8())
            case b"IntProperty":
                return IntProperty(name, index, self.read_property_guid(), self.read_int32())
            case b"UInt32Property":
                return UInt32Property(name, index, self.read_property_guid(), self.read_uint32())
            case b"Int64Property":
                return Int64Property(name, index, self.read_property_guid(), self.read_int64())
            case b"UInt64Property":
                return Int64Property(name, index, self.read_property_guid(), self.read_uint64())
            case b"FloatProperty":
                return FloatProperty(name, index, self.read_property_guid(), self.read_float())
            case b"DoubleProperty":
                return DoubleProperty(name, index, self.read_property_guid(), self.read_double())
            case b"StrProperty" | b"Name":
                return StrProperty(name, index, self.read_property_guid(), self.read_string())
            case b"NameProperty":
                return NameProperty(name, index, self.read_property_guid(), self.read_string())
            case b"ObjectProperty":
                return ObjectProperty(name, index, self.read_property_guid(), self.read_object_reference())
            case b"InterfaceProperty":
                return InterfaceProperty(name, index, self.read_property_guid(), self.read_object_reference())
            case b"EnumProperty":
                return EnumProperty(name, index, self.read_string().string, self.read_property_guid(), self.read_string().string)
            case b"ByteProperty":
                enum_name = self.read_string().string
                guid = self.read_property_guid()
                if enum_name == b"None":
                    value = self.read_uint8()
                else:
                    value = self.read_string().string
                return ByteProperty(name, index, enum_name, guid, value)
            case b"TextProperty":
                return TextProperty(name, index, self.read_property_guid(), self.read_text_property())
            case b"ArrayProperty":
                return ArrayProperty(name, index, *self.read_array_property(name))
            case b"StructProperty":
                return StructProperty(name, index, *self.read_struct_property())
            case _:
                raise NotImplementedError

    def read_property_guid(self) -> bytes | None:
        has_property_guid = self.read_uint8()
        if has_property_guid == 1:
            return self.read(16)
        return None

    def read_text_property(self) -> dict:
        flags = self.read_int32()
        history_type = self.read_uint8()

        match history_type:
            case 0:
                return {"flags": flags,
                        "history_type": history_type,
                        "namespace": self.read_string(),
                        "key": self.read_string(),
                        "value": self.read_string()}
            case 1 | 3:
                source_fmt = self.read_text_property()

                argument_count = self.read_int32()
                arguments = []
                for _ in range(argument_count):
                    argument_name = self.read_string()
                    value_type = self.read_uint8()
                    if value_type == 4:
                        arguments.append({"name": argument_name, "value_type": value_type, "value": self.read_text_property()})
                    else:
                        raise NotImplementedError

                return {"flags": flags,
                        "history_type": history_type,
                        "source_fmt": source_fmt,
                        "arguments": arguments}
            case 10:
                return {"flags": flags,
                        "history_type": history_type,
                        "source_text": self.read_text_property(),
                        "transform_type": self.read_uint8()}
            case 11:
                return {"flags": flags,
                        "history_type": history_type,
                        "table_id": self.read_string(),
                        "text_key": self.read_string()}
            case 255:
                has_culture_invariant_string = self.read_int32()
                if has_culture_invariant_string == 1:
                    value = self.read_string()
                else:
                    value = None
                return {"flags": flags,
                        "history_type": history_type,
                        "has_culture_invariant_string": has_culture_invariant_string,
                        "value": value}
            case _:
                raise NotImplementedError

    def read_array_property(self, property_name: bytes) -> tuple[bytes, list]:
        value_type = self.read_string().string
        values = []
        self.skip(1)

        array_property_count = self.read_int32()
        array_start_cursor = self.cursor
        match value_type:
            case b"ObjectProperty" | b"InterfaceProperty":
                for _ in range(array_property_count):
                    values.append(self.read_object_reference())
            case _:
                raise NotImplementedError
        array_end_cursor = self.cursor
        self.array_cursors[property_name] = (array_property_count, array_start_cursor, array_end_cursor)
        return value_type, values

    def read_struct_property(self) -> tuple[bytes, dict]:
        value_type = self.read_string().string
        self.skip(17)

        # Because this script only limits itself to RailroadSubsystem, FGTrainStationIdentifier and BP_Train_C,
        # it only needs to be able to parse generic struct value types
        values = []
        while True:
            sub_struct_property = self.read_property()
            if sub_struct_property is None:
                break
            values.append(sub_struct_property)
            if isinstance(sub_struct_property, StructProperty) \
                    and sub_struct_property.value_type == b"InventoryItem" \
                    and sub_struct_property.value_dict["property"] is None:
                break
        value_dict = {"values": values}
        return value_type, value_dict


class SaveTrainParser:
    def __init__(self, file_object):
        self.savefile = SaveDataCursor(file_object.read())

        self.max_chunk_size = 0
        self.save_header_version = 0
        self.save_file_version = 0
        self.header_length = 0
        self.chunk_header_begin: bytes | None = None

        self.railroad_system: Actor | None = None
        self.train_station_identifiers: dict[bytes, Actor] = {}
        self.trains: dict[bytes, Actor] = {}

        self.header = self.read_header()
        self.body = SaveDataCursor(self.unzip_body(), self.save_file_version)
        self.parse_body()

    def read_header(self) -> bytes:
        self.save_header_version = self.savefile.read_int32()
        self.save_file_version = self.savefile.read_int32()
        if self.save_file_version > LATEST_SUPPORTED_SAVE_VERSION:
            print("This save file version is using a newer version than this tool is currently updated for.")
            print("It may work, but if any errors occur, please create an Issue on GitHub to let me know.")

        self.header_length = self.savefile.data.find(b"\xC1\x83\x2A\x9E")
        if self.header_length == -1:
            raise ValueError("Start of compressed body not found")
        self.savefile.seek(0)
        return self.savefile.read(self.header_length)

    def unzip_body(self) -> bytes:
        print("Decompressing...", end="", flush=True)
        if self.save_file_version >= 41:
            chunk_header_length = 49
            chunk_header_begin_length = 17
        else:
            chunk_header_length = 48
            chunk_header_begin_length = 16

        inflated_chunks = []
        while chunk_header := DataCursor(self.savefile.read(chunk_header_length)):
            if self.chunk_header_begin is None:
                chunk_header.seek(8)
                self.max_chunk_size = chunk_header.read_uint32()
                chunk_header.seek(0)
                self.chunk_header_begin = chunk_header.read(chunk_header_begin_length)

            chunk_header.seek(chunk_header_begin_length)
            current_chunk_size = chunk_header.read_uint32()
            current_chunk = self.savefile.read(current_chunk_size)

            inflated_chunks.append(zlib.decompress(current_chunk))
        print("Done")
        return b"".join(inflated_chunks)

    def write_file(self, filename):
        with open(filename, "wb") as f:
            self.savefile.seek(0)
            f.write(self.savefile.read(self.header_length))  # Write the original header

            self.body.seek(0)
            print("Compressing...", end="", flush=True)
            while chunk_uncompressed := self.body.read(self.max_chunk_size):
                compressed_chunk = zlib.compress(chunk_uncompressed)
                chunk_header = self.chunk_header_begin + struct.pack("QQQQ",
                                                                     len(compressed_chunk),
                                                                     len(chunk_uncompressed),
                                                                     len(compressed_chunk),
                                                                     len(chunk_uncompressed))
                f.write(chunk_header)
                f.write(compressed_chunk)
        print("Done")

    def parse_body(self):
        print("Parsing...", end="", flush=True)
        if self.save_file_version >= 41:
            self.body.skip(8)
            grid_count = self.body.read_int32()
            self.body.skip_string()
            self.body.skip(12)  # int64 + int32
            self.body.skip_string()
            self.body.skip(4)  # int32
            for _ in range(1, grid_count):
                self.body.skip_string()
                self.body.skip(8)  # int32 + int32
                partition_count = self.body.read_int32()
                for _ in range(partition_count):
                    self.body.skip_string()
                    self.body.skip(4)  # uint32
        else:
            self.body.skip(4)

        nb_levels = self.body.read_int32()
        for level_i in range(nb_levels+1):
            if level_i < nb_levels:
                self.body.skip_string()  # level_name

            if self.save_file_version >= 41:
                objects_length = self.body.read_int64()
            else:
                objects_length = self.body.read_int32()
            objects_start_cursor = self.body.cursor
            objects_count = self.body.read_int32()
            entities_to_objects: list[Object | Actor] = []
            for _ in range(objects_count):
                object_type = self.body.read_int32()
                match object_type:
                    case 0:
                        obj = self.body.read_object_minimal()
                        entities_to_objects.append(obj)
                    case 1:
                        actor = self.body.read_actor_minimal()
                        entities_to_objects.append(actor)
                    case _:
                        raise ValueError(f"Unknown object type: {object_type}")

            if not self.save_file_version >= 41 or self.body.cursor < objects_start_cursor + objects_length:
                collected_count = self.body.read_int32()
                for _ in range(collected_count):
                    self.body.skip_object_property()

            # entitiesBinaryLength
            if self.save_file_version >= 41:
                self.body.skip(8)
            else:
                self.body.skip(4)

            self.body.skip(4)  # entities count
            for obj in entities_to_objects:
                path_name = obj.path_name
                if path_name == b"Persistent_Level:PersistentLevel.RailroadSubsystem":
                    self.body.read_entity(obj)
                    self.railroad_system = obj
                elif b"Persistent_Level:PersistentLevel.FGTrainStationIdentifier_" in path_name:
                    self.body.read_entity(obj)
                    self.train_station_identifiers[path_name] = obj
                elif b"Persistent_Level:PersistentLevel.BP_Train_C_" in path_name:
                    self.body.read_entity(obj)
                    self.trains[path_name] = obj
                else:
                    self.body.skip_entity()

            collected_count = self.body.read_int32()
            for _ in range(collected_count):
                self.body.skip_object_property()
        print("Done")

    def edit_array(self, array_name: bytes, array_objects: list[ObjectReference]):
        body_array_length, body_array_start, body_array_end = self.body.array_cursors[array_name]
        if len(array_objects) != body_array_length:
            raise ValueError("array length does not match original")

        entries = []
        for array_object in array_objects:
            level_name = array_object.level_name
            path_name = array_object.path_name
            level_name_size = len(level_name) + 1
            path_name_size = len(path_name) + 1

            entries.append(struct.pack(f"<i{level_name_size}si{path_name_size}s", level_name_size, level_name, path_name_size, path_name))
        new_array_data = b"".join(entries)

        if len(new_array_data) != body_array_end - body_array_start:
            raise ValueError("array byte size does not match original")

        self.body.data = self.body.data[:body_array_start] + new_array_data + self.body.data[body_array_end:]

    def get_stations_entries(self) -> list[tuple[String, ObjectReference]]:
        result = []
        property_stations: ArrayProperty = next(prop for prop in self.railroad_system.properties if prop.name == b"mTrainStationIdentifiers")  # noqa
        for station_array_entry in property_stations.values:
            property_station_name: TextProperty = next(prop for prop in self.train_station_identifiers[station_array_entry.path_name].properties if prop.name == b"mStationName")  # noqa
            station_name: String = property_station_name.prop_dict["value"]
            result.append((station_name, station_array_entry))
        return result

    def get_train_entries(self) -> list[tuple[String, ObjectReference]]:
        result = []
        property_trains: ArrayProperty = next(prop for prop in self.railroad_system.properties if prop.name == b"mTrains")  # noqa
        for train_array_entry in property_trains.values:
            property_train_name: TextProperty = next((prop for prop in self.trains[train_array_entry.path_name].properties if prop.name == b"mTrainName"), None)  # noqa
            if property_train_name is None:
                train_name = String(b"Train", "utf-8")
            else:
                train_name = property_train_name.prop_dict["value"]
            result.append((train_name, train_array_entry))
        return result


def read_new_order(filename: str, original_entries: list[tuple[String, ObjectReference]], entry_type: str) -> list[ObjectReference]:
    with open(filename, "r", encoding="utf-8") as f:
        new_entry_name_order = [line.rstrip("\n") for line in f]

    original_entries_copy = original_entries.copy()
    new_array_object_refs = []

    for line_number, new_name in enumerate(new_entry_name_order):
        entry_found = False
        for orig_i, orig_entry in enumerate(original_entries_copy):
            orig_name, orig_object_ref = orig_entry
            if new_name == orig_name.decode():
                new_array_object_refs.append(orig_object_ref)
                entry_found = True
                del original_entries_copy[orig_i]
                break

        if not entry_found:
            print(f'\nError on line {line_number}: {entry_type} "{new_name}" does not exist, or is already used on a line above')
            print(f'Correct "{filename}" and try again')
            wait_for_enter()
            return read_new_order(filename, original_entries, entry_type)

    if len(new_entry_name_order) != len(original_entries):
        print(f"\nError! One or more {entry_type}s are missing from the text file: ")
        missing_names = []
        for orig_name, _ in original_entries:
            orig_name_str = orig_name.decode()
            if orig_name_str not in new_entry_name_order:
                missing_names.append(orig_name_str)
        print(", ".join(missing_names))
        print(f'Correct "{filename}" and try again.')
        wait_for_enter()
        return read_new_order(filename, original_entries, entry_type)

    os.remove(filename)
    return new_array_object_refs


def wait_for_enter():
    input("Press Enter to continue...")


def main():
    if len(sys.argv) != 2:
        _, extension = os.path.splitext(sys.argv[0])
        print(f"Usage: drag and drop a Satisfactory .sav file on this {extension} file.")
        print("Closing...\n")
        wait_for_enter()
        exit(1)

    input_save_path = sys.argv[1]

    with open(input_save_path, "rb") as f:
        parser = SaveTrainParser(f)

    station_entries = parser.get_stations_entries()
    train_entries = parser.get_train_entries()

    with open("station list.txt", "w", encoding="utf-8") as f:
        for station_name, station_entry in station_entries:
            f.write(station_name.decode())
            f.write("\n")

    with open("train list.txt", "w", encoding="utf-8") as f:
        for train_name, train_entry in train_entries:
            f.write(train_name.decode())
            f.write("\n")

    path, filename = os.path.split(input_save_path)
    name, extension = os.path.splitext(filename)
    output_save_filename = f"{name}_REORDERED{extension}"

    print('\nCreated "station list.txt" and "train list.txt".')
    print("Rearrange the order of the names to your liking in a text editor, and save.")
    print("NOTE: Exactly one name per line, names can not be changed; only reordered")
    print()
    print(f"After saving, press any key in this window to read the new orders and generate the edited save file.")
    print(f'Output save file will be called "{output_save_filename}"')
    print()
    wait_for_enter()

    new_station_array_entries = read_new_order("station list.txt", station_entries, "Station")
    new_train_array_entries = read_new_order("train list.txt", train_entries, "Train")

    parser.edit_array(b"mTrainStationIdentifiers", new_station_array_entries)
    parser.edit_array(b"mTrains", new_train_array_entries)

    print()
    parser.write_file(output_save_filename)
    print("Success!")
    print(f'Save file saved as "{output_save_filename}"\n')
    input("Press Enter to close...")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        input("Press Enter to close...")
