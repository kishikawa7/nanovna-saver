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
import math

from PySide6 import QtGui, QtWidgets

from ..Formatting import format_frequency_chart
from ..RFTools import Datapoint
from ..SITools import Format, Value
from .Chart import Chart
from .RI import RealImaginaryChart

logger = logging.getLogger(__name__)


def _parse_axis_value(text: str) -> float:
    value = text.strip().replace("\N{OHM SIGN}", "").replace("ohm", "")
    multipliers = {
        "p": 1e-12,
        "n": 1e-9,
        "u": 1e-6,
        "\N{MICRO SIGN}": 1e-6,
        "m": 1e-3,
        "k": 1e3,
        "K": 1e3,
        "M": 1e6,
        "G": 1e9,
    }
    if value and value[-1] in multipliers:
        return float(value[:-1]) * multipliers[value[-1]]
    return float(value)


class RealImaginaryZChart(RealImaginaryChart):
    log_axis_min = 1e-3

    def __init__(self, name=""):
        super().__init__(name)
        self.y_logarithmic = True
        self.min_real = self.log_axis_min
        self.min_imag = self.log_axis_min

        self.y_menu.addSeparator()
        self.action_set_linear_y = QtGui.QAction("Linear Y")
        self.action_set_linear_y.setCheckable(True)
        self.action_set_linear_y.triggered.connect(
            lambda: self.setLogarithmicY(False)
        )
        self.action_set_logarithmic_y = QtGui.QAction("Logarithmic Y")
        self.action_set_logarithmic_y.setCheckable(True)
        self.action_set_logarithmic_y.setChecked(True)
        self.action_set_logarithmic_y.triggered.connect(
            lambda: self.setLogarithmicY(True)
        )
        y_scale_group = QtGui.QActionGroup(self)
        y_scale_group.addAction(self.action_set_linear_y)
        y_scale_group.addAction(self.action_set_logarithmic_y)
        self.y_menu.addAction(self.action_set_linear_y)
        self.y_menu.addAction(self.action_set_logarithmic_y)

        self.y_menu.addSeparator()

        self.action_set_fixed_maximum_real = QtGui.QAction(
            f"Maximum R ({self.maxDisplayReal})"
        )
        self.action_set_fixed_maximum_real.triggered.connect(
            self.setMaximumRealValue
        )

        self.action_set_fixed_minimum_real = QtGui.QAction(
            f"Minimum R ({self.minDisplayReal})"
        )
        self.action_set_fixed_minimum_real.triggered.connect(
            self.setMinimumRealValue
        )

        self.action_set_fixed_maximum_imag = QtGui.QAction(
            f"Maximum jX ({self.maxDisplayImag})"
        )
        self.action_set_fixed_maximum_imag.triggered.connect(
            self.setMaximumImagValue
        )

        self.action_set_fixed_minimum_imag = QtGui.QAction(
            f"Minimum jX ({self.minDisplayImag})"
        )
        self.action_set_fixed_minimum_imag.triggered.connect(
            self.setMinimumImagValue
        )

        self.y_menu.addAction(self.action_set_fixed_maximum_real)
        self.y_menu.addAction(self.action_set_fixed_minimum_real)
        self.y_menu.addSeparator()
        self.y_menu.addAction(self.action_set_fixed_maximum_imag)
        self.y_menu.addAction(self.action_set_fixed_minimum_imag)
        # Default to logarithmic X-axis for LogMag charts
        try:
            self.setLogarithmicX(True)
            # update menu checked state if actions exist
            if hasattr(self, "action_set_logarithmic_x"):
                self.action_set_logarithmic_x.setChecked(True)
            if hasattr(self, "action_set_linear_x"):
                self.action_set_linear_x.setChecked(False)
        except Exception:
            pass

    def copy(self) -> "RealImaginaryZChart":
        new_chart: RealImaginaryZChart = super().copy()
        new_chart.y_logarithmic = self.y_logarithmic
        new_chart.action_set_linear_y.setChecked(not self.y_logarithmic)
        new_chart.action_set_logarithmic_y.setChecked(self.y_logarithmic)
        return new_chart

    def setLogarithmicY(self, logarithmic: bool) -> None:
        self.y_logarithmic = logarithmic
        self.action_set_linear_y.setChecked(not logarithmic)
        self.action_set_logarithmic_y.setChecked(logarithmic)
        self.update()
        
    def drawChart(self, qp: QtGui.QPainter):
        qp.setPen(QtGui.QPen(Chart.color.text))
        qp.drawText(self.leftMargin + 5, 15, f"{self.name} (\N{OHM SIGN})")
        qp.drawText(10, 15, "|R|")
        qp.drawText(self.leftMargin + self.dim.width + 10, 15, "|X|")
        qp.setPen(QtGui.QPen(Chart.color.foreground))
        qp.drawLine(
            self.leftMargin,
            self.topMargin - 5,
            self.leftMargin,
            self.topMargin + self.dim.height + 5,
        )
        qp.drawLine(
            self.leftMargin - 5,
            self.topMargin + self.dim.height,
            self.leftMargin + self.dim.width + 5,
            self.topMargin + self.dim.height,
        )
        self.drawTitle(qp)

    def contextMenuEvent(self, event):
        self.action_set_fixed_start.setText(
            f"Start ({format_frequency_chart(self.minFrequency)})"
        )
        self.action_set_fixed_stop.setText(
            f"Stop ({format_frequency_chart(self.maxFrequency)})"
        )
        self.action_set_fixed_minimum_real.setText(
            f"Minimum R ({self.minDisplayReal})"
        )
        self.action_set_fixed_maximum_real.setText(
            f"Maximum R ({self.maxDisplayReal})"
        )
        self.action_set_fixed_minimum_imag.setText(
            f"Minimum jX ({self.minDisplayImag})"
        )
        self.action_set_fixed_maximum_imag.setText(
            f"Maximum jX ({self.maxDisplayImag})"
        )
        self.menu.exec(event.globalPos())

    def value(self, p: Datapoint) -> complex:
        return self.impedance(p)

    def impedance(self, p: Datapoint) -> complex:
        return p.impedance()

    def _log_axis_max(self, value: float, axis_min: float) -> float:
        value = max(value, axis_min)
        axis_max = 10 ** math.ceil(math.log10(value))
        if axis_max <= axis_min:
            return axis_min * 10
        return axis_max

    def _safe_abs(self, value: float, axis_min: float | None = None) -> float:
        if axis_min is None:
            axis_min = self.log_axis_min
        if not math.isfinite(value):
            return axis_min
        return max(abs(value), axis_min)

    def _axis_min(self, value: float) -> float:
        if not math.isfinite(value) or value <= 0:
            return self.log_axis_min
        return max(value, self.log_axis_min)

    def _linear_axis_limits(self, min_value, max_value) -> tuple[float, float]:
        if min_value is None or not math.isfinite(min_value):
            min_value = 0
        if not math.isfinite(max_value):
            max_value = 1
        if max_value <= min_value:
            max_value = min_value + 1
        return min_value, max_value

    def _y_position(
        self, value: float, axis_min: float, axis_max: float
    ) -> int:
        if not self.y_logarithmic:
            value = abs(value)
            span = (axis_max - axis_min) or 1
            return int(
                self.topMargin
                + (axis_max - value) / span * self.dim.height
            )

        log_min = math.log10(axis_min)
        log_max = math.log10(self._log_axis_max(axis_max, axis_min))
        log_value = math.log10(self._safe_abs(value, axis_min))
        span = log_max - log_min
        if span <= 0:
            return self.topMargin
        return int(
            self.topMargin
            + (log_max - log_value) / span * self.dim.height
        )

    def drawHorizontalTicks(self, qp):
        fmt = Format(max_nr_digits=3)
        horizontal_ticks = max(1, self.dim.height // 50)
        if not self.y_logarithmic:
            re_span = (self.max_real - self.min_real) or 1
            im_span = (self.max_imag - self.min_imag) or 1
            for i in range(horizontal_ticks):
                y = self.topMargin + i * self.dim.height // horizontal_ticks
                qp.setPen(QtGui.QPen(Chart.color.foreground))
                qp.drawLine(
                    self.leftMargin - 5,
                    y,
                    self.leftMargin + self.dim.width + 5,
                    y,
                )
                qp.setPen(QtGui.QPen(Chart.color.text))
                ratio = i / horizontal_ticks
                re = self.max_real - ratio * re_span
                im = self.max_imag - ratio * im_span
                qp.drawText(3, y + 4, f"{Value(re, fmt=fmt)}")
                qp.drawText(
                    self.leftMargin + self.dim.width + 8,
                    y + 4,
                    f"{Value(im, fmt=fmt)}",
                )
            return

        re_log_min = math.log10(self.min_real)
        im_log_min = math.log10(self.min_imag)
        re_log_max = math.log10(
            self._log_axis_max(self.max_real, self.min_real)
        )
        im_log_max = math.log10(
            self._log_axis_max(self.max_imag, self.min_imag)
        )

        for i in range(horizontal_ticks):
            y = self.topMargin + i * self.dim.height // horizontal_ticks
            qp.setPen(QtGui.QPen(Chart.color.foreground))
            qp.drawLine(
                self.leftMargin - 5, y, self.leftMargin + self.dim.width + 5, y
            )
            qp.setPen(QtGui.QPen(Chart.color.text))
            ratio = i / horizontal_ticks
            re = 10 ** (re_log_max - ratio * (re_log_max - re_log_min))
            im = 10 ** (im_log_max - ratio * (im_log_max - im_log_min))
            qp.drawText(3, y + 4, f"{Value(re, fmt=fmt)}")
            qp.drawText(
                self.leftMargin + self.dim.width + 8,
                y + 4,
                f"{Value(im, fmt=fmt)}",
            )

    def find_scaling(self):
        if self.fixedValues:
            if not self.y_logarithmic:
                self.min_real, max_real = self._linear_axis_limits(
                    abs(self.minDisplayReal),
                    abs(self.maxDisplayReal),
                )
                self.min_imag, max_imag = self._linear_axis_limits(
                    abs(self.minDisplayImag),
                    abs(self.maxDisplayImag),
                )
                return self.min_real, max_real, self.min_imag, max_imag

            self.min_real = self._axis_min(abs(self.minDisplayReal))
            self.min_imag = self._axis_min(abs(self.minDisplayImag))
            return (
                self.min_real,
                self._log_axis_max(abs(self.maxDisplayReal), self.min_real),
                self.min_imag,
                self._log_axis_max(abs(self.maxDisplayImag), self.min_imag),
            )

        max_real = self.log_axis_min if self.y_logarithmic else 0
        max_imag = self.log_axis_min if self.y_logarithmic else 0
        min_real = None
        min_imag = None
        for d in self.data:
            value = self.value(d)
            re = abs(value.real)
            im = abs(value.imag)
            if math.isfinite(re):
                min_real = re if min_real is None else min(min_real, re)
                max_real = max(
                    max_real,
                    self._safe_abs(re) if self.y_logarithmic else re,
                )
            if math.isfinite(im):
                min_imag = im if min_imag is None else min(min_imag, im)
                max_imag = max(
                    max_imag,
                    self._safe_abs(im) if self.y_logarithmic else im,
                )

        for d in self.reference:
            if d.freq < self.fstart or d.freq > self.fstop:
                continue
            value = self.value(d)
            re = abs(value.real)
            im = abs(value.imag)
            if math.isfinite(re):
                min_real = re if min_real is None else min(min_real, re)
                max_real = max(
                    max_real,
                    self._safe_abs(re) if self.y_logarithmic else re,
                )
            if math.isfinite(im):
                min_imag = im if min_imag is None else min(min_imag, im)
                max_imag = max(
                    max_imag,
                    self._safe_abs(im) if self.y_logarithmic else im,
                )

        if not self.y_logarithmic:
            self.min_real, max_real = self._linear_axis_limits(
                min_real, max_real
            )
            self.min_imag, max_imag = self._linear_axis_limits(
                min_imag, max_imag
            )
            return self.min_real, max_real, self.min_imag, max_imag

        self.min_real = self._axis_min(
            self.log_axis_min if min_real is None else min_real
        )
        self.min_imag = self._axis_min(
            self.log_axis_min if min_imag is None else min_imag
        )

        return (
            self.min_real,
            self._log_axis_max(max_real, self.min_real),
            self.min_imag,
            self._log_axis_max(max_imag, self.min_imag),
        )

    def getImYPosition(self, d: Datapoint) -> int:
        return self._y_position(
            self.value(d).imag, self.min_imag, self.max_imag
        )

    def getReYPosition(self, d: Datapoint) -> int:
        return self._y_position(
            self.value(d).real, self.min_real, self.max_real
        )

    def valueAtPosition(self, y) -> list[float]:
        absy = y - self.topMargin
        ratio = absy / self.dim.height
        if not self.y_logarithmic:
            val_re = self.max_real - ratio * (self.max_real - self.min_real)
            val_im = self.max_imag - ratio * (self.max_imag - self.min_imag)
            return [val_re, val_im]

        re_log_min = math.log10(self.min_real)
        im_log_min = math.log10(self.min_imag)
        re_log_max = math.log10(
            self._log_axis_max(self.max_real, self.min_real)
        )
        im_log_max = math.log10(
            self._log_axis_max(self.max_imag, self.min_imag)
        )
        val_re = 10 ** (re_log_max - ratio * (re_log_max - re_log_min))
        val_im = 10 ** (im_log_max - ratio * (im_log_max - im_log_min))
        return [val_re, val_im]

    def _get_positive_axis_value(self, title: str, label: str, value: float):
        text, selected = QtWidgets.QInputDialog.getText(
            self,
            title,
            label,
            text=f"{value:g}",
        )
        if not selected:
            return None
        try:
            parsed = _parse_axis_value(text)
        except ValueError:
            return None
        if not math.isfinite(parsed) or parsed <= 0:
            return None
        return parsed

    def setMinimumRealValue(self):
        min_val = self._get_positive_axis_value(
            "Minimum |R| value",
            "Set minimum |R| value",
            self.minDisplayReal,
        )
        if min_val is None:
            return
        if min_val >= abs(self.maxDisplayReal):
            return
        self.minDisplayReal = min_val
        self.setFixedValues(True)

    def setMaximumRealValue(self):
        max_val = self._get_positive_axis_value(
            "Maximum |R| value",
            "Set maximum |R| value",
            self.maxDisplayReal,
        )
        if max_val is None:
            return
        if max_val <= abs(self.minDisplayReal):
            return
        self.maxDisplayReal = max_val
        self.setFixedValues(True)

    def setMinimumImagValue(self):
        min_val = self._get_positive_axis_value(
            "Minimum |X| value",
            "Set minimum |X| value",
            self.minDisplayImag,
        )
        if min_val is None:
            return
        if min_val >= abs(self.maxDisplayImag):
            return
        self.minDisplayImag = min_val
        self.setFixedValues(True)

    def setMaximumImagValue(self):
        max_val = self._get_positive_axis_value(
            "Maximum |X| value",
            "Set maximum |X| value",
            self.maxDisplayImag,
        )
        if max_val is None:
            return
        if max_val <= abs(self.minDisplayImag):
            return
        self.maxDisplayImag = max_val
        self.setFixedValues(True)
