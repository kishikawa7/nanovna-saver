from struct import pack
from threading import RLock
from unittest.mock import Mock, patch

import pytest

from NanoVNASaver.Hardware.NanoVNA_V2 import NanoVNA_V2


def test_incomplete_fifo_response_is_rejected() -> None:
    device = object.__new__(NanoVNA_V2)
    device.datapoints = 1
    device.features = set()
    serial = Mock()
    serial.lock = RLock()
    serial.read.return_value = b""
    device.serial = serial

    with (
        patch("NanoVNASaver.Hardware.NanoVNA_V2.sleep"),
        pytest.raises(ValueError, match="expected 32 bytes, got 0"),
    ):
        device._read_fifo_into_sweepdata(0)


def test_delayed_fifo_response_is_accepted_without_warning(caplog) -> None:
    device = object.__new__(NanoVNA_V2)
    device.datapoints = 1
    device.features = set()
    serial = Mock()
    serial.lock = RLock()
    serial.read.side_effect = [
        b"",
        pack("<iiiiiiHxxxxxx", 1, 0, 1, 0, 0, 0, 0),
    ]
    device.serial = serial

    with patch("NanoVNASaver.Hardware.NanoVNA_V2.sleep"):
        assert device._read_fifo_into_sweepdata(0)

    assert device._sweepdata == [(1 + 0j, 0j)]
    assert not caplog.records


def test_fifo_frequency_index_is_unsigned() -> None:
    device = object.__new__(NanoVNA_V2)
    device._sweepdata = [(0j, 0j)] * 65536
    data = pack("<iiiiiiHxxxxxx", 1, 0, 1, 0, 0, 0, 65535)
    seen = set()

    device._read_pointstoread(1, data, seen)

    assert seen == {65535}
    assert device._sweepdata[65535] == (1 + 0j, 0j)
