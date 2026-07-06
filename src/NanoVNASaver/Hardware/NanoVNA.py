#  NanoVNASaver
#
#  A python program to view and export Touchstone data from a NanoVNA
#  Copyright (C) 2019, 2020  Rune B. Broberg
#  Copyright (C) 2020,2021 NanoVNA-Saver Authors
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
import logging
from time import sleep

import serial
from PySide6.QtGui import QPixmap

from ..utils import Version
from .Convert import get_argb32_pixmap
from .Serial import Interface, drain_serial
from .VNA import VNA

logger = logging.getLogger(__name__)

HARD_COMM_FAILURE_TEXT = (
    "Input/output error",
    "Write timeout",
    "device disconnected",
    "multiple access on port",
    "Could not configure port",
    "write failed",
)
SCAN_MASK_DISABLED_FOR_SESSION = False


def _is_hard_comm_failure(exc: Exception) -> bool:
    return any(text in str(exc) for text in HARD_COMM_FAILURE_TEXT)


def _disable_scan_mask_for_session(reason: Exception) -> None:
    global SCAN_MASK_DISABLED_FOR_SESSION  # pylint: disable=global-statement
    if SCAN_MASK_DISABLED_FOR_SESSION:
        return
    SCAN_MASK_DISABLED_FOR_SESSION = True
    logger.warning(
        "Disabling scan mask command for this session after communication "
        "failure: %s",
        reason,
    )


