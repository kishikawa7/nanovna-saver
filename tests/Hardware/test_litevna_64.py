from threading import RLock
from unittest.mock import Mock, patch

from NanoVNASaver.Hardware.LiteVNA64 import LiteVNA64, ScreenshotData
from NanoVNASaver.Hardware.NanoVNA_V2 import NanoVNA_V2

VALID_HEADER = b"\xe0\x01@\x01\x10"


def test_measurements_do_not_exit_usb_mode() -> None:
    device = object.__new__(LiteVNA64)
    with (
        patch.object(NanoVNA_V2, "readValues", return_value=[1 + 0j]) as read,
        patch.object(device, "_exit_usb_mode") as exit_usb,
    ):
        assert device.readValues("data 0") == [1 + 0j]
        assert device.readValues("data 1") == [1 + 0j]

    assert read.call_count == 2
    exit_usb.assert_not_called()


def test_scheduled_refresh_exits_usb_mode() -> None:
    device = object.__new__(LiteVNA64)
    serial = Mock(is_open=True)
    serial.lock = RLock()
    device.serial = serial

    with patch("NanoVNASaver.Hardware.LiteVNA64.sleep"):
        device.power_reset()

    serial.write.assert_called_once_with(b"\x20\x26\x02")


def test_if_average_is_not_read_on_connect() -> None:
    serial = Mock(is_open=True)
    serial.lock = RLock()

    def initialize_base(device, iface):
        device.serial = iface

    with (
        patch.object(NanoVNA_V2, "__init__", initialize_base),
        patch("NanoVNASaver.Hardware.LiteVNA64.sleep"),
    ):
        device = LiteVNA64(serial)

    assert device.datapoints == 201
    serial.write.assert_not_called()
    serial.read.assert_not_called()


def test_litevna_fifo_timeout_does_not_depend_on_if_readback() -> None:
    device = object.__new__(LiteVNA64)
    assert device._readfifo_timeout(1) >= 4.0
    assert device._readfifo_timeout(101) > device._readfifo_timeout(1)


class TestScreenshotData:
    @staticmethod
    def test_from_header() -> None:
        result = ScreenshotData.from_header(VALID_HEADER)

        assert result.width == 480
        assert result.height == 320
        assert result.pixel_size == 16
        assert len(result.data) == 0

    @staticmethod
    def test_data_size() -> None:
        assert ScreenshotData(0, 0, 0).data_size() == 0
        assert ScreenshotData(480, 320, 16).data_size() == 307200

    @staticmethod
    def test_repr() -> None:
        assert f"{ScreenshotData(0, 0, 0)}" == "0x0 0bits (0 Bytes)"
        assert (
            f"{ScreenshotData(480, 320, 16)}" == "480x320 16bits (307200 Bytes)"
        )

    @staticmethod
    def test_rgb565_to_888() -> None:
        assert ScreenshotData.rgb565_to_888(0x0000) == (0x00, 0x00, 0x00)
        assert ScreenshotData.rgb565_to_888(0xFFE0) == (0xFF, 0xFF, 0x00)
        assert ScreenshotData.rgb565_to_888(0xFFFF) == (0xFF, 0xFF, 0xFF)

    @staticmethod
    def test_get_rgb888_data() -> None:
        img = ScreenshotData(0, 0, 0)
        img.data = b"\x00\x00\xff\xe0\xff\xff"

        result = img.get_rgb888_data()

        assert len(result) == 3 * 3
        assert result == b"\x00\x00\x00\xff\xff\x00\xff\xff\xff"
