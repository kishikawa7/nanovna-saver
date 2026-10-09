from types import SimpleNamespace
from unittest.mock import Mock

from NanoVNASaver.SweepWorker import SweepWorker


def test_primed_single_point_averages_second_and_third_acquisitions() -> None:
    events = []
    vna = Mock()
    vna.read_frequencies.return_value = [1_000_000]
    vna.readValues.side_effect = lambda value: (
        events.append(("discard", value)) or [9 + 0j]
    )
    values = iter([[1 + 2j], [2 + 4j], [3 + 6j], [4 + 8j]])

    def read_data(channel):
        events.append(("keep", channel))
        return next(values)

    worker = SimpleNamespace(app=SimpleNamespace(vna=vna), read_data=read_data)

    result = SweepWorker.read_segment(
        worker, 1_000_000, 1_000_000, discard_first_sample=True
    )

    assert result == ([1_000_000], [2 + 4j], [3 + 6j])
    vna.setSweep.assert_called_once_with(1_000_000, 1_000_000)
    assert events == [
        ("discard", "data 0"),
        ("keep", "data 0"),
        ("keep", "data 1"),
        ("keep", "data 0"),
        ("keep", "data 1"),
    ]


def test_priming_recovers_from_incomplete_fifo() -> None:
    vna = Mock()
    vna.read_frequencies.return_value = [1_000_000]
    vna.readValues.side_effect = [ValueError("Incomplete FIFO"), [9 + 0j]]
    values = iter([[1 + 0j], [2 + 0j], [3 + 0j], [4 + 0j]])
    worker = SimpleNamespace(
        app=SimpleNamespace(vna=vna),
        read_data=lambda channel: next(values),
    )

    result = SweepWorker.read_segment(
        worker, 1_000_000, 1_000_000, discard_first_sample=True
    )

    assert result == ([1_000_000], [2 + 0j], [3 + 0j])
    vna.reconnect.assert_called_once_with()
    assert vna.setSweep.call_count == 2
    assert vna.readValues.call_count == 2


def test_every_litevna_single_log_point_is_primed() -> None:
    primed = []

    def read_segment(start, stop, discard_first_sample=False):
        primed.append(discard_first_sample)
        return [start], [1 + 0j], [2 + 0j]

    def read_with_retries(start, stop, reader=None):
        return reader() if reader else read_segment(start, stop)

    vna = SimpleNamespace(datapoints=201, prime_each_logarithmic_point=True)
    sweep = SimpleNamespace(
        properties=SimpleNamespace(
            logarithmic_points_per_scan=1,
            averages=(1, 0),
        )
    )
    worker = SimpleNamespace(
        app=SimpleNamespace(vna=vna),
        sweep=sweep,
        _terminate=False,
        _safe_f_v2_logarithmic_enabled=Mock(return_value=False),
        _first_decade_boundary=SweepWorker._first_decade_boundary,
        _read_segment_with_retries=read_with_retries,
        read_segment=read_segment,
        signals=SimpleNamespace(progress=SimpleNamespace(emit=Mock())),
        _note_activity=Mock(),
        update_logarithmic_partial_data=Mock(),
    )

    result = SweepWorker.read_averaged_frequencies(
        worker, [1_000_000, 2_000_000]
    )

    assert primed == [True, True]
    assert result == ([1_000_000, 2_000_000], [1 + 0j] * 2, [2 + 0j] * 2)
