from dataclasses import dataclass
import struct
import zlib
import os
import sys
import traceback


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


class DataCursor:
    def __init__(self, data):
        self.data = data
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


class SaveDataCursor(DataCursor):
    def __init__(self, data):
        super().__init__(data)
        # Keep track of the name, start and end position and number of elements in each array that is found
        self.array_cursors: dict[bytes, tuple[int, int, int]] = {}

    def read_string(self) -> bytes:
        length = self.read_int32()
        if length < 0:
            return self.read(-length * 2)[:-2]
        return self.read(length)[:-1]

    def skip_string(self):
        length = self.read_int32()
        if length < 0:
            self.cursor += -length * 2
        self.cursor += length

    def read_object_minimal(self) -> dict:
        obj = {"class_name": self.read_string()}
        self.skip_string()  # level_name
        obj["path_name"] = self.read_string()
        obj["outer_path_name"] = self.read_string()
        return obj

    def read_object_property(self) -> dict:
        prop = {"level_name": self.read_string(), "path_name": self.read_string()}
        return prop

    def skip_object_property(self):
        self.skip_string()  # level_name
        self.skip_string()  # path_name

    def read_actor_minimal(self) -> dict:
        actor = {"class_name": self.read_string()}
        self.skip_string()  # level_name
        actor["path_name"] = self.read_string()
        self.cursor += 48
        return actor

    def skip_entity(self):
        entity_length = self.read_int32()
        self.cursor += entity_length

    def read_entity(self, obj: dict):
        entity_length = self.read_int32()
        start_cursor = self.cursor
        if "outer_path_name" not in obj:
            obj["entity"] = self.read_object_property()
            child_count = self.read_int32()
            if child_count > 0:
                obj["children"] = []
                for _ in range(child_count):
                    obj["children"].append(self.read_object_property())

        obj["properties"] = []
        while True:
            prop = self.read_property()
            if prop is None:
                break
            if prop["name"] != "CachedActorTransform":
                obj["properties"].append(prop)

        missing_bytes = start_cursor + entity_length - self.cursor
        if missing_bytes > 4:
            obj["missing"] = self.read(missing_bytes)
        else:
            self.cursor += 4

    def read_property(self) -> dict | None:
        prop: dict = {"name": self.read_string()}

        if prop["name"] == b"None":
            return None

        if self.peek(1) == b"\x00":
            self.cursor += 1

        prop["type"] = self.read_string().replace(b"Property", b"")
        self.read_int32()

        index = self.read_int32()
        if index:
            prop["index"] = index

        match prop["type"]:
            case b"Bool":
                prop["value"] = self.read_uint8()
                prop.update(self.read_property_guid())
            case b"Int8":
                prop.update(self.read_property_guid())
                prop["value"] = self.read_uint8()
            case b"Int" | b"UInt32":
                prop.update(self.read_property_guid())
                prop["value"] = self.read_int32()
            case b"Int64" | b"UInt64":
                prop.update(self.read_property_guid())
                prop["value"] = self.read_int64()
            case b"Float":
                prop.update(self.read_property_guid())
                prop["value"] = self.read_float()
            case b"Double":
                prop.update(self.read_property_guid())
                prop["value"] = self.read_double()
            case b"Str" | b"Name":
                prop.update(self.read_property_guid())
                prop["value"] = self.read_string()
            case b"Object" | b"Interface":
                prop.update(self.read_property_guid())
                prop["value"] = self.read_object_property()
            case b"Enum":
                enum_property_name = self.read_string()
                prop.update(self.read_property_guid())
                prop["value"] = {"name": enum_property_name, "value": self.read_string()}
            case b"Byte":
                enum_name = self.read_string()
                prop.update(self.read_property_guid())
                if enum_name == b"None":
                    prop["value"] = {"enum_name": enum_name, "value": self.read_uint8()}
                else:
                    prop["value"] = {"enum_name": enum_name, "value_name": self.read_string()}
            case b"Text":
                prop.update(self.read_property_guid())
                prop.update(self.read_text_property())
            case b"Array":
                self.read_array_property(prop)
            case b"Struct":
                self.read_struct_property(prop)
            case _:
                raise NotImplementedError

        return prop

    def read_property_guid(self) -> dict:
        has_property_guid = self.read_uint8()
        if has_property_guid == 1:
            return {"property_guid": self.read(16)}
        return {}

    def read_text_property(self) -> dict:
        prop = {"flags": self.read_int32(), "history_type": self.read_uint8()}

        match prop["history_type"]:
            case 0:
                prop["namespace"] = self.read_string()
                prop["key"] = self.read_string()
                prop["value"] = self.read_string()
            case 1 | 3:
                prop["source_fmt"] = self.read_text_property()
                prop["arguments_count"] = self.read_int32()
                prop["arguments"] = []

                for i in range(prop["arguments_count"]):
                    argument_data = {"name": self.read_string(), "value_type": self.read_uint8()}
                    match argument_data["value_type"]:
                        case 4:
                            argument_data["argument_value"] = self.read_text_property()
                        case _:
                            raise NotImplementedError
                    prop["arguments"].append(argument_data)
            case 10:
                prop["source_text"] = self.read_text_property()
                prop["transform_type"] = self.read_uint8()
            case 11:
                prop["table_id"] = self.read_string()
                prop["text_key"] = self.read_string()
            case 255:
                prop["has_culture_invariant_string"] = self.read_int32()
                if prop["has_culture_invariant_string"] == 1:
                    prop["value"] = self.read_string()
            case _:
                raise NotImplementedError
        return prop

    def read_array_property(self, prop: dict):
        prop["value"] = {"type": self.read_string().replace(b"Property", b""), "values": []}
        self.cursor += 1
        array_property_count = self.read_int32()
        array_start_cursor = self.cursor
        match prop["value"]["type"]:
            case b"Object" | b"Interface":
                for _ in range(array_property_count):
                    prop["value"]["values"].append(self.read_object_property())
            case _:
                raise NotImplementedError

        array_end_cursor = self.cursor
        self.array_cursors[prop["name"]] = (array_property_count, array_start_cursor, array_end_cursor)

    def read_struct_property(self, prop: dict):
        prop["value"] = {"type": self.read_string()}
        self.cursor += 17
        match prop["value"]["type"]:
            case b"Color":
                prop["value"]["values"] = {"b": self.read_uint8(), "g": self.read_uint8(), "r": self.read_uint8(), "a": self.read_uint8()}
            case b"LinearColor":
                prop["value"]["values"] = {"b": self.read_float(), "g": self.read_float(), "r": self.read_float(), "a": self.read_float()}
            case b"Vector" | b"Rotator":
                prop["value"]["values"] = {"x": self.read_float(), "y": self.read_float(), "z": self.read_float()}
            case b"Vector2D":
                prop["value"]["values"] = {"x": self.read_float(), "y": self.read_float()}
            case b"Quat" | b"Vector4":
                prop["value"]["values"] = {"a": self.read_float(), "b": self.read_float(), "c": self.read_float(), "d": self.read_float()}
            case b"Box":
                prop["value"]["min"] = {"x": self.read_float(), "y": self.read_float(), "z": self.read_float()}
                prop["value"]["max"] = {"x": self.read_float(), "y": self.read_float(), "z": self.read_float()}
                prop["value"]["is_valid"] = self.read_uint8()
            case b"RailroadTrackPosition":
                prop["value"].update(self.read_object_property())
                prop["value"]["offset"] = self.read_float()
                prop["value"]["forward"] = self.read_float()
            case b"TimeHandle":
                prop["value"]["handle"] = self.read_string()
            case b"Guid":
                prop["value"]["guid"] = self.read(16)
            case b"InventoryItem":
                prop["value"]["unk1"] = self.read_int32()
                prop["value"]["item_name"] = self.read_string()
                prop["value"].update(self.read_object_property())
                prop["value"]["properties"] = [self.read_property()]
            case b"FluidBox":
                prop["value"]["value"] = self.read_float()
            case b"SlateBrush":
                prop["value"]["unk1"] = self.read_string()
            case _:
                prop["value"]["values"] = []
                while True:
                    sub_struct_property = self.read_property()
                    if sub_struct_property is None:
                        break
                    prop["value"]["values"].append(sub_struct_property)
                    if "value" in sub_struct_property \
                            and isinstance(sub_struct_property["value"], dict) \
                            and "properties" in sub_struct_property["value"] \
                            and sub_struct_property["value"]["properties"] == [None]:
                        break

        return prop


