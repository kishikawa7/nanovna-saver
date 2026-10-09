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
import contextlib
import logging
from collections.abc import Callable
from math import floor, log10
from time import monotonic, sleep
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal, Slot

from .Calibration import correct_delay
from .Hardware.VNA import VNA, VNACommandRetryExceeded
from .RFTools import Datapoint
from .Settings.Sweep import Sweep, SweepMode

if TYPE_CHECKING:
    from .NanoVNASaver.NanoVNASaver import NanoVNASaver as vna_app

logger = logging.getLogger(__name__)

VALUE_MAX: float = 9.5
RETRIES_RECONNECT: int = 3
RETRIES_MAX: int = 10
SEGMENT_TIMEOUT_S: float = 120.0
LOG_SINGLE_POINT_SCAN_COOLDOWN_S: float = 0.03


def truncate(values: list[list[complex]], count: int) -> list[list[complex]]:
    """truncate drops extrema from data list if averaging is active"""
    keep = len(values) - count
    logger.debug("Truncating from %d values to %d", len(values), keep)
    if count < 1 or keep < 1:
        logger.info("Not doing illegal truncate")
        return values
    truncated = []
    for valueset in np.swapaxes(values, 0, 1).tolist():
        avg = complex(np.average(valueset))

        def cmp_fn(v: complex, avg=avg) -> float:
            return float(abs(avg - v))

        truncated.append(sorted(valueset, key=cmp_fn)[:keep])
    return np.swapaxes(truncated, 0, 1).tolist()


class WorkerSignals(QObject):
    updated = Signal()
    progress = Signal()
    finished = Signal()
    sweep_error = Signal()