class NanoVNA(VNA):
    name = "NanoVNA"
    screenwidth = 320
    screenheight = 240

    def __init__(self, iface: Interface):
        super().__init__(iface)
        self.sweep_method = "sweep"
        self.init_features()
        logger.debug("Setting initial start,stop")
        self.start, self.stop = self._get_running_frequencies()
        self.sweep_max_freq_hz = 300e6
        self._sweepdata: list[tuple[complex, complex]] = []

    def _get_running_frequencies(self):
        logger.debug("Reading values: frequencies")
        try:
            frequencies = super().readValues("frequencies")
            return int(frequencies[0].real), int(frequencies[-1].real)
        except serial.SerialException as e:
            logger.debug("%s reading frequencies", e)
            logger.info("falling back to generic")

        return VNA._get_running_frequencies(self)

    def _recover_after_comm_failure(self) -> None:
        ser = getattr(self, "serial", None)
        if ser is None:
            return

        try:
            if not ser.is_open:
                return
        except Exception:
            return

        try:
            ser.reset_input_buffer()
        except Exception as e:
            logger.debug("reset_input_buffer failed: %s", e)

        try:
            ser.reset_output_buffer()
        except Exception as e:
            logger.debug("reset_output_buffer failed: %s", e)

        try:
            drain_serial(ser)
        except Exception as e:
            logger.debug("drain_serial after failure failed: %s", e)

        sleep(0.2)

    def _capture_data(self) -> bytes:
        timeout = self.serial.timeout
        with self.serial.lock:
            drain_serial(self.serial)
            self.serial.write("capture\r".encode("ascii"))
            self.serial.readline()
            self.serial.timeout = 10
            image_data = self.serial.read(
                self.screenwidth * self.screenheight * 2
            )
            self.serial.timeout = timeout
        self.serial.timeout = timeout
        return image_data

    def getScreenshot(self) -> QPixmap:
        logger.debug("Capturing screenshot...")
        if not self.connected():
            return QPixmap()
        try:
            logger.debug("Captured screenshot")
            return get_argb32_pixmap(
                self._capture_data(), self.screenwidth, self.screenheight
            )
        except serial.SerialException as exc:
            logger.exception("Exception while capturing screenshot: %s", exc)
        return QPixmap()

    def resetSweep(self, start: int, stop: int):
        try:
            list(self.exec_command(f"sweep {start} {stop} {self.datapoints}"))
            list(self.exec_command("resume"))
        except Exception as e:
            logger.debug("resetSweep(%s, %s) failed: %s", start, stop, e)
            self._recover_after_comm_failure()
            if _is_hard_comm_failure(e):
                raise
        # list(self.exec_command(f"sweep {start} {stop} {self.datapoints}"))
        # list(self.exec_command("resume"))

    def setSweep(self, start, stop):
        self.start = start
        self.stop = stop
        try:
            if self.sweep_method == "sweep":
                list(self.exec_command(f"sweep {start} {stop} {self.datapoints}"))
            elif self.sweep_method == "scan":
                list(self.exec_command(f"scan {start} {stop} {self.datapoints}"))
        except Exception as e:
            logger.debug("setSweep(%s, %s) failed: %s", start, stop, e)
            self._recover_after_comm_failure()
            if _is_hard_comm_failure(e):
                raise
        # if self.sweep_method == "sweep":
        #     list(self.exec_command(f"sweep {start} {stop} {self.datapoints}"))
        # elif self.sweep_method == "scan":
        #     list(self.exec_command(f"scan {start} {stop} {self.datapoints}"))

    def init_features(self) -> None:
        super().init_features()
        if self.version >= Version.parse("0.7.1"):
            if SCAN_MASK_DISABLED_FOR_SESSION:
                logger.info(
                    "Using scan command because scan mask was disabled "
                    "after a communication failure."
                )
                self.features.add("Scan command")
                self.sweep_method = "scan"
            else:
                logger.debug("Using scan mask command.")
                self.features.add("Scan mask command")
                self.sweep_method = "scan_mask"
        elif self.version >= Version.parse("0.2.0"):
            logger.debug("Using new scan command.")
            self.features.add("Scan command")
            self.sweep_method = "scan"
                
    def read_frequencies(self) -> list[int]:
        if self.sweep_method != "scan_mask":
            try:
                return super().read_frequencies()
            except Exception as e:
                logger.debug("read_frequencies() failed in base implementation: %s", e)
                self._recover_after_comm_failure()
                return self._fallback_frequencies()

        return self._fallback_frequencies()


    def _fallback_frequencies(self) -> list[int]:
        if self.datapoints <= 1:
            return [int(self.start)]

        step = (self.stop - self.start) / (self.datapoints - 1)
        return [int(self.start + i * step) for i in range(self.datapoints)]


    def readValues(self, value) -> list[complex]:
        if self.sweep_method != "scan_mask":
            try:
                return super().readValues(value)
            except Exception as e:
                logger.debug("readValues(%s) failed in base implementation: %s", value, e)
                self._recover_after_comm_failure()
                return []

        logger.debug("readValue with scan mask (%s)", value)

        # data 1 は、直前の data 0 scan で保存した値を返すだけ
        if value == "data 1":
            return [x[1] for x in self._sweepdata]

        # data 0 以外も data 0 として扱う
        old_sweepdata = list(self._sweepdata)

        try:
            lines = list(
                self.exec_command(
                    f"scan {self.start} {self.stop} {self.datapoints} 0b110"
                )
            )
        except Exception as e:
            logger.debug(
                "readValues(data 0) scan failed (%s-%s, %s pts): %s",
                self.start, self.stop, self.datapoints, e
            )
            self._recover_after_comm_failure()
            if _is_hard_comm_failure(e):
                _disable_scan_mask_for_session(e)
                self.features.discard("Scan mask command")
                self.features.add("Scan command")
                self.sweep_method = "scan"
                self._sweepdata = []
                raise
            if "command timeout" in str(e):
                _disable_scan_mask_for_session(e)
                self.features.discard("Scan mask command")
                self.features.add("Scan command")
                self.sweep_method = "scan"
                self._sweepdata = []
                raise

            # 失敗時は前回データを返す
            if old_sweepdata:
                logger.debug("readValues(data 0): using previous sweepdata")
                self._sweepdata = old_sweepdata
                return [x[0] for x in self._sweepdata]

            return []

        if len(lines) != self.datapoints:
            logger.debug(
                "readValues(data 0): got %s lines, expected %s",
                len(lines), self.datapoints
            )
            self._recover_after_comm_failure()

            # 点数不足でも前回データを返す
            if old_sweepdata:
                logger.debug("readValues(data 0): using previous sweepdata")
                self._sweepdata = old_sweepdata
                return [x[0] for x in self._sweepdata]

            return []

        new_sweepdata = []
        try:
            for line in lines:
                d = list(map(float, line.split()))
                if len(d) < 4:
                    raise ValueError(f"not enough columns in line: {line!r}")
                new_sweepdata.append(
                    (complex(d[0], d[1]), complex(d[2], d[3]))
                )
        except Exception as e:
            logger.debug("readValues(data 0): parse failed: %s", e)
            self._recover_after_comm_failure()

            # parse失敗でも前回データを返す
            if old_sweepdata:
                logger.debug("readValues(data 0): using previous sweepdata")
                self._sweepdata = old_sweepdata
                return [x[0] for x in self._sweepdata]

            return []

        self._sweepdata = new_sweepdata
        return [x[0] for x in self._sweepdata]