class SaveTrainParser:
    def __init__(self, file_object):
        self.savefile = SaveDataCursor(file_object.read())

        self.package_file_tag: int | None = None
        self.max_chunk_size: int | None = None
        self.header_end = 0

        self.railroad_system: dict = {}
        self.train_station_identifiers: dict[bytes, dict] = {}
        self.trains: dict[bytes, dict] = {}

        self.header: SaveHeader = self.parse_header()
        self.body = SaveDataCursor(self.unzip_body())
        self.parse_body()

    def parse_header(self) -> SaveHeader:
        header = SaveHeader(self.savefile.read_int32(),
                            self.savefile.read_int32(),
                            self.savefile.read_int32(),
                            self.savefile.read_string(),
                            self.savefile.read_string(),
                            self.savefile.read_string(),
                            self.savefile.read_int32(),
                            self.savefile.read_int64(),
                            self.savefile.read_uint8(),
                            self.savefile.read_int32(),
                            self.savefile.read_string(),
                            self.savefile.read_int32(),
                            self.savefile.read_string())
        self.header_end = self.savefile.cursor
        return header

    def unzip_body(self) -> bytes:
        print("Decompressing...", end="", flush=True)
        inflated_chunks = []
        while chunk_header := DataCursor(self.savefile.read(48)):
            if self.package_file_tag is None:
                self.package_file_tag = chunk_header.read_uint64()
            if self.max_chunk_size is None:
                self.max_chunk_size = chunk_header.read_uint64()

            chunk_header.seek(16)
            current_chunk_size = chunk_header.read_uint64()
            current_chunk = self.savefile.read(current_chunk_size)

            inflated_chunks.append(zlib.decompress(current_chunk))
        print("Done")
        return b"".join(inflated_chunks)

    def write_file(self, filename):
        with open(filename, "wb") as f:
            self.savefile.seek(0)
            f.write(self.savefile.read(self.header_end))  # Copy the original header

            self.body.seek(0)
            print("Compressing...", end="", flush=True)
            while chunk_uncompressed := self.body.read(self.max_chunk_size):
                compressed_chunk = zlib.compress(chunk_uncompressed)
                chunk_header = struct.pack("QQQQQQ",
                                           self.package_file_tag,
                                           self.max_chunk_size,
                                           len(compressed_chunk),
                                           len(chunk_uncompressed),
                                           len(compressed_chunk),
                                           len(chunk_uncompressed))
                f.write(chunk_header)
                f.write(compressed_chunk)
        print("Done")

    def parse_body(self):
        print("Parsing...", end="", flush=True)
        self.body.seek(4)
        nb_levels = self.body.read_int32()
        for level_i in range(nb_levels+1):
            if level_i < nb_levels:
                self.body.skip_string()  # level_name

            self.body.read_int32()  # objectsBinaryLength
            objects_count = self.body.read_int32()
            entities_to_objects = []
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

            collected_count = self.body.read_int32()
            if collected_count > 0:
                for _ in range(collected_count):
                    self.body.skip_object_property()

            self.body.read_int32()  # entitiesBinaryLength

            entities_count = self.body.read_int32()
            for i in range(entities_count):
                obj = entities_to_objects[i]
                path_name = obj["path_name"]
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
            if collected_count > 0:
                for _ in range(collected_count):
                    self.body.skip_object_property()
        print("Done")

    def edit_array(self, array_name: bytes, array_objects: list[dict]):
        body_array_length, body_array_start, body_array_end = self.body.array_cursors[array_name]
        if len(array_objects) != body_array_length:
            raise ValueError("array length does not match original")

        entries = []
        for array_object in array_objects:
            level_name = array_object["level_name"]
            path_name = array_object["path_name"]
            level_name_size = len(level_name) + 1
            path_name_size = len(path_name) + 1

            entries.append(struct.pack(f"<i{level_name_size}si{path_name_size}s", level_name_size, level_name, path_name_size, path_name))
        new_array_data = b"".join(entries)

        if len(new_array_data) != body_array_end - body_array_start:
            raise ValueError("array byte size does not match original")

        self.body.data = self.body.data[:body_array_start] + new_array_data + self.body.data[body_array_end:]

    def get_stations_entries(self) -> list[tuple[bytes, dict]]:
        result = []
        property_stations = next(prop for prop in self.railroad_system["properties"] if prop["name"] == b"mTrainStationIdentifiers")
        for station_array_entry in property_stations["value"]["values"]:
            property_station_name = next(prop for prop in self.train_station_identifiers[station_array_entry["path_name"]]["properties"] if prop["name"] == b"mStationName")
            station_name = property_station_name["value"]
            result.append((station_name, station_array_entry))
        return result

    def get_train_entries(self) -> list[tuple[bytes, dict]]:
        result = []
        property_trains = next(prop for prop in self.railroad_system["properties"] if prop["name"] == b"mTrains")
        for train_array_entry in property_trains["value"]["values"]:
            property_train_name = next((prop for prop in self.trains[train_array_entry["path_name"]]["properties"] if prop["name"] == b"mTrainName"), {"value": b"Train"})
            train_name = property_train_name["value"]
            result.append((train_name, train_array_entry))
        return result


