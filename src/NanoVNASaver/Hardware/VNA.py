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
import os
import sys
from contextlib import suppress
from pathlib import Path
from time import monotonic, sleep
from typing import Iterator

from PySide6 import QtGui

from ..utils import Version
from .Serial import Interface, drain_serial

logger = logging.getLogger(__name__)

DISLORD_BW = {
    10: 363,
    33: 117,
    50: 78,
    100: 39,
    200: 19,
    250: 15,
    333: 11,
    500: 7,
    1000: 3,
    2000: 1,
    4000: 0,
}
WAIT = 0.05
MIN_COMMAND_WAIT = 0.20
COMMAND_POLL_WAIT = 0.40
COMMAND_TIMEOUT = 35.0
RECONNECT_SETTLE_TIME = 5.0
USBDEVFS_RESET = (ord("U") << 8) | 20


def _usb_device_node_for_serial(port: str | None) -> Path | None:
    """Resolve a tty device to its corresponding usbfs device node."""
    if not port:
        return None

    tty_name = Path(port).resolve().name
    sysfs_device = Path("/sys/class/tty") / tty_name / "device"
    if not sysfs_device.exists():
        return None

    device = sysfs_device.resolve()
    for parent in (device, *device.parents):
        busnum_path = parent / "busnum"
        devnum_path = parent / "devnum"
        if not busnum_path.exists() or not devnum_path.exists():
            continue
        busnum = int(busnum_path.read_text(encoding="ascii").strip())
        devnum = int(devnum_path.read_text(encoding="ascii").strip())
        return Path(f"/dev/bus/usb/{busnum:03d}/{devnum:03d}")
    return None


def _reset_usb_device_for_serial(port: str | None) -> bool:
    """Best-effort Linux USB-level reset for a serial device."""
    if not sys.platform.startswith("linux"):
        logger.info("USB-level reset is unavailable on %s", sys.platform)
        return False
    usb_device = _usb_device_node_for_serial(port)
    if usb_device is None:
        logger.warning("Could not resolve USB device for serial port %s", port)
        return False

    try:
        import fcntl  # Linux-only; usbfs is unavailable on other platforms.

        fd = os.open(usb_device, os.O_WRONLY | os.O_CLOEXEC)
        try:
            fcntl.ioctl(fd, USBDEVFS_RESET, 0)
        finally:
            os.close(fd)
    except OSError as exc:
        logger.warning("USB reset failed for %s: %s", usb_device, exc)
        return False

    logger.info("USB reset completed for %s", usb_device)
    return True


def _max_retries(bandwidth: int, datapoints: int) -> int:
    return round(
        20
        + 20 * (datapoints / 101)
        + (1000 / bandwidth) ** 1.30 * (datapoints / 101)
    )


def _command_wait(command: str, bandwidth: int, datapoints: int) -> float:
    if not command.startswith(("scan ", "sweep ")):
        return WAIT
    point_time = max(1 / max(bandwidth, 1), WAIT)
    return max(MIN_COMMAND_WAIT, min(2.0, point_time * datapoints / 4))

class VNACommandRetryExceeded(Exception):
    """一時的な通信失敗。アプリ全体は止めない。"""
    pass

