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

import serial
from PySide6.QtGui import QPixmap

from ..utils import Version
from .Convert import get_rgb16_pixmap
from .NanoVNA import NanoVNA
from .Serial import Interface

logger = logging.getLogger(__name__)


class NanoVNA_F_V2(NanoVNA):
    name = "NanoVNA-F_V2"
    screenwidth = 800
    screenheight = 480
    sweep_min_freq_hz = 50_000
    sweep_max_freq_hz = 3_000_000_000
    safe_logarithmic_scan_points = 11
    safe_logarithmic_low_step_hz = 1_000
    safe_logarithmic_high_step_hz = 10_000
    synthesizer_switch_hz = 140_000_000
    valid_datapoints: tuple[int, ...] = (101, 11, 51, 201, 301)
    sweep_points_min = 11
    sweep_points_max = 301

    def __init__(self, iface: Interface):
        super().__init__(iface)
        self.sweep_max_freq_hz = 3_000_000_000
        self.version = self.read_firmware_version()
        # max datapoints reach up to 301 since version 0.5.0
        if self.version >= Version.parse("0.5.0"):
            pass
        # max datapoints reach up to 201 since version 0.2.0
        elif self.version >= Version.parse("0.2.0"):
            self.valid_datapoints = (101, 11, 51, 201)
            self.sweep_points_max = 201
        # max datapoints reach up to 101 before version 0.2.0
        else:
            self.valid_datapoints = (101, 11, 51)
            self.sweep_points_max = 101

    def read_centered_single_frequency(
        self, frequency: int
    ) -> tuple[list[int], list[complex], list[complex]]:
        """Measure one frequency at the center of a valid 11-point scan."""
        points = self.safe_logarithmic_scan_points
        step = (
            self.safe_logarithmic_low_step_hz
            if frequency < self.synthesizer_switch_hz
            else self.safe_logarithmic_high_step_hz
        )
        available_below = (
            frequency - int(self.sweep_min_freq_hz)
        ) // step
        available_above = (
            int(self.sweep_max_freq_hz) - frequency
        ) // step
        first_valid_index = max(0, points - 1 - available_above)
        last_valid_index = min(points - 1, available_below)
        if first_valid_index > last_valid_index:
            raise ValueError(
                f"Cannot place {frequency} Hz in a supported {points}-point "
                "scan"
            )

        target_index = min(
            max(points // 2, first_valid_index), last_valid_index
        )
        start = frequency - target_index * step
        stop = start + (points - 1) * step
        command = f"scan {start} {stop} {points} 7"
        lines = list(self.exec_command(command))
        if len(lines) != points:
            preview = "; ".join(lines[:3])
            raise ValueError(
                f"{command}: expected {points} rows, got {len(lines)}"
                f" ({preview})"
            )

        frequencies: list[int] = []
        values11: list[complex] = []
        values21: list[complex] = []
        for line in lines:
            fields = line.split()
            if len(fields) != 5:
                raise ValueError(
                    f"{command}: expected frequency, S11 and S21; got {line!r}"
                )
            try:
                measured_frequency = int(float(fields[0]))
                s11 = complex(float(fields[1]), float(fields[2]))
                s21 = complex(float(fields[3]), float(fields[4]))
            except ValueError as exc:
                raise ValueError(
                    f"{command}: invalid measurement row {line!r}"
                ) from exc
            frequencies.append(measured_frequency)
            values11.append(s11)
            values21.append(s21)

        expected_frequencies = [start + index * step for index in range(points)]
        if frequencies != expected_frequencies:
            raise ValueError(
                f"{command}: device returned unexpected frequencies; "
                f"expected {expected_frequencies[:3]}..., got "
                f"{frequencies[:3]}..."
            )
        if frequencies[target_index] != frequency:
            raise ValueError(
                f"{command}: target row is {frequencies[target_index]} Hz, "
                f"expected {frequency} Hz"
            )
        return (
            [frequency],
            [values11[target_index]],
            [values21[target_index]],
        )

    def getCalibration(self) -> str:
        """Return an explicit device-side calibration apply state."""
        try:
            details = " ".join(self.exec_command("cal")).strip()
        except Exception as exc:
            logger.warning("Unable to read NanoVNA-F V2 CAL status: %s", exc)
            return "Unavailable"

        if "command not recognised" in details.lower():
            return "Unsupported"

        tokens = {
            token.strip("[](),:;").lower() for token in details.split()
        }
        state = "ON" if "cal'ed" in tokens else "OFF"
        return f"{state} ({details})" if details else f"{state} (no terms)"

    def getScreenshot(self) -> QPixmap:
        logger.debug("Capturing screenshot...")
        if not self.connected():
            return QPixmap()
        try:
            logger.debug("Captured screenshot")
            return get_rgb16_pixmap(
                self._capture_data(), self.screenwidth, self.screenheight
            )
        except serial.SerialException as exc:
            logger.exception("Exception while capturing screenshot: %s", exc)
        return QPixmap()
    """
    Add missing function to fix SN display in device setting window
    Copied from _F_V3
    """

    def init_features(self) -> None:
        super().init_features()
        result = " ".join(self.exec_command("help")).lower().split()
        if "sn:" in result:
            self.features.add("SN")
            self.SN = self.getSerialNumber()


    def read_firmware_version(self) -> "Version":
        """For example, command version in NanoVNA_F_V2 and NanoVNA_F_V3
        returns as this 0.5.8
        """
        result = list(self.exec_command("version"))
        logger.debug("firmware version result:\n%s", result[0])
        return Version.parse(result[0])

    def get_features(self):
        super().get_features()
        result = " ".join(self.exec_command("help")).split()
        if "sn:" or "SN:" in result:
            self.features.add("SN")
            self.SN = self.getSerialNumber()
        # The return value initializes the valid sweep-point combobox.
        return self.features

    def getSerialNumber(self) -> str:
        return (
            " ".join(list(self.exec_command("SN")))
            if "SN:" in " ".join(self.exec_command("help")).split()
            else " ".join(list(self.exec_command("sn")))
        )