def decode_bytes(byte_str: bytes) -> str:
    try:
        return byte_str.decode(encoding="utf-8")
    except UnicodeDecodeError:
        return byte_str.decode(encoding="utf-16")


def main():
    if len(sys.argv) != 2:
        print("Usage: drag and drop a save file on this .py file.")
        print("Closing...\n")
        os.system("pause")
        exit(1)

    input_save_path = sys.argv[1]

    with open(input_save_path, "rb") as f:
        parser = SaveTrainParser(f)

    station_entries = parser.get_stations_entries()
    train_entries = parser.get_train_entries()

    with open("station list.txt", "w", encoding="utf-8") as f:
        for station_name, station_entry in station_entries:
            f.write(decode_bytes(station_name))
            f.write("\n")

    with open("train list.txt", "w", encoding="utf-8") as f:
        for train_name, train_entry in train_entries:
            f.write(decode_bytes(train_name))
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
    os.system("pause")

    while True:
        with open("station list.txt", "r", encoding="utf-8") as f:
            new_station_name_order = [line.replace("\n", "") for line in f]

        station_entries_copy = station_entries.copy()
        new_station_array_entries = []
        has_unknown_station = False
        for line_number, new_name in enumerate(new_station_name_order):
            station_found = False

            for i_orig, orig_station in enumerate(station_entries_copy):
                orig_name, orig_entry = orig_station
                if new_name == decode_bytes(orig_name):
                    new_station_array_entries.append(orig_entry)
                    station_found = True
                    del station_entries_copy[i_orig]
                    break

            if not station_found:
                print(f'\nError line {line_number}: station "{new_name}" is unknown or is already on a line above')
                print('Correct "station list.txt" and try again.')
                os.system("pause")
                has_unknown_station = True
                break

        if has_unknown_station:
            continue  # Re-read station list.txt and try again

        if len(new_station_name_order) != len(station_entries):
            print("\nError: one or more stations are missing: ")
            missing_stations = []
            for orig_name, _ in station_entries:
                orig_name_str = decode_bytes(orig_name)
                if orig_name_str not in new_station_name_order:
                    missing_stations.append(orig_name_str)
            print(", ".join(missing_stations))
            print('Correct "station list.txt" and try again.')
            os.system("pause")
            continue  # Re-read station list.txt and try again
        break
    os.remove("station list.txt")

    while True:
        with open("train list.txt", "r", encoding="utf-8") as f:
            new_train_name_order = [line.replace("\n", "") for line in f]

        train_entries_copy = train_entries.copy()
        new_train_array_entries = []
        has_unknown_train = False
        for line_number, new_name in enumerate(new_train_name_order):
            train_found = False

            for i_orig, orig_train in enumerate(train_entries_copy):
                orig_name, orig_entry = orig_train
                if new_name == decode_bytes(orig_name):
                    new_train_array_entries.append(orig_entry)
                    train_found = True
                    del train_entries_copy[i_orig]
                    break

            if not train_found:
                print(f'\nError {line_number}: train "{new_name}" is unknown or is already on a line above')
                print('Correct "train list.txt" and try again.')
                os.system("pause")
                has_unknown_train = False
                break

        if has_unknown_train:
            continue  # Re-read train list.txt and try again

        if len(new_train_name_order) != len(train_entries):
            print("\nError: one or more trains are missing: ")
            missing_trains = []
            for orig_name, _ in train_entries:
                orig_name_str = decode_bytes(orig_name)
                if orig_name_str not in new_train_name_order:
                    missing_trains.append(orig_name_str)
            print(", ".join(missing_trains))
            print('Correct "train list.txt" and try again.')
            os.system("pause")
            continue  # Re-read train list.txt and try again
        break
    os.remove("train list.txt")

    parser.edit_array(b"mTrainStationIdentifiers", new_station_array_entries)
    parser.edit_array(b"mTrains", new_train_array_entries)

    print()
    parser.write_file(output_save_filename)
    print(f'Save file saved as "{output_save_filename}"\n')
    os.system("pause")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        os.system("pause")

