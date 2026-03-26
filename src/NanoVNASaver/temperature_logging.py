import csv
import os
import re
from datetime import datetime

from .pico_temp_client import PicoTempClient


class TemperatureLoggingManager:
    def __init__(self, pico_port: str, csv_path: str = ""):
        self.pico_port = pico_port
        self.csv_path = csv_path
        self.client = None
        self.enabled = True

    def connect(self) -> bool:
        if self.client is not None and self.client.is_open():
            return True

        try:
            self.client = PicoTempClient(self.pico_port)
            self.client.open()
            return True
        except Exception:
            self.client = None
            return False

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def extract_index_from_filename(self, file_path: str) -> int:
        base = os.path.basename(file_path)
        nums = re.findall(r"\d+", base)
        if not nums:
            raise ValueError(f"No index found in filename: {base}")
        return int(nums[-1])

    def ensure_csv_header(self):
        new_file = not os.path.exists(self.csv_path)

        if new_file:
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "index",
                    "file_name",
                    "timestamp",
                    "raw",
                    "resistance_ohm",
                    "temp_c",
                    "fault",
                ])

    def log_raw_for_file(self, saved_file_path: str):
        if not self.enabled:
            return None

        if not self.csv_path:
            raise RuntimeError("CSV path is not set")

        if not self.connect():
            raise RuntimeError("Pico not connected")

        raw_data = self.client.read_raw()

        idx = self.extract_index_from_filename(saved_file_path)
        timestamp = datetime.now().isoformat(timespec="seconds")

        self.ensure_csv_header()

        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                idx,
                os.path.basename(saved_file_path),
                timestamp,
                raw_data["raw"],
                f"{raw_data['resistance_ohm']:.6f}",
                "" if raw_data["temp_c"] is None else f"{raw_data['temp_c']:.4f}",
                raw_data["fault"],
            ])

        return raw_data
# import csv
# import os
# import re
# from datetime import datetime

# from .pico_temp_client import PicoTempClient


# DEFAULT_PICO_PORT = "/dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_e664a836a33ba327-if00"


# class TemperatureLoggingManager:
#     def __init__(
#         self,
#         pico_port: str = DEFAULT_PICO_PORT,
#         csv_path: str = "temperature_log.csv",
#     ):
#         self.pico_port = pico_port
#         self.csv_path = csv_path
#         self.client = None
#         self.enabled = True

#     def connect(self) -> bool:
#         if self.client is not None and self.client.is_open():
#             return True

#         try:
#             self.client = PicoTempClient(self.pico_port)
#             self.client.open()
#             return True
#         except Exception:
#             self.disconnect()
#             return False

#     def disconnect(self) -> None:
#         if self.client is not None:
#             try:
#                 self.client.close()
#             except Exception:
#                 pass
#         self.client = None

#     def set_enabled(self, enabled: bool) -> None:
#         self.enabled = enabled
#         if not enabled:
#             self.disconnect()

#     # def ensure_csv_header(self) -> None:
#     #     new_file = not os.path.exists(self.csv_path)
#     #     directory = os.path.dirname(self.csv_path)
#     #     if directory:
#     #         os.makedirs(directory, exist_ok=True)

#     #     if new_file:
#     #         with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
#     #             writer = csv.writer(f)
#     #             writer.writerow(["index", "temperature_c", "timestamp", "file_name"])

#     def ensure_csv_header(self) -> None:
#         if not self.csv_path:
#             raise RuntimeError("CSV path is not set")

#         new_file = not os.path.exists(self.csv_path)
#         directory = os.path.dirname(self.csv_path)
#         if directory:
#             os.makedirs(directory, exist_ok=True)

#         if new_file:
#             with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
#                 writer = csv.writer(f)
#                 writer.writerow([
#                     "index",
#                     "file_name",
#                     "timestamp",
#                     "raw",
#                     "resistance_ohm",
#                     "temp_c",
#                     "fault",
#                 ])
#     @staticmethod
#     def extract_index_from_filename(file_path: str) -> int:
#         """
#         例:
#           meas_0001.s1p -> 1
#           sweep-42.csv  -> 42
#         ファイル名中の最後の整数を採用
#         """
#         base = os.path.basename(file_path)
#         stem, _ = os.path.splitext(base)
#         nums = re.findall(r"\d+", stem)
#         if not nums:
#             raise ValueError(f"Could not extract index from filename: {base}")
#         return int(nums[-1])

#     def log_temperature_for_file(self, saved_file_path: str):
#         if not self.enabled:
#             return None

#         if not self.connect():
#             raise RuntimeError("Raspi Pico is not connected")

#         temp_c = self.client.read_temperature_c()
#         if temp_c is None:
#             raise RuntimeError("Temperature read failed")

#         # idx = self.extract_index_from_filename(saved_file_path)
#         # self.ensure_csv_header()

#         # with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
#         #     writer = csv.writer(f)
#         #     writer.writerow([
#         #         idx,
#         #         f"{temp_c:.4f}",
#         #         datetime.now().isoformat(timespec="seconds"),
#         #         os.path.basename(saved_file_path),
#         #     ])

#         # return idx, temp_c
#         idx = self.extract_index_from_filename(saved_file_path)
#         raw_data = self.client.read_raw()
#         self.ensure_csv_header()
#         timestamp = datetime.now().isoformat(timespec="seconds")

#         with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
#             writer = csv.writer(f)
#             writer.writerow([
#                 idx,
#                 os.path.basename(saved_file_path),
#                 timestamp,
#                 raw_data["raw"],
#                 f"{raw_data['resistance_ohm']:.6f}",
#                 "" if raw_data["temp_c"] is None else f"{raw_data['temp_c']:.4f}",
#                 raw_data["fault"],
#             ])

#         return raw_data