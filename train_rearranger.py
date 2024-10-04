import io
import os
import re
import struct
import sys
import traceback
import zlib
from dataclasses import dataclass
from typing import BinaryIO, Literal

COMPRESSED_BODY_CHUNK_SIG = b"\xC1\x83\x2A\x9E"
LATEST_SUPPORTED_SAVE_VERSION = 46
LITTLE_ENDIAN: Literal["little"] = "little"


@dataclass(frozen=True)
class SaveDataString:
    string: bytes
    encoding: Literal["utf-8", "utf-16"]

    def decode(self):
        return self.string.decode(encoding=self.encoding)

    @staticmethod
    def encode_utf8(string: str):
        return SaveDataString(string.encode("utf-8"), "utf-8")

    def to_bytes(self):
        if self.encoding == "utf-8":
            return (len(self.string) + 1).to_bytes(4, LITTLE_ENDIAN, signed=True) + self.string + b"\x00"
        else:
            return (len(self.string) // -2 + 2).to_bytes(4, LITTLE_ENDIAN, signed=True) + self.string + b"\x00\x00"


@dataclass(frozen=True)
class SaveDataObjectReference:
    level_name: SaveDataString
    path_name: SaveDataString

    def to_bytes(self):
        return self.level_name.to_bytes() + self.path_name.to_bytes()


class SavefileIO(io.BytesIO):
    def __init__(self, data: bytes):
        self._data = data
        super().__init__(data)

    @property
    def data(self) -> bytes:
        return self._data

    def skip(self, offset: int):
        self.seek(offset, os.SEEK_CUR)

    def read_int32(self) -> int:
        return int.from_bytes(self.read(4), LITTLE_ENDIAN, signed=True)

    def read_uint32(self) -> int:
        return int.from_bytes(self.read(4), LITTLE_ENDIAN, signed=True)

    def read_string(self) -> SaveDataString:
        length = self.read_int32()
        if length < 0:
            # Length is negative; this is a UTF-16 string
            result = SaveDataString(self.read(length * -2 - 2), "utf-16")
            self.skip(2)
        else:
            result = SaveDataString(self.read(length - 1), "utf-8")
            self.skip(1)
        return result

    def read_object_reference(self):
        return SaveDataObjectReference(self.read_string(), self.read_string())


class SaveTrainParser:
    def __init__(self, file_object: BinaryIO):
        self.save_header_version: int | None = None
        self.header: bytes | None = None
        self.body: bytes | None = None
        self.max_chunk_size: int | None = None
        self.chunk_header_begin: bytes | None = None

        self.train_station_order_array: tuple[int, int, list[SaveDataObjectReference]] | None = None
        self.train_order_array: tuple[int, int, list[SaveDataObjectReference]] | None = None

        self.train_stations: dict[SaveDataObjectReference, SaveDataString] | None = None
        self.trains: dict[SaveDataObjectReference, SaveDataString] | None = None

        self.parse(file_object)

    def parse(self, savefile: BinaryIO):
        savefile_data = SavefileIO(savefile.read())

        # Warn that this save file is newer than what has been tested
        savefile_data.seek(4)
        self.save_header_version = savefile_data.read_uint32()
        if self.save_header_version > LATEST_SUPPORTED_SAVE_VERSION:
            print("This save file version is using a newer version than this tool is currently updated for.")
            print("It may work, but if any errors occur, please create an Issue on GitHub to let me know.")

        # Find the magic value that indicates the end of the header
        header_length = savefile_data.data.find(COMPRESSED_BODY_CHUNK_SIG)
        if header_length == -1:
            raise ValueError("Start of compressed body not found")
        savefile_data.seek(0)
        self.header = savefile_data.read(header_length)
        zipped_body = savefile_data.read()

        self.body = self.unzip_body(zipped_body)
        self.quick_parse_body()

    def unzip_body(self, zipped_body: bytes) -> bytes:
        print("Decompressing...", end="", flush=True)
        if self.save_header_version < 41:
            chunk_header_length = 48
            chunk_header_begin_length = 16
        else:
            chunk_header_length = 49
            chunk_header_begin_length = 17

        self.max_chunk_size = int.from_bytes(zipped_body[8: 12], "little")
        self.chunk_header_begin = zipped_body[:chunk_header_begin_length]

        unzipped_chunks = []
        body_stream = SavefileIO(zipped_body)
        while chunk_header := body_stream.read(chunk_header_length):
            chunk_header_stream = SavefileIO(chunk_header)
            chunk_header_stream.seek(chunk_header_begin_length)
            chunk_size = chunk_header_stream.read_uint32()
            chunk = body_stream.read(chunk_size)
            unzipped_chunks.append(zlib.decompress(chunk))

        print("Done")
        return b"".join(unzipped_chunks)

    def quick_parse_body(self):
        print("Parsing...", end="", flush=True)
        # Get the arrays which hold the order of stations and trains in the Time Table menu
        self.train_station_order_array = self.find_and_read_array("mTrainStationIdentifiers")
        self.train_order_array = self.find_and_read_array("mTrains")

        # Get the objects, in order of how they appear in the save file
        train_station_references = self.find_present_objects("/Script/FactoryGame.FGTrainStationIdentifier")
        train_references = self.find_present_objects("/Game/FactoryGame/Buildable/Vehicle/Train/-Shared/BP_Train.BP_Train_C")

        # Get the names of stations and trains, which will be in the same order as the object references
        train_station_names = self.find_present_text_properties("mStationName")
        train_names = self.find_present_text_properties("mTrainName")

        # Zip the objects and their names into dicts, so the name of every object can easily be retrieved
        self.train_stations = dict(zip(train_station_references, train_station_names))
        self.trains = dict(zip(train_references, train_names))
        print("Done")

    def write_file(self, filename: str):
        body_stream = SavefileIO(self.body)
        with open(filename, "wb") as f:
            f.write(self.header)  # Write the original header

            print("Compressing...", end="", flush=True)
            while chunk_uncompressed := body_stream.read(self.max_chunk_size):
                compressed_chunk = zlib.compress(chunk_uncompressed)
                chunk_header = self.chunk_header_begin + struct.pack("QQQQ",
                                                                     len(compressed_chunk),
                                                                     len(chunk_uncompressed),
                                                                     len(compressed_chunk),
                                                                     len(chunk_uncompressed))
                f.write(chunk_header)
                f.write(compressed_chunk)
        print("Done")

    def find_and_read_array(self, array_name: str) -> tuple[int, int, list[SaveDataObjectReference]]:
        body_stream = SavefileIO(self.body)
        array_name_bytes = SaveDataString.encode_utf8(array_name).to_bytes()

        property_start = body_stream.data.find(array_name_bytes)
        if property_start == -1:
            raise ValueError(f"Failed to find array {array_name}")

        body_stream.seek(property_start + len(array_name_bytes))
        prop_type = body_stream.read_string().decode()
        if prop_type != "ArrayProperty":
            raise ValueError(f"Failed to parse array {array_name}, was expecting an ArrayProperty, got {prop_type}")

        body_stream.skip(8)  # property binary length + index
        array_type = body_stream.read_string().decode()
        if array_type != "ObjectProperty":
            raise ValueError(f"Failed to parse array {array_name}, was expecting an array of ObjectProperty, got {array_type}")

        body_stream.skip(1)  # Pad byte

        array_length = body_stream.read_uint32()
        array_start = body_stream.tell()
        array_data = [body_stream.read_object_reference() for _ in range(array_length)]
        array_end = body_stream.tell()
        return array_start, array_end, array_data

    def find_present_objects(self, class_name: str) -> list[SaveDataObjectReference]:
        body_stream = SavefileIO(self.body)
        class_name_bytes = SaveDataString.encode_utf8(class_name).to_bytes()

        objects = []
        for match in re.finditer(re.escape(class_name_bytes), body_stream.data):
            body_stream.seek(match.end(0))
            objects.append(body_stream.read_object_reference())

        return objects

    def find_present_text_properties(self, property_name: str) -> list[SaveDataString]:
        body_stream = SavefileIO(self.body)
        search_bytes = SaveDataString.encode_utf8(property_name).to_bytes() + SaveDataString.encode_utf8("TextProperty").to_bytes()

        strings = []
        for match in re.finditer(re.escape(search_bytes), body_stream.data):
            body_stream.seek(match.end(0) + 18)  # Seek to the start of the string of the TextProperty
            strings.append(body_stream.read_string())

        return strings

    def get_stations_entries(self) -> list[tuple[SaveDataObjectReference, SaveDataString]]:
        """
        Get a list of all station objects and their names, ordered by how they appear in the Time Table menu
        """
        result = []
        for station_object in self.train_station_order_array[2]:
            result.append((station_object, self.train_stations[station_object]))
        return result

    def get_train_entries(self) -> list[tuple[SaveDataObjectReference, SaveDataString]]:
        """
        Get a list of all train objects and their names, ordered by how they appear in the Time Table menu
        """
        result = []
        for station_object in self.train_order_array[2]:
            result.append((station_object, self.trains[station_object]))
        return result

    def reorder_train_stations(self, new_order: list[SaveDataObjectReference]):
        self.reorder_array(self.train_station_order_array, new_order)

    def reorder_trains(self, new_order: list[SaveDataObjectReference]):
        self.reorder_array(self.train_order_array, new_order)

    def reorder_array(self, original_array: tuple[int, int, list[SaveDataObjectReference]], new_order: list[SaveDataObjectReference]):
        body_array_start, body_array_end, original_order = original_array
        if len(new_order) != len(original_order):
            raise ValueError("Array item count does not match the original's")

        new_array_data = b"".join(array_item.to_bytes() for array_item in new_order)

        if len(new_array_data) != body_array_end - body_array_start:
            raise ValueError("Array byte length does not match the original's")

        self.body = self.body[:body_array_start] + new_array_data + self.body[body_array_end:]


def read_new_order(filename: str, original_entries: list[tuple[SaveDataObjectReference, SaveDataString]], entry_type: str) -> list[SaveDataObjectReference]:
    with open(filename, "r", encoding="utf-8") as f:
        new_entry_name_order = [line.rstrip("\n") for line in f]

    original_entries_copy = original_entries.copy()
    new_array_object_refs = []

    for line_number, new_name in enumerate(new_entry_name_order):
        line_number += 1
        entry_found = False
        for orig_i, orig_entry in enumerate(original_entries_copy):
            orig_object_ref, orig_name = orig_entry
            if new_name == orig_name.decode():
                new_array_object_refs.append(orig_object_ref)
                entry_found = True
                del original_entries_copy[orig_i]
                break

        if not entry_found:
            print(f"\nError in {filename!r} on line {line_number}: {entry_type} {new_name!r} does not exist. (Did you spell it correctly? Or is it a duplicate?)")
            print(f"Correct {filename!r} and try again.")
            wait_for_enter()
            return read_new_order(filename, original_entries, entry_type)

    if len(new_entry_name_order) != len(original_entries):
        print(f"\nError! One or more {entry_type}s are missing from the text file: ")
        missing_names = []
        for _, orig_name in original_entries:
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
        for station_entry, station_name in station_entries:
            f.write(station_name.decode())
            f.write("\n")

    with open("train list.txt", "w", encoding="utf-8") as f:
        for train_entry, train_name in train_entries:
            f.write(train_name.decode())
            f.write("\n")

    path, filename = os.path.split(input_save_path)
    name, extension = os.path.splitext(filename)
    output_save_filename = f"{name}_REORDERED{extension}"

    print()
    print("Created 'station list.txt' and 'train list.txt'.")
    print("Rearrange the order of the names to your liking in a text editor, and save.")
    print("NOTE: Exactly one name per line, names can not be changed; only reordered.")
    print()
    print("After saving, press Enter in this window to read the new orders from the text files.")
    print("This will generate a new edited save file.")
    print(f"Output save file will be called {output_save_filename!r}.")
    print()
    wait_for_enter()

    new_station_array_entries = read_new_order("station list.txt", station_entries, "Station")
    new_train_array_entries = read_new_order("train list.txt", train_entries, "Train")

    parser.reorder_train_stations(new_station_array_entries)
    parser.reorder_trains(new_train_array_entries)

    print()
    parser.write_file(output_save_filename)
    print("Success!")
    print(f"Save file saved as {output_save_filename!r}.\n")
    input("Press Enter to close...")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        input("Press Enter to close...")