class VNA:
    name = "VNA"
    valid_datapoints: tuple[int, ...] = (101, 51, 11)
    wait = 0.05
    SN = "NOT SUPPORTED"
    hardware_revision: str | Version = "NOT SUPPORTED"
    sweep_points_max = 101
    sweep_points_min = 11

    # Must be initilized in child classes
    sweep_min_freq_hz = 1
    sweep_max_freq_hz = 0.0

    def __init__(self, iface: Interface):
        self.serial = iface
        self.version = Version.parse("0.0.0")
        self.features: set[str] = set()
        self.validateInput = False
        # self.datapoints = self.valid_datapoints[0]
        self.datapoints = 51
        self.bandwidth = 10
        self.bw_method = "ttrftech"
        # [((min_freq, max_freq), [description])]. Order by increasing
        # frequency. Put default output power first.
        self.txPowerRanges: list[tuple[tuple[float, float], list[str]]] = []
        if self.connected():
            self.version = self.read_fw_version()
            self.init_features()
            logger.debug("Features: %s", self.features)
            #  cannot read current bandwidth, so set to highest
            #  to get initial sweep fast
            if "Bandwidth" in self.features:
                # self.set_bandwidth(self.get_bandwidths()[-1])
                self.set_bandwidth(10)

    def connect(self):
        logger.info("connect %s", self.serial)
        with self.serial.lock:
            self.serial.open()

    def disconnect(self):
        logger.info("disconnect %s", self.serial)
        with self.serial.lock:
            with suppress(Exception):
                self.serial.cancel_read()
            with suppress(Exception):
                self.serial.cancel_write()
            self.serial.close()

    def reconnect(self):
        logger.info("reconnect %s", self.serial)
        with self.serial.lock:
            with suppress(Exception):
                self.serial.cancel_read()
            with suppress(Exception):
                self.serial.cancel_write()
            with suppress(Exception):
                if self.serial.is_open:
                    self.serial.close()

            sleep(RECONNECT_SETTLE_TIME)
            self.serial.open()

            # Some NanoVNA CDC endpoints keep the old command state until the
            # host drops and restores modem control lines after a long idle.
            with suppress(Exception):
                self.serial.dtr = False
                self.serial.rts = False
            sleep(0.1)
            with suppress(Exception):
                self.serial.dtr = True
                self.serial.rts = True

            sleep(RECONNECT_SETTLE_TIME)
            with suppress(Exception):
                self.serial.reset_input_buffer()
            with suppress(Exception):
                self.serial.reset_output_buffer()

    # def exec_command(self, command: str, wait: float = WAIT) -> Iterator[str]:
    #     logger.debug("exec_command(%s)", command)
    #     with self.serial.lock:
    #         drain_serial(self.serial)
    #         self.serial.write(f"{command}\r".encode("ascii"))
    #         sleep(wait)
    #         retries = 0
    #         max_retries = _max_retries(self.bandwidth, self.datapoints)
    #         logger.debug("Max retries: %s", max_retries)
    #         while True:
    #             line = self.serial.readline().decode("ascii").strip()
    #             if not line:
    #                 retries += 1
    #                 if retries > max_retries:
    #                     raise IOError("too many retries")
    #                 sleep(wait)
    #                 continue
    #             if line == command:  # suppress echo
    #                 continue
    #             if line.startswith("ch>"):
    #                 logger.debug("Needed retries: %s", retries)
    #                 break
    #             yield line
    def exec_command(self, command: str, wait: float = WAIT) -> Iterator[str]:
        logger.debug("exec_command(%s)", command)

        ser = self.serial
        if ser is None:
            raise VNACommandRetryExceeded(f"{command}: serial is None")

        try:
            if not ser.is_open:
                raise VNACommandRetryExceeded(f"{command}: serial is not open")
        except Exception as e:
            raise VNACommandRetryExceeded(f"{command}: failed to check serial state: {e}") from e

        with ser.lock:
            old_timeout = getattr(ser, "timeout", None)
            old_write_timeout = getattr(ser, "write_timeout", None)
            try:
                drain_serial(ser)
            except Exception as e:
                raise VNACommandRetryExceeded(f"{command}: drain_serial failed: {e}") from e

            try:
                ser.write_timeout = 5
                ser.write(f"{command}\r".encode("ascii"))
            except Exception as e:
                with suppress(Exception):
                    ser.timeout = old_timeout
                with suppress(Exception):
                    ser.write_timeout = old_write_timeout
                raise VNACommandRetryExceeded(f"{command}: write failed: {e}") from e

            wait = max(wait, _command_wait(command, self.bandwidth, self.datapoints))
            sleep(wait)
            poll_wait = COMMAND_POLL_WAIT
            deadline = monotonic() + COMMAND_TIMEOUT
            ser.timeout = max(0.05, poll_wait)

            retries = 0
            max_retries = _max_retries(self.bandwidth, self.datapoints)
            logger.debug("Max retries: %s", max_retries)
            if command.startswith("scan "):
                try:
                    parts = command.split()
                    start = int(parts[1])
                    if start <= 1000000:
                        wait = max(wait, 0.7)
                        max_retries += 120
                except (IndexError, ValueError):
                    pass

            logger.debug("Max retries: %s", max_retries)
            try:
                while True:
                    try:
                        raw = ser.readline()
                    except Exception as e:
                        raise VNACommandRetryExceeded(f"{command}: readline failed: {e}") from e

                    try:
                        line = raw.decode("ascii", errors="ignore").strip()
                    except Exception as e:
                        raise VNACommandRetryExceeded(f"{command}: decode failed: {e}") from e

                    if not line:
                        retries += 1
                        if retries > max_retries or monotonic() > deadline:
                            logger.debug("exec_command(%s): too many retries", command)
                            raise VNACommandRetryExceeded(f"{command}: command timeout")
                        sleep(poll_wait)
                        continue

                    if line == command:  # suppress echo
                        continue

                    if line.startswith("ch>"):
                        logger.debug("Needed retries: %s", retries)
                        return

                    yield line
            finally:
                with suppress(Exception):
                    ser.timeout = old_timeout
                with suppress(Exception):
                    ser.write_timeout = old_write_timeout
        # logger.debug("exec_command(%s)", command)
        # try:
        #     with self.serial.lock:
        #         try:
        #             drain_serial(self.serial)
        #             self.serial.write(f"{command}\r".encode("ascii"))
        #         except Exception as e:
        #             logger.warning("exec_command(%s): serial write/drain failed: %s", command, e)
        #             return

        #         sleep(wait)
        #         retries = 0
        #         max_retries = _max_retries(self.bandwidth, self.datapoints)
        #         logger.debug("Max retries: %s", max_retries)

        #         while True:
        #             try:
        #                 line = self.serial.readline().decode("ascii", errors="ignore").strip()
        #             except Exception as e:
        #                 logger.warning("exec_command(%s): readline failed: %s", command, e)
        #                 return

        #             if not line:
        #                 retries += 1
        #                 if retries > max_retries:
        #                     logger.warning("exec_command(%s): too many retries", command)
        #                     return
        #                 sleep(wait)
        #                 continue

        #             if line == command:
        #                 continue
        #             if line.startswith("ch>"):
        #                 logger.debug("Needed retries: %s", retries)
        #                 return
        #             yield line
        # except Exception as e:
        #     logger.warning("exec_command(%s): unexpected error: %s", command, e)
        #     return
    
    def init_features(self) -> None:
        result = " ".join(self.exec_command("help")).split()
        logger.debug("result:\n%s", result)
        if "capture" in result:
            self.features.add("Screenshots")
        if "sn:" in result:
            self.features.add("SN")
            self.SN = self.getSerialNumber()
        if "bandwidth" in result:
            self.features.add("Bandwidth")
            bwresult = " ".join(list(self.exec_command("bandwidth")))
            if "Hz)" in bwresult:
                self.bw_method = "dislord"
        if len(self.valid_datapoints) > 1:
            self.features.add("Customizable data points")

    def get_bandwidths(self) -> list[int]:
        logger.debug("get bandwidths")
        if self.bw_method == "dislord":
            return list(DISLORD_BW.keys())
        result = " ".join(list(self.exec_command("bandwidth")))
        try:
            result = result.split(" {")[1].strip("}")
            return sorted([int(i) for i in result.split("|")])
        except IndexError:
            return [
                1000,
            ]

    def set_bandwidth(self, bandwidth: int):
        bw_val = (
            DISLORD_BW[bandwidth] if self.bw_method == "dislord" else bandwidth
        )
        result = " ".join(self.exec_command(f"bandwidth {bw_val}"))
        if self.bw_method == "ttrftech" and result:
            raise IOError(f"set_bandwith({bandwidth}: {result}")
        self.bandwidth = bandwidth

    def read_frequencies(self) -> list[int]:
        return [int(f.real) for f in self.readValues("frequencies")]

    def resetSweep(self, start: int, stop: int):
        pass

    def _get_running_frequencies(self) -> tuple[int, int]:
        """
        If possible, read frequencies already running
        if not return default values
        Overwrite in specific HW
        """
        return 27000000, 30000000

    def connected(self) -> bool:
        return self.serial.is_open

    def get_features(self) -> set[str]:
        return self.features

    def getCalibration(self) -> str:
        # return " ".join(list(self.exec_command("cal")))
        try:
            resp = list(self.exec_command("cal"))
            return " ".join(resp) if resp else "N/A"
        except Exception as e:
            logger.warning("getCalibration failed: %s", e)
            return "N/A"

    def getScreenshot(self) -> QtGui.QPixmap:
        return QtGui.QPixmap()

    def flushSerialBuffers(self):
        if not self.connected():
            return
        with self.serial.lock:
            self.serial.write("\r\n\r\n".encode("ascii"))
            sleep(0.1)
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()
            sleep(0.1)

    def power_reset(self):
        """Reset the NanoVNA MCU without waiting for a prompt."""
        if not self.connected():
            raise IOError("VNA is not connected")
        with self.serial.lock:
            drain_serial(self.serial)
            self.serial.write("reset\r".encode("ascii"))
            self.serial.flush()

    def readFirmware(self) -> str:
        result = "\n".join(list(self.exec_command("info")))
        logger.debug("result:\n%s", result)
        return result

    def readValues(self, value) -> list[complex]:
        logger.debug("VNA reading %s", value)
        result = [
            complex(*map(float, s.split())) for s in self.exec_command(value)
        ]
        logger.debug("VNA done reading %s (%d values)", value, len(result))
        return result

    def read_fw_version(self) -> Version:
        result = list(self.exec_command("version"))
        logger.debug("Firmware Version:\n%s", result)
        return Version.parse(result[0])

    def setSweep(self, start, stop):
        list(self.exec_command(f"sweep {start} {stop} {self.datapoints}"))

    def setTXPower(self, freq_range, power_desc):
        raise NotImplementedError()

    def getSerialNumber(self) -> str:
        return " ".join(list(self.exec_command("sn")))
