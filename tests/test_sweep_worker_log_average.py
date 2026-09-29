from types import SimpleNamespace
from unittest.mock import Mock

from NanoVNASaver.RFTools import Datapoint
from NanoVNASaver.SweepWorker import SweepWorker


def test_multiple_averages_update_at_each_decade() -> None:
    frequencies = [1_000_000, 10_000_000, 100_000_000]
    samples = iter([1, 2, 3, 3, 4, 5])

    def read_segment(start, stop):
        sample = next(samples)
        return [start], [complex(sample)], [complex(sample * 10)]

    def read_with_retries(start, stop, reader=None):
        return reader() if reader else read_segment(start, stop)

    updates = []

    def record_update(current_freq, values11, values21, preserve_tail=False):
        updates.append(
            (current_freq[:], values11[:], values21[:], preserve_tail)
        )

    vna = SimpleNamespace(datapoints=201)
    worker = SimpleNamespace(
        app=SimpleNamespace(vna=vna),
        sweep=SimpleNamespace(
            properties=SimpleNamespace(
                logarithmic_points_per_scan=1,
                averages=(2, 0),
            )
        ),
        _terminate=False,
        _safe_f_v2_logarithmic_enabled=Mock(return_value=False),
        _first_decade_boundary=SweepWorker._first_decade_boundary,
        _read_segment_with_retries=read_with_retries,
        read_segment=read_segment,
        update_logarithmic_partial_data=record_update,
        signals=SimpleNamespace(progress=SimpleNamespace(emit=Mock())),
        _note_activity=Mock(),
        _preserve_previous_batch_trace=False,
    )
    worker._update_logarithmic_running_average = (
        lambda *args: SweepWorker._update_logarithmic_running_average(
            worker, *args
        )
    )

    result = SweepWorker.read_averaged_frequencies(
        worker, frequencies, averages=2
    )

    assert updates == [
        (frequencies[:2], [1 + 0j, 2 + 0j], [10 + 0j, 20 + 0j], False),
        (
            frequencies,
            [1 + 0j, 2 + 0j, 3 + 0j],
            [10 + 0j, 20 + 0j, 30 + 0j],
            False,
        ),
        (frequencies[:2], [2 + 0j, 3 + 0j], [20 + 0j, 30 + 0j], True),
        (
            frequencies,
            [2 + 0j, 3 + 0j, 4 + 0j],
            [20 + 0j, 30 + 0j, 40 + 0j],
            True,
        ),
    ]
    assert result == (
        frequencies,
        [2 + 0j, 3 + 0j, 4 + 0j],
        [20 + 0j, 30 + 0j, 40 + 0j],
    )


def test_partial_display_contains_only_measured_prefix() -> None:
    frequencies = [1_000_000, 10_000_000, 100_000_000]
    initial = [Datapoint(freq, 0.0, 0.0) for freq in frequencies]
    save_data = Mock()
    worker = SimpleNamespace(
        data11=initial[:],
        data21=initial[:],
        rawData11=initial[:],
        rawData21=initial[:],
        applyCalibration=lambda s11, s21: (s11, s21),
        app=SimpleNamespace(saveData=save_data),
        signals=SimpleNamespace(updated=SimpleNamespace(emit=Mock())),
    )

    SweepWorker.update_logarithmic_partial_data(
        worker,
        frequencies[:2],
        [1 + 0j, 2 + 0j],
        [10 + 0j, 20 + 0j],
    )

    shown11, shown21 = save_data.call_args.args
    assert [point.freq for point in shown11] == frequencies[:2]
    assert [point.freq for point in shown21] == frequencies[:2]
    assert len(worker.data11) == len(frequencies)

    save_data.reset_mock()
    SweepWorker.update_logarithmic_partial_data(
        worker,
        frequencies[:2],
        [3 + 0j, 4 + 0j],
        [30 + 0j, 40 + 0j],
        preserve_tail=True,
    )
    shown11, shown21 = save_data.call_args.args
    assert [point.freq for point in shown11] == frequencies
    assert [point.freq for point in shown21] == frequencies


def test_next_batch_sweep_keeps_previous_trace() -> None:
    frequencies = [1_000_000, 10_000_000, 100_000_000]
    previous11 = [Datapoint(freq, 5.0, 0.0) for freq in frequencies]
    previous21 = [Datapoint(freq, 6.0, 0.0) for freq in frequencies]
    app = SimpleNamespace(
        _batch_active=True,
        data=SimpleNamespace(s11=previous11, s21=previous21),
    )
    worker = SimpleNamespace(
        app=app,
        sweep=SimpleNamespace(
            properties=SimpleNamespace(logarithmic_measurement=True),
            get_logarithmic_measurement_frequencies=lambda: iter(frequencies),
        ),
    )
    worker._init_from_previous_data = lambda values: (
        SweepWorker._init_from_previous_data(worker, values)
    )

    SweepWorker.init_data(worker)

    assert worker._preserve_previous_batch_trace
    updates = Mock()
    worker.update_logarithmic_partial_data = updates
    SweepWorker._update_logarithmic_running_average(
        worker,
        frequencies[:2],
        [1 + 0j, 2 + 0j],
        [3 + 0j, 4 + 0j],
        [],
        [],
    )
    assert updates.call_args.kwargs == {"preserve_tail": True}

    save_data = Mock()
    worker.app.saveData = save_data
    worker.signals = SimpleNamespace(updated=SimpleNamespace(emit=Mock()))
    worker.applyCalibration = lambda s11, s21: (s11, s21)
    SweepWorker.update_logarithmic_partial_data(
        worker,
        frequencies[:2],
        [1 + 0j, 2 + 0j],
        [3 + 0j, 4 + 0j],
        preserve_tail=True,
    )
    shown11, shown21 = save_data.call_args.args
    assert len(shown11) == len(frequencies)
    assert shown11[-1] == previous11[-1]
    assert shown21[-1] == previous21[-1]