class SweepWorker(QThread):
    def __init__(self, app: "vna_app") -> None:
        super().__init__()
        logger.info("Initializing SweepWorker")
        self.signals: WorkerSignals = WorkerSignals()
        self.app = app
        self.sweep = Sweep()
        self.percentage: float = 0.0
        self.data11: list[Datapoint] = []
        self.data21: list[Datapoint] = []
        self.rawData11: list[Datapoint] = []
        self.rawData21: list[Datapoint] = []
        self.init_data()
        self.error_message: str = ""
        self.offsetDelay: float = 0.0
        self._terminate: bool = False

    @Slot()
    def quit(self) -> None:
        logger.debug("Worker quit request")
        self._terminate = True

    @Slot()
    def run(self) -> None:
        self._terminate = False
        try:
            self._run()
        except BaseException as exc:  # pylint: disable=broad-except
            logger.exception("%s", exc)
            if self.app._batch_active:
                action = (
                    "Attempting USB recovery"
                    if isinstance(exc, VNACommandRetryExceeded)
                    else "Continuing batch"
                )
                self.gui_error(f"ERROR during sweep\n\n{action}\n\n{exc}")
                logger.debug('Sending "finished" signal after sweep error')
                self.signals.finished.emit()
            else:
                self.gui_error(f"ERROR during sweep\n\nStopped\n\n{exc}")
            if (
                logger.isEnabledFor(logging.DEBUG)
                and not self.app._batch_active
            ):
                raise exc

    def _run(self) -> None:
        logger.info("Initializing SweepWorker")
        if not self.app.vna.connected():
            logger.debug(
                "Attempted to run without being connected to the NanoVNA"
            )
            return

        self.percentage = 0.0

        sweep = self.app.sweep.copy()
        if sweep != self.sweep:  # parameters changed
            self.sweep = sweep
            self.init_data()
        elif sweep.properties.logarithmic_measurement:
            self.init_data()

        self._run_loop()

        if sweep.segments > 1 or sweep.properties.logarithmic_measurement:
            start = sweep.start
            end = sweep.end
            logger.debug(
                "Resetting NanoVNA sweep to full range: %d to %d", start, end
            )
            self.app.vna.resetSweep(start, end)

        self.percentage = 100.0
        logger.debug('Sending "finished" signal')
        self.signals.finished.emit()

    def _run_loop(self) -> None:
        sweep = self.sweep
        averages = (
            sweep.properties.averages[0]
            if sweep.properties.mode == SweepMode.AVERAGE
            else 1
        )
        logger.info("%d averages", averages)
        if sweep.properties.logarithmic_measurement:
            self._run_logarithmic_loop(averages)
            return

        while True:
            for i in range(sweep.segments):
                logger.debug("Sweep segment no %d", i)
                if self._terminate:
                    logger.debug("Stopping sweeping as signalled")
                    break
                start, stop = sweep.get_index_range(i)

                freq, values11, values21 = self.read_averaged_segment(
                    start, stop, averages
                )
                self.percentage = (i + 1) * 100 / sweep.segments
                self.update_data(freq, values11, values21, i)
            if sweep.properties.mode != SweepMode.CONTINOUS or self._terminate:
                break

    def _run_logarithmic_loop(self, averages: int) -> None:
        frequencies = list(self.sweep.get_logarithmic_measurement_frequencies())
        while True:
            if self._terminate:
                logger.debug("Stopping logarithmic sweep as signalled")
                break

            freq, values11, values21 = self.read_averaged_frequencies(
                frequencies, averages
            )
            if not freq:
                break
            self.update_data(freq, values11, values21, 0)

            if (
                self.sweep.properties.mode != SweepMode.CONTINOUS
                or self._terminate
            ):
                break

    def init_data(self) -> None:
        self.data11 = []
        self.data21 = []
        self.rawData11 = []
        self.rawData21 = []
        self._preserve_previous_batch_trace = False
        if self.sweep.properties.logarithmic_measurement:
            frequencies = list(
                self.sweep.get_logarithmic_measurement_frequencies()
            )
        else:
            frequencies = list(self.sweep.get_frequencies())
        if self._init_from_previous_data(frequencies):
            self._preserve_previous_batch_trace = bool(
                getattr(self.app, "_batch_active", False)
            )
            return
        for freq in frequencies:
            self.data11.append(Datapoint(freq, 0.0, 0.0))
            self.data21.append(Datapoint(freq, 0.0, 0.0))
            self.rawData11.append(Datapoint(freq, 0.0, 0.0))
            self.rawData21.append(Datapoint(freq, 0.0, 0.0))
        logger.debug("Init data length: %s", len(self.data11))

    def _init_from_previous_data(self, frequencies: list[int]) -> bool:
        if not hasattr(self.app, "data"):
            return False

        previous11 = list(getattr(self.app.data, "s11", []))
        previous21 = list(getattr(self.app.data, "s21", []))
        if (
            len(previous11) != len(frequencies)
            or len(previous21) != len(frequencies)
            or [p.freq for p in previous11] != frequencies
            or [p.freq for p in previous21] != frequencies
        ):
            return False

        self.data11 = previous11[:]
        self.data21 = previous21[:]
        self.rawData11 = previous11[:]
        self.rawData21 = previous21[:]
        logger.debug(
            "Init data length: %s using previous display data",
            len(self.data11),
        )
        return True

    def update_data(
        self,
        frequencies: list[int],
        values11: list[complex],
        values21: list[complex],
        index: int,
    ) -> None:
        # Update the data from (i*101) to (i+1)*101
        logger.debug(
            "Calculating data and inserting in existing data at index %d", index
        )
        offset = self.sweep.points * index

        raw_data11 = [
            Datapoint(freq, values11[i].real, values11[i].imag)
            for i, freq in enumerate(frequencies)
        ]
        raw_data21 = [
            Datapoint(freq, values21[i].real, values21[i].imag)
            for i, freq in enumerate(frequencies)
        ]

        data11, data21 = self.applyCalibration(raw_data11, raw_data21)
        logger.debug("update Freqs: %s, Offset: %s", len(frequencies), offset)
        for i in range(len(frequencies)):
            self.data11[offset + i] = data11[i]
            self.data21[offset + i] = data21[i]
            self.rawData11[offset + i] = raw_data11[i]
            self.rawData21[offset + i] = raw_data21[i]

        logger.debug(
            "Saving data to application (%d and %d points)",
            len(self.data11),
            len(self.data21),
        )
        self.app.saveData(self.data11, self.data21)
        logger.debug('Sending "updated" signal')
        self.signals.updated.emit()

    def update_logarithmic_partial_data(
        self,
        frequencies: list[int],
        values11: list[complex],
        values21: list[complex],
        preserve_tail: bool = False,
    ) -> None:
        raw_data11 = [
            Datapoint(freq, values11[i].real, values11[i].imag)
            for i, freq in enumerate(frequencies)
        ]
        raw_data21 = [
            Datapoint(freq, values21[i].real, values21[i].imag)
            for i, freq in enumerate(frequencies)
        ]

        data11, data21 = self.applyCalibration(raw_data11, raw_data21)
        logger.debug(
            "Merging partial logarithmic data into display data (%d points)",
            len(frequencies),
        )
        for i in range(len(frequencies)):
            self.data11[i] = data11[i]
            self.data21[i] = data21[i]
            self.rawData11[i] = raw_data11[i]
            self.rawData21[i] = raw_data21[i]
        if preserve_tail:
            self.app.saveData(self.data11, self.data21)
        else:
            self.app.saveData(
                self.data11[: len(frequencies)],
                self.data21[: len(frequencies)],
            )
        self.signals.updated.emit()

    def applyCalibration(
        self, raw_data11: list[Datapoint], raw_data21: list[Datapoint]
    ) -> tuple[list[Datapoint], list[Datapoint]]:
        data11: list[Datapoint] = []
        data21: list[Datapoint] = []

        if not self.app.calibration.isCalculated:
            data11 = raw_data11.copy()
            data21 = raw_data21.copy()
        elif self.app.calibration.isValid1Port():
            data11.extend(
                self.app.calibration.correct11(dp) for dp in raw_data11
            )
        else:
            data11 = raw_data11.copy()

        if self.app.calibration.isValid2Port():
            for counter, dp in enumerate(raw_data21):
                dp11 = raw_data11[counter]
                data21.append(self.app.calibration.correct21(dp, dp11))
        else:
            data21 = raw_data21

        if self.offsetDelay != 0.0:
            data11 = [
                correct_delay(dp, self.offsetDelay, reflect=True)
                for dp in data11
            ]
            data21 = [correct_delay(dp, self.offsetDelay) for dp in data21]

        return data11, data21

    def _note_activity(self) -> None:
        with contextlib.suppress(Exception):
            self.app._note_batch_worker_activity()

    def read_averaged_segment(
        self, start: int, stop: int, averages: int = 1
    ) -> tuple[list[int], list[complex], list[complex]]:
        logger.info(
            "Reading from %d to %d. Averaging %d values", start, stop, averages
        )

        freq: list[int] = []
        values11: list[list[complex]] = []
        values21: list[list[complex]] = []

        for i in range(averages):
            if self._terminate:
                logger.debug("Stopping averaging as signalled.")
                if averages == 1:
                    break
                logger.warning("Stop during average. Discarding sweep result.")
                return [], [], []
            logger.debug("Reading average no %d / %d", i + 1, averages)
            retries = RETRIES_RECONNECT
            tmp_11: list[complex] = []
            tmp_21: list[complex] = []
            while retries and not tmp_11:
                self._note_activity()
                if retries < RETRIES_RECONNECT:
                    logger.warning("retry readSegment(%s,%s)", start, stop)
                    sleep(0.5)
                    self._note_activity()
                retries -= 1
                segment_started = monotonic()
                try:
                    freq, tmp_11, tmp_21 = self.read_segment(start, stop)
                except VNACommandRetryExceeded as exc:
                    logger.warning(
                        "readSegment(%s,%s) communication failed: %s",
                        start,
                        stop,
                        exc,
                    )
                    with contextlib.suppress(Exception):
                        self.app.vna.reconnect()
                    self._note_activity()
                    if retries:
                        sleep(2.0)
                        self._note_activity()
                        continue
                    raise
                elapsed = monotonic() - segment_started
                if elapsed > SEGMENT_TIMEOUT_S:
                    logger.warning(
                        "readSegment(%s,%s) took %.1fs; reconnecting",
                        start,
                        stop,
                        elapsed,
                    )
                    with contextlib.suppress(Exception):
                        self.app.vna.reconnect()

            if not tmp_11:
                raise IOError("Invalid data during sweep")

            values11.append(tmp_11)
            values21.append(tmp_21)
            self.percentage += 100 / (self.sweep.segments * averages)
            self.signals.updated.emit()

        if not values11:
            raise IOError("Invalid data during sweep")

        truncates = self.sweep.properties.averages[1]
        if truncates > 0 and averages > 1:
            logger.debug("Truncating %d values by %d", len(values11), truncates)
            values11 = truncate(values11, truncates)
            values21 = truncate(values21, truncates)

        logger.debug("Averaging %d values", len(values11[0]))
        return (
            freq,
            np.average(values11, axis=0).tolist(),
            np.average(values21, axis=0).tolist(),
        )

    @staticmethod
    def _first_decade_boundary(frequency: int) -> int:
        return 10 ** (floor(log10(frequency)) + 1)

    def read_averaged_frequencies(
        self, frequencies: list[int], averages: int = 1
    ) -> tuple[list[int], list[complex], list[complex]]:
        logger.info(
            "Reading %d logarithmic frequency points. Averaging %d values",
            len(frequencies),
            averages,
        )

        measured_freqs: list[int] = frequencies[:]
        values11: list[list[complex]] = []
        values21: list[list[complex]] = []
        original_datapoints = self.app.vna.datapoints
        total_reads = max(1, len(frequencies) * averages)
        completed_reads = 0
        safe_f_v2_scan = self._safe_f_v2_logarithmic_enabled()
        points_per_scan = (
            1
            if safe_f_v2_scan
            else max(
                1,
                self.sweep.properties.logarithmic_points_per_scan,
            )
        )

        try:
            for i in range(averages):
                if self._terminate:
                    logger.debug("Stopping logarithmic averaging as signalled.")
                    if averages == 1:
                        break
                    logger.warning(
                        "Stop during logarithmic average. "
                        "Discarding sweep result."
                    )
                    return [], [], []

                logger.debug(
                    "Reading logarithmic average no %d / %d",
                    i + 1,
                    averages,
                )
                tmp_freq: list[int] = []
                tmp_11: list[complex] = []
                tmp_21: list[complex] = []
                next_partial_update = self._first_decade_boundary(
                    frequencies[0]
                )
                last_partial_update_count = 0

                for start_index in range(0, len(frequencies), points_per_scan):
                    if self._terminate:
                        break
                    chunk = frequencies[
                        start_index : start_index + points_per_scan
                    ]
                    if safe_f_v2_scan:
                        measured_freq, point_11, point_21 = (
                            self._read_safe_f_v2_frequency(chunk[0])
                        )
                    else:
                        self.app.vna.datapoints = len(chunk)
                        prime_single_point = bool(
                            len(chunk) == 1
                            and getattr(
                                self.app.vna,
                                "prime_each_logarithmic_point",
                                False,
                            )
                        )
                        measured_freq, point_11, point_21 = (
                            self._read_segment_with_retries(
                                chunk[0],
                                chunk[-1],
                                (
                                    lambda chunk=chunk: self.read_segment(
                                        chunk[0],
                                        chunk[-1],
                                        discard_first_sample=True,
                                    )
                                )
                                if prime_single_point
                                else None,
                            )
                        )
                    if not point_11:
                        raise IOError("Invalid data during logarithmic sweep")

                    if len(point_11) == len(chunk):
                        selected = list(range(len(chunk)))
                    else:
                        selected = [
                            self._nearest_frequency_index(measured_freq, freq)
                            for freq in chunk
                        ]

                    if len(chunk) > len(point_11):
                        raise IOError("Invalid data during logarithmic sweep")

                    for target_freq, measured_index in zip(
                        chunk, selected, strict=False
                    ):
                        if tmp_freq and target_freq == tmp_freq[-1]:
                            continue
                        tmp_freq.append(target_freq)
                        tmp_11.append(point_11[measured_index])
                        tmp_21.append(point_21[measured_index])
                        completed_reads += 1
                        self.percentage = completed_reads * 100 / total_reads
                        self.signals.progress.emit()
                        while tmp_freq[-1] >= next_partial_update:
                            self._update_logarithmic_running_average(
                                tmp_freq,
                                tmp_11,
                                tmp_21,
                                values11,
                                values21,
                            )
                            last_partial_update_count = len(tmp_freq)
                            next_partial_update *= 10
                    if points_per_scan == 1 and not self._terminate:
                        sleep(LOG_SINGLE_POINT_SCAN_COOLDOWN_S)
                        self._note_activity()

                if self._terminate:
                    break
                if (
                    averages > 1
                    and not values11
                    and len(tmp_freq) > last_partial_update_count
                ):
                    # Complete the first trace before the next averaging pass
                    # starts updating it from the low-frequency end.
                    self.update_logarithmic_partial_data(
                        tmp_freq, tmp_11, tmp_21
                    )
                measured_freqs = tmp_freq
                values11.append(tmp_11)
                values21.append(tmp_21)

        finally:
            self.app.vna.datapoints = original_datapoints

        if self._terminate:
            return [], [], []

        if not values11:
            raise IOError("Invalid data during logarithmic sweep")

        truncates = self.sweep.properties.averages[1]
        if truncates > 0 and averages > 1:
            logger.debug("Truncating %d values by %d", len(values11), truncates)
            values11 = truncate(values11, truncates)
            values21 = truncate(values21, truncates)

        logger.debug("Averaging %d logarithmic values", len(values11[0]))
        return (
            measured_freqs,
            np.average(values11, axis=0).tolist(),
            np.average(values21, axis=0).tolist(),
        )

    def _update_logarithmic_running_average(
        self,
        frequencies: list[int],
        current11: list[complex],
        current21: list[complex],
        completed11: list[list[complex]],
        completed21: list[list[complex]],
    ) -> None:
        count = len(frequencies)
        samples11 = [values[:count] for values in completed11]
        samples21 = [values[:count] for values in completed21]
        samples11.append(current11)
        samples21.append(current21)
        self.update_logarithmic_partial_data(
            frequencies,
            np.average(samples11, axis=0).tolist(),
            np.average(samples21, axis=0).tolist(),
            preserve_tail=(
                bool(completed11) or self._preserve_previous_batch_trace
            ),
        )

    def _safe_f_v2_logarithmic_enabled(self) -> bool:
        return bool(
            self.sweep.properties.f_v2_safe_logarithmic
            and callable(
                getattr(
                    self.app.vna,
                    "read_centered_single_frequency",
                    None,
                )
            )
        )

    def _read_safe_f_v2_frequency(
        self, frequency: int
    ) -> tuple[list[int], list[complex], list[complex]]:
        vna = self.app.vna
        points = int(vna.safe_logarithmic_scan_points)
        vna.datapoints = points

        measured, values11, values21 = self._read_segment_with_retries(
            frequency,
            frequency,
            lambda: vna.read_centered_single_frequency(frequency),
        )
        if not (len(measured) == len(values11) == len(values21) == 1):
            raise IOError(
                "Invalid centered single-frequency data: "
                "expected one selected row, got "
                f"{len(measured)}/{len(values11)}/{len(values21)}"
            )
        if measured[0] != frequency:
            raise IOError(
                "Centered single-frequency scan returned a different frequency"
            )
        logger.debug(
            "F V2 centered %d-point scan selected %d Hz",
            points,
            frequency,
        )
        return measured, values11, values21

    @staticmethod
    def _nearest_frequency_index(
        measured_frequencies: list[int], target_frequency: int
    ) -> int:
        if not measured_frequencies:
            return 0
        return min(
            range(len(measured_frequencies)),
            key=lambda i: abs(measured_frequencies[i] - target_frequency),
        )

    def _read_segment_with_retries(
        self,
        start: int,
        stop: int,
        reader: Callable[[], tuple[list[int], list[complex], list[complex]]]
        | None = None,
    ) -> tuple[list[int], list[complex], list[complex]]:
        retries = RETRIES_RECONNECT
        freq: list[int] = []
        values11: list[complex] = []
        values21: list[complex] = []

        while retries and not values11:
            self._note_activity()
            if retries < RETRIES_RECONNECT:
                logger.warning("retry readSegment(%s,%s)", start, stop)
                sleep(0.5)
                self._note_activity()
            retries -= 1
            segment_started = monotonic()
            try:
                if reader is not None:
                    freq, values11, values21 = reader()
                else:
                    freq, values11, values21 = self.read_segment(start, stop)
            except VNACommandRetryExceeded as exc:
                logger.warning(
                    "readSegment(%s,%s) communication failed: %s",
                    start,
                    stop,
                    exc,
                )
                with contextlib.suppress(Exception):
                    self.app.vna.reconnect()
                self._note_activity()
                if retries:
                    sleep(2.0)
                    self._note_activity()
                    continue
                raise
            elapsed = monotonic() - segment_started
            if elapsed > SEGMENT_TIMEOUT_S:
                logger.warning(
                    "readSegment(%s,%s) took %.1fs; reconnecting",
                    start,
                    stop,
                    elapsed,
                )
                with contextlib.suppress(Exception):
                    self.app.vna.reconnect()

        return freq, values11, values21

    def read_segment(
        self, start: int, stop: int, discard_first_sample: bool = False
    ) -> tuple[list[int], list[complex], list[complex]]:
        logger.debug("Setting sweep range to %d to %d", start, stop)
        self.app.vna.setSweep(start, stop)

        frequencies = self.app.vna.read_frequencies()
        logger.debug("Read %s frequencies", len(frequencies))
        discarded = None
        if discard_first_sample:
            # LiteVNA's first single-frequency acquisition may precede its
            # reference measurement. Read it without applying validation and
            # leave the sweep configuration unchanged for the next sample.
            try:
                discarded = self.app.vna.readValues("data 0")
            except ValueError as exc:
                logger.warning("LiteVNA priming read failed: %s", exc)
                self.app.vna.reconnect()
                self.app.vna.setSweep(start, stop)
                discarded = self.app.vna.readValues("data 0")
        if discard_first_sample:
            first11 = self.read_data("data 0")
            first21 = self.read_data("data 1")
            second11 = self.read_data("data 0")
            second21 = self.read_data("data 1")
            if not (
                len(frequencies)
                == len(first11)
                == len(first21)
                == len(second11)
                == len(second21)
            ):
                logger.info("No valid data during this run")
                return [], [], []
            values11 = [(a + b) / 2 for a, b in zip(first11, second11)]
            values21 = [(a + b) / 2 for a, b in zip(first21, second21)]
            logger.debug(
                "Primed first LiteVNA point at %d Hz: discarded S11=%s, "
                "averaged S11=%s S21=%s",
                start,
                discarded,
                values11,
                values21,
            )
        else:
            values11 = self.read_data("data 0")
            values21 = self.read_data("data 1")
        if not len(frequencies) == len(values11) == len(values21):
            logger.info("No valid data during this run")
            frequencies = []
            values11 = values21 = []
        return frequencies, values11, values21

    def read_data(self, data) -> list[complex]:
        logger.debug("Reading %s", data)

        vna: "VNA" = self.app.vna  # shortcut to device
        retries = RETRIES_MAX
        while retries:
            retries -= 1
            try:
                result = vna.readValues(data)
                logger.debug("Read %d values", len(result))
                if vna.validateInput and any(
                    abs(v) > VALUE_MAX for v in result
                ):
                    logger.error("Got a non plausible data: (%s)", data)
                else:
                    return result
            except ValueError as exc:
                logger.exception(
                    "An exception occurred reading %s: %s", data, exc
                )
            logger.error("Re-reading %s", data)
            sleep(0.2)
            vna.reconnect()

        logger.critical(
            "Tried and failed to read %s %s times. Giving up.",
            data,
            RETRIES_MAX,
        )
        raise IOError(
            f"Failed reading {data} {RETRIES_MAX} times.\n"
            f"Data outside expected valid ranges,"
            f" or in an unexpected format.\n\n"
            f"You can disable data validation on the"
            f"device settings screen."
        )

    def gui_error(self, message: str) -> None:
        self.error_message = message
        self.signals.sweep_error.emit()
