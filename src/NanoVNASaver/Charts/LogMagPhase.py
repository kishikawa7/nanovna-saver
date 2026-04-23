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
import numpy as np

from PySide6 import QtGui, QtWidgets

from ..Formatting import format_frequency_chart
from ..Marker.Widget import Marker
from ..RFTools import Datapoint
from ..SITools import Format, Value
from .Chart import Chart, ChartPosition
from .Frequency import FrequencyChart

logger = logging.getLogger(__name__)


class LogMagPhaseChart(FrequencyChart):
    def __init__(self, name=""):
        super().__init__(name)
        self.leftMargin = 45
        self.rightMargin = 45
        self.dim.width = 230
        self.dim.height = 250
        self.fstart = 0
        self.fstop = 0
        self.span_db = 0.01
        self.span_phase = 0.01
        self.max_db = 0
        self.max_phase = 180

        self.maxDisplayDB = 10
        self.maxDisplayPhase = 180
        self.minDisplayDB = -100
        self.minDisplayPhase = -180

        self.isInverted: bool = False
        #
        #  Build the context menu
        #

        self.y_menu.clear()

        self.y_action_automatic = QtGui.QAction("Automatic")
        self.y_action_automatic.setCheckable(True)
        self.y_action_automatic.setChecked(True)
        self.y_action_automatic.changed.connect(
            lambda: self.setFixedValues(self.y_action_fixed_span.isChecked())
        )
        self.y_action_fixed_span = QtGui.QAction("Fixed span")
        self.y_action_fixed_span.setCheckable(True)
        self.y_action_fixed_span.changed.connect(
            lambda: self.setFixedValues(self.y_action_fixed_span.isChecked())
        )
        mode_group = QtGui.QActionGroup(self)
        mode_group.addAction(self.y_action_automatic)
        mode_group.addAction(self.y_action_fixed_span)
        self.y_menu.addAction(self.y_action_automatic)
        self.y_menu.addAction(self.y_action_fixed_span)
        try:
            self.setLogarithmicX(True)
            # update menu checked state if actions exist
            if hasattr(self, "action_set_logarithmic_x"):
                self.action_set_logarithmic_x.setChecked(True)
            if hasattr(self, "action_set_linear_x"):
                self.action_set_linear_x.setChecked(False)
        except Exception:
            pass

    def copy(self) -> "LogMagPhaseChart":
        new_chart: LogMagPhaseChart = super().copy()

        new_chart.maxDisplayDB = self.maxDisplayDB
        new_chart.maxDisplayPhase = self.maxDisplayPhase
        new_chart.minDisplayDB = self.minDisplayDB
        new_chart.minDisplayPhase = self.minDisplayPhase
        return new_chart

    def drawValues(self, qp: QtGui.QPainter) -> None:
        if not self.data and not self.reference:
            return

        primary_pen = QtGui.QPen(Chart.color.sweep)
        primary_pen.setWidth(self.dim.point)
        secondary_pen = QtGui.QPen(Chart.color.sweep_secondary)
        secondary_pen.setWidth(self.dim.point)
        line_pen = QtGui.QPen(Chart.color.sweep)
        line_pen.setWidth(self.dim.line)
        highlighter = QtGui.QPen(QtGui.QColor(20, 0, 255))
        highlighter.setWidth(1)

        self._set_start_stop()

        # Draw bands if required
        if self.bands.enabled:
            self.drawBands(qp, self.fstart, self.fstop)

        min_db, max_db, min_phase, max_phase = self.find_scaling()

        self.max_db = max_db
        self.max_phase = max_phase
        # self.min_db = min_db
        # self.min_phase = min_phase

        self.span_db = (max_db - min_db) or 0.01
        self.span_phase = (max_phase - min_phase) or 0.01

        self.drawHorizontalTicks(qp)

        fmt = Format(max_nr_digits=3)
        qp.drawText(
            3, self.dim.height + self.topMargin, str(Value(min_db, fmt=fmt))
        )
        qp.drawText(
            self.leftMargin + self.dim.width + 8,
            self.dim.height + self.topMargin,
            str(Value(min_phase, fmt=fmt)),
        )

        self.drawFrequencyTicks(qp)

        self._draw_ri_labels(qp)
        self._draw_data(qp, line_pen, primary_pen, secondary_pen)

        if self.reference:
            primary_pen.setColor(Chart.color.reference)
            line_pen.setColor(Chart.color.reference)
            secondary_pen.setColor(Chart.color.reference_secondary)
            self._draw_ri_labels(qp, is_reference=True)
            self._draw_ref_data(qp, line_pen, primary_pen, secondary_pen)

        # Now draw the markers
        for m in self.markers:
            if m.location != -1:
                x = self.getXPosition(self.data[m.location])
                y_db = self.getDBYPosition(self.data[m.location])
                y_phase = self.getPhaseYPosition(self.data[m.location])

                self.drawMarker(x, y_db, qp, m.color, self.markers.index(m) + 1)
                self.drawMarker(x, y_phase, qp, m.color, self.markers.index(m) + 1)

    def _draw_ri_labels(self, qp: QtGui.QPainter, is_reference=False) -> None:
        c1, c2 = (
            (
                QtGui.QColor(Chart.color.sweep),
                QtGui.QColor(Chart.color.sweep_secondary),
            )
            if not is_reference
            else (
                QtGui.QColor(Chart.color.reference),
                QtGui.QColor(Chart.color.reference_secondary),
            )
        )
        y = 9 if not is_reference else 14
        c1.setAlpha(255)
        c2.setAlpha(255)
        pen = QtGui.QPen(c1)
        pen.setWidth(4)
        qp.setPen(pen)
        qp.drawLine(20, y, 25, y)

        pen.setColor(c2)
        qp.setPen(pen)
        qp.drawLine(
            self.leftMargin + self.dim.width,
            y,
            self.leftMargin + self.dim.width + 5,
            y,
        )

    def _draw_ref_data(self, qp, line_pen, primary_pen, secondary_pen):
        for i, reference in enumerate(self.reference):
            if reference.freq < self.fstart or reference.freq > self.fstop:
                continue
            x = self.getXPosition(reference)
            y_db = self.getDBYPosition(reference)
            y_phase = self.getPhaseYPosition(reference)
            qp.setPen(primary_pen)
            if self.isPlotable(x, y_db):
                qp.drawPoint(x, y_db)
            qp.setPen(secondary_pen)
            if self.isPlotable(x, y_phase):
                qp.drawPoint(x, y_phase)
            if self.flag.draw_lines and i > 0:
                prev_x = self.getXPosition(self.reference[i - 1])
                prev_y_db = self.getDBYPosition(self.reference[i - 1])
                prev_y_phase = self.getPhaseYPosition(self.reference[i - 1])

                # Real part first
                line_pen.setColor(Chart.color.reference)
                self._draw_line(qp, line_pen, (x, y_db), (prev_x, prev_y_db))

                # Imag part second
                line_pen.setColor(Chart.color.reference_secondary)
                self._draw_line(qp, line_pen, (x, y_phase), (prev_x, prev_y_phase))

    def _draw_data(self, qp, line_pen, primary_pen, secondary_pen) -> None:
        for i, data in enumerate(self.data):
            x = self.getXPosition(data)
            y_db = self.getDBYPosition(data)
            y_phase = self.getPhaseYPosition(data)
            qp.setPen(primary_pen)
            if self.isPlotable(x, y_db):
                qp.drawPoint(x, y_db)
            qp.setPen(secondary_pen)
            if self.isPlotable(x, y_phase):
                qp.drawPoint(x, y_phase)
            if self.flag.draw_lines and i > 0:
                prev_x = self.getXPosition(self.data[i - 1])
                prev_y_db = self.getDBYPosition(self.data[i - 1])
                prev_y_phase = self.getPhaseYPosition(self.data[i - 1])

                # Real part first
                line_pen.setColor(Chart.color.sweep)
                self._draw_line(
                    qp,
                    line_pen,
                    ChartPosition(x, y_db),
                    ChartPosition(prev_x, prev_y_db),
                )

                # Imag part second
                line_pen.setColor(Chart.color.sweep_secondary)
                self._draw_line(
                    qp,
                    line_pen,
                    ChartPosition(x, y_phase),
                    ChartPosition(prev_x, prev_y_phase),
                )

    def _draw_line(
        self, qp, line_pen, p: ChartPosition, prev_p: ChartPosition
    ) -> None:
        x, y = p
        prev_x, prev_y = prev_p
        qp.setPen(line_pen)
        if self.isPlotable(x, y):
            if self.isPlotable(prev_x, prev_y):
                qp.drawLine(x, y, prev_x, prev_y)
            else:
                new_x, new_y = self.getPlotable(x, y, prev_x, prev_y)
                qp.drawLine(x, y, new_x, new_y)
        elif self.isPlotable(prev_x, prev_y):
            new_x, new_y = self.getPlotable(prev_x, prev_y, x, y)
            qp.drawLine(prev_x, prev_y, new_x, new_y)

    def drawHorizontalTicks(self, qp):
        # We want one horizontal tick per 50 pixels, at most
        fmt = Format(max_nr_digits=3)
        horizontal_ticks = self.dim.height // 50
        for i in range(horizontal_ticks):
            y = self.topMargin + i * self.dim.height // horizontal_ticks
            qp.setPen(QtGui.QPen(Chart.color.foreground))
            qp.drawLine(
                self.leftMargin - 5, y, self.leftMargin + self.dim.width + 5, y
            )
            qp.setPen(QtGui.QPen(Chart.color.text))
            db = self.max_db - i * self.span_db / horizontal_ticks
            phase = self.max_phase - i * self.span_phase / horizontal_ticks
            qp.drawText(3, y + 4, f"{Value(db, fmt=fmt)}")
            qp.drawText(
                self.leftMargin + self.dim.width + 8,
                y + 4,
                f"{Value(phase, fmt=fmt)}",
            )

    def find_scaling(self):
        # Find scaling
        if self.fixedValues:
            min_db = self.minDisplayDB
            max_db = self.maxDisplayDB
            min_phase = self.minDisplayPhase
            max_phase = self.maxDisplayPhase
            return min_db, max_db, min_phase, max_phase

        min_db = 1000
        min_phase = 1000
        max_db = -1000
        max_phase = -1000
        for d in self.data:
            imp = self.value(d)
            re, im = imp.real, imp.imag
            db = self.logMag(d)
            phase = math.degrees(d.phase)
            # phase = math.atan(im/re) 
            if math.isinf(re):  # Avoid infinite scales
                continue
            max_db = max(max_db, db)
            min_db = min(min_db, db)
            max_phase = max(max_phase, phase)
            min_phase = min(min_phase, phase)
        # Also check min/max for the reference sweep
        for d in self.reference:
            if d.freq < self.fstart or d.freq > self.fstop:
                continue
            imp = self.value(d)
            re, im = imp.real, imp.imag
            db = self.logMag(d)
            phase = math.degrees(d.phase)
            if math.isinf(re):  # Avoid infinite scales
                continue
            max_db = max(max_db, db)
            min_db = min(min_db, db)
            max_phase = max(max_phase, phase)
            min_phase = min(min_phase, phase)
        # Always have at least 8 numbered horizontal lines
        max_db = math.ceil(max_db)
        try:
            min_db = math.floor(min_db)
        except OverflowError:
            min_db=-100
        max_phase = math.ceil(max_phase)
        min_phase = math.floor(min_phase)
        # min_phase, max_phase = self.imag_scaling_constraints(min_phase, max_phase)
        return min_db, max_db, min_phase, max_phase

    def imag_scaling_constraints(self, min_imag, max_imag):
        if max_imag - min_imag < 8:
            missing = 8 - (max_imag - min_imag)
            max_imag += math.ceil(missing / 2)
            min_imag -= math.floor(missing / 2)

        if 0 > max_imag > -2:
            max_imag = 0
        if 0 < min_imag < 2:
            min_imag = 0

        if (max_imag - min_imag) > 8 and min_imag < 0 < max_imag:
            # We should show a "0" line for the reactive part
            span = max_imag - min_imag
            step_size = span / 8
            if max_imag < step_size:
                # The 0 line is the first step after the top.
                # Scale accordingly.
                max_imag = -min_imag / 7
            elif -min_imag < step_size:
                # The 0 line is the last step before the bottom.
                # Scale accordingly.
                min_imag = -max_imag / 7
            else:
                # Scale max_imag to be a whole factor of min_imag
                num_min = math.floor(min_imag / step_size * -1)
                num_max = 8 - num_min
                max_imag = num_max * (min_imag / num_min) * -1
        return min_imag, max_imag

    def getPhaseYPosition(self, d: Datapoint) -> int:
        imp = self.value(d)
        re, im = imp.real, imp.imag
        db = self.logMag(d)
        phase = math.degrees(d.phase)
        return int(
            self.topMargin
            + (self.max_phase - phase) / self.span_phase * self.dim.height
        )

    def getDBYPosition(self, d: Datapoint) -> int:
        imp = self.value(d)
        re, im = imp.real, imp.imag
        db = self.logMag(d)
        phase = math.degrees(d.phase)
        return int(
            self.topMargin
            + (self.max_db - db) / self.span_db * self.dim.height
            if math.isfinite(db)
            else self.topMargin
        )

    def valueAtPosition(self, y) -> list[float]:
        absy = y - self.topMargin
        valDB = -1 * ((absy / self.dim.height * self.span_db) - self.max_db)
        valPhase = -1 * ((absy / self.dim.height * self.span_phase) - self.max_phase)
        return [valDB, valPhase]

    def zoomTo(self, x1, y1, x2, y2):
        val1 = self.valueAtPosition(y1)
        val2 = self.valueAtPosition(y2)

        if len(val1) == len(val2) == 2 and val1[0] != val2[0]:
            self.minDisplayDB = round(min(val1[0], val2[0]), 2)
            self.maxDisplayDB = round(max(val1[0], val2[0]), 2)
            self.minDisplayPhase = round(min(val1[1], val2[1]), 2)
            self.maxDisplayPhase = round(max(val1[1], val2[1]), 2)
            self.setFixedValues(True)

        freq1 = max(1, self.frequencyAtPosition(x1, limit=False))
        freq2 = max(1, self.frequencyAtPosition(x2, limit=False))

        if freq1 > 0 and freq2 > 0 and freq1 != freq2:
            self.minFrequency = min(freq1, freq2)
            self.maxFrequency = max(freq1, freq2)
            self.setFixedSpan(True)

        self.update()

    def getNearestMarker(self, x, y) -> Marker | None:
        if not self.data:
            return None
        shortest = 10e6
        nearest = None
        for m in self.markers:
            mx, _ = self.getPosition(self.data[m.location])
            mydb = self.getDBYPosition(self.data[m.location])
            myphase = self.getPhaseYPosition(self.data[m.location])
            dx = abs(x - mx)
            dy = min(abs(y - mydb), abs(y - myphase))
            distance = math.sqrt(dx**2 + dy**2)
            if distance < shortest:
                shortest = distance
                nearest = m
        return nearest

    def setMinimumDBValue(self):
        min_val, selected = QtWidgets.QInputDialog.getDouble(
            self,
            "Minimum DB value",
            "Set minimum DB value",
            value=self.minDisplayDB,
            decimals=2,
        )
        if not selected:
            return
        if not (self.fixedValues and min_val >= self.maxDisplayDB):
            self.minDisplayDB = min_val
        if self.fixedValues:
            self.update()

    def setMaximumDBValue(self):
        max_val, selected = QtWidgets.QInputDialog.getDouble(
            self,
            "Maximum DB value",
            "Set maximum DB value",
            value=self.maxDisplayDB,
            decimals=2,
        )
        if not selected:
            return
        if not (self.fixedValues and max_val <= self.minDisplayDB):
            self.maxDisplayDB = max_val
        if self.fixedValues:
            self.update()

    def setMinimumPhaseValue(self):
        min_val, selected = QtWidgets.QInputDialog.getDouble(
            self,
            "Minimum phase value",
            "Set minimum phase value",
            value=self.minDisplayPhase,
            decimals=2,
        )
        if not selected:
            return
        if not (self.fixedValues and min_val >= self.maxDisplayPhase):
            self.minDisplayPhase = min_val
        if self.fixedValues:
            self.update()

    def setMaximumPhaseValue(self):
        max_val, selected = QtWidgets.QInputDialog.getDouble(
            self,
            "Maximum phase value",
            "Set maximum phase value",
            value=self.maxDisplayPhase,
            decimals=2,
        )
        if not selected:
            return
        if not (self.fixedValues and max_val <= self.minDisplayPhase):
            self.maxDisplayPhase = max_val
        if self.fixedValues:
            self.update()

    def setFixedValues(self, fixed_values: bool):
        self.fixedValues = fixed_values
        if fixed_values and (
            self.minDisplayDB >= self.maxDisplayDB
            or self.minDisplayPhase > self.maxDisplayPhase
        ):
            self.fixedValues = False
            self.y_action_automatic.setChecked(True)
            self.y_action_fixed_span.setChecked(False)
        self.update()

    def contextMenuEvent(self, event):
        self.action_set_fixed_start.setText(
            f"Start ({format_frequency_chart(self.minFrequency)})"
        )
        self.action_set_fixed_stop.setText(
            f"Stop ({format_frequency_chart(self.maxFrequency)})"
        )
        self.action_set_fixed_minimum_db.setText(
            f"Minimum R ({self.minDisplayDB})"
        )
        self.action_set_fixed_maximum_db.setText(
            f"Maximum R ({self.maxDisplayDB})"
        )
        self.action_set_fixed_minimum_phase.setText(
            f"Minimum jX ({self.minDisplayPhase})"
        )
        self.action_set_fixed_maximum_phase.setText(
            f"Maximum jX ({self.maxDisplayPhase})"
        )
        self.menu.exec(event.globalPos())


    def value(self, p: Datapoint) -> complex:
        return self.impedance(p)

    def impedance(self, p: Datapoint) -> complex:
        return p.impedance()

    
    def logMag(self, p: Datapoint) -> float:
        return -p.gain if self.isInverted else p.gain

    def drawChart(self, qp: QtGui.QPainter):
        qp.setPen(QtGui.QPen(Chart.color.text))
        qp.drawText(self.leftMargin + 5, 15, f"{self.name} (dB, deg)")
        qp.drawText(10, 15, "dB")
        qp.drawText(self.leftMargin + self.dim.width + 10, 15, "Phase")
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
