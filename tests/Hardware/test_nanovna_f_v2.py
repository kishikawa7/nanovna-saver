from types import SimpleNamespace

import pytest

from NanoVNASaver.Hardware.NanoVNA_F_V2 import NanoVNA_F_V2
from NanoVNASaver.SweepWorker import SweepWorker


def test_single_frequency_uses_center_of_supported_scan() -> None:
    device = NanoVNA_F_V2.__new__(NanoVNA_F_V2)
    commands: list[str] = []

    def exec_command(command: str):
        commands.append(command)
        return iter(
            f"{99_995_000 + i * 1_000} {i} {-i} {i + 1} {-i - 1}"
            for i in range(11)
        )

    device.exec_command = exec_command

    frequencies, values11, values21 = device.read_centered_single_frequency(
        100_000_000
    )

    assert commands == ["scan 99995000 100005000 11 7"]
    assert frequencies == [100_000_000]
    assert values11 == [complex(5, -5)]
    assert values21 == [complex(6, -6)]


def test_centered_single_frequency_rejects_frequency_drift() -> None:
    device = NanoVNA_F_V2.__new__(NanoVNA_F_V2)

    def exec_command(_command: str):
        frequencies = [99_995_000 + i * 1_000 for i in range(11)]
        frequencies[-1] += 1
        return iter(f"{frequency} 1 2 3 4" for frequency in frequencies)

    device.exec_command = exec_command

    with pytest.raises(ValueError, match="unexpected frequencies"):
        device.read_centered_single_frequency(100_000_000)


@pytest.mark.parametrize(
    ("response", "expected"),
    (
        ([], "OFF (no terms)"),
        (["load open short thru"], "OFF (load open short thru)"),
        (["load open short thru cal'ed"], "ON (load open short thru cal'ed)"),
    ),
)
def test_device_calibration_status_is_explicit(
    response: list[str], expected: str
) -> None:
    device = NanoVNA_F_V2.__new__(NanoVNA_F_V2)
    device.exec_command = lambda command: iter(response)

    assert device.getCalibration() == expected


@pytest.mark.parametrize(
    ("frequency", "expected_command", "expected_index"),
    (
        (50_000, "scan 50000 60000 11 7", 0),
        (200_000_000, "scan 199950000 200050000 11 7", 5),
        (3_000_000_000, "scan 2999900000 3000000000 11 7", 10),
    ),
)
def test_centered_single_frequency_keeps_scan_inside_device_range(
    frequency: int, expected_command: str, expected_index: int
) -> None:
    device = NanoVNA_F_V2.__new__(NanoVNA_F_V2)
    commands: list[str] = []

    def exec_command(command: str):
        commands.append(command)
        _, start, stop, points, _ = command.split()
        start_hz = int(start)
        point_count = int(points)
        step_hz = (int(stop) - start_hz) // (point_count - 1)
        return iter(
            f"{start_hz + i * step_hz} {i} 0 {-i} 0"
            for i in range(point_count)
        )

    device.exec_command = exec_command

    frequencies, values11, values21 = device.read_centered_single_frequency(
        frequency
    )

    assert commands == [expected_command]
    assert frequencies == [frequency]
    assert values11 == [complex(expected_index, 0)]
    assert values21 == [complex(-expected_index, 0)]


class _FakeFv2:
    datapoints = 51
    safe_logarithmic_scan_points = 11

    def __init__(self) -> None:
        self.calls: list[int] = []

    def read_centered_single_frequency(
        self, frequency: int
    ) -> tuple[list[int], list[complex], list[complex]]:
        self.calls.append(frequency)
        return [frequency], [complex(5, -5)], [complex(-5, 5)]


def test_safe_logarithmic_scan_uses_centered_sample() -> None:
    vna = _FakeFv2()
    worker = SweepWorker(SimpleNamespace(vna=vna))
    worker.sweep.set_f_v2_safe_logarithmic(True)

    frequencies, values11, values21 = worker.read_averaged_frequencies(
        [100_000_000, 200_000_000]
    )

    assert frequencies == [100_000_000, 200_000_000]
    assert values11 == [complex(5, -5)] * 2
    assert values21 == [complex(-5, 5)] * 2
    assert vna.calls == [100_000_000, 200_000_000]
    assert vna.datapoints == 51
