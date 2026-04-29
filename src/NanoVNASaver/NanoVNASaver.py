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
import threading
import warnings
from time import localtime, strftime
import skrf as rf
import numpy as np
from skrf.calibration import SOLT
from skrf.constants import c as RF_C
from skrf.frequency import InvalidFrequencyWarning
from skrf.media.media import DefinedGammaZ0
from skrf.network import two_port_reflect

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from .About import VERSION
from .Calibration import Calibration
from .Charts import (
    CapacitanceChart,
    CombinedLogMagChart,
    GroupDelayChart,
    InductanceChart,
    LogMagChart,
    LogMagPhaseChart,
    MagnitudeChart,
    MagnitudeZChart,
    MagnitudeZSeriesChart,
    MagnitudeZShuntChart,
    PermeabilityChart,
    PhaseChart,
    PolarChart,
    QualityFactorChart,
    RealImaginaryMuChart,
    RealImaginaryZChart,
    RealImaginaryZSeriesChart,
    RealImaginaryZShuntChart,
    SmithChart,
    SParameterChart,
    TDRChart,
    VSWRChart,
)
from .Charts.Chart import Chart
from .Controls.MarkerControl import MarkerControl
from .Controls.SerialControl import SerialControl
from .Controls.SweepControl import SweepControl
from .Defaults import APP_SETTINGS, AppSettings, get_app_config
from .FileDialogs import get_existing_directory
from .Formatting import format_frequency, format_gain, format_vswr
from .Hardware.Hardware import Interface
from .Hardware.VNA import VNA
from .Marker.Delta import DeltaMarker
from .Marker.Widget import Marker
from .RFTools import corr_att_data
from .Settings.Bands import BandsModel
from .Settings.Sweep import Sweep
from .SweepWorker import SweepWorker
from .Touchstone import Touchstone
from .Windows import (
    AboutWindow,
    AnalysisWindow,
    CalibrationWindow,
    DeviceSettingsWindow,
    DisplaySettingsWindow,
    FilesWindow,
    SweepSettingsWindow,
    TDRWindow,
)
from .Windows.ui import get_window_icon
####20260302 add
import os
from dataclasses import dataclass
####


from .temperature_logging import TemperatureLoggingManager

logger = logging.getLogger(__name__)

WORKING_KILL_TIME_MS = 10 * 1000
MIN_WINDOW_WIDTH = 800
MIN_WINDOW_HEIGHT = 600


def get_S2P_nVNA(filename):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InvalidFrequencyWarning)
        ntwk = rf.Network(filename)
    ntwk = make_full_thru_network(ntwk)
    ntwk = average_duplicate_freq(ntwk)
    return ntwk

def average_duplicate_freq(ntwk: rf.Network) -> rf.Network:
    f = ntwk.frequency.f
    idx_sort = np.argsort(f)
    f = f[idx_sort]
    s = ntwk.s[idx_sort]
    # z0 = ntwk.z0[idx_sort]

    uf, inv = np.unique(f, return_inverse=True)
    s_out = np.zeros((len(uf),) + s.shape[1:], dtype=s.dtype)
    # z0_out = np.zeros((len(uf),) + z0.shape[1:], dtype=z0.dtype)

    for k in range(len(uf)):
        m = (inv == k)
        s_out[k] = s[m].mean(axis=0)
        # z0_out[k] = z0[m].mean(axis=0)

    return rf.Network(
        frequency=rf.Frequency.from_f(uf, "Hz"),
        s=s_out,
        z0=50,
        name=ntwk.name
    )

def make_full_thru_network(ntwk):
    """Return a copy of `ntwk` with full 2x2 S-parameters."""
    s = ntwk.s.copy()
    n = s.shape[0]
    s_full = np.zeros((n, 2, 2), dtype=complex)
    # extract available elements (fall back to zeros if missing)
    s11 = s[:, 0, 0] if s.shape[1] >= 2 else np.zeros(n, dtype=complex)
    s21 = s[:, 1, 0] if s.shape[1] >= 2 else np.zeros(n, dtype=complex)
    # assign symmetric/full values: S22 = S11, S12 = S21
    s_full[:, 0, 0] = s11
    s_full[:, 1, 1] = s11
    s_full[:, 1, 0] = s21
    s_full[:, 0, 1] = s21
    nt = ntwk.copy()
    nt.s = s_full
    return nt


# calpath = '/home/keisuke/Documents/Spara/caldata/nanoVNAH4/20260325_nanoVNAH4_nocal_IF10Hz/'
calpath = '/home/keisuke/Documents/Spara/caldata/nanoVNAH4/20260424_nanoVNAH4_DCcut/'
datalist = ['short','open','load','thru']
caldata = {}
for k in datalist:
    caldata[k] = {'ntwk': get_S2P_nVNA(f'{calpath}S_{k}.s2p')}

freq = caldata['short']['ntwk'].frequency
beta = 2*np.pi*freq.f / RF_C   # 自由空間の β（簡易）
media = DefinedGammaZ0(frequency=freq, z0=50, gamma=1j*beta)

ideals = [media.short(nports=2), media.open(nports=2), media.match(nports=2), media.thru()]

measured_thru  = caldata['thru']['ntwk']
short_meas_2p = two_port_reflect(caldata['short']['ntwk'].s11, caldata['short']['ntwk'].s11)
open_meas_2p  = two_port_reflect(caldata['open']['ntwk'].s11,  caldata['open']['ntwk'].s11)
load_meas_2p  = two_port_reflect(caldata['load']['ntwk'].s11,  caldata['load']['ntwk'].s11)
measured = [short_meas_2p, open_meas_2p,load_meas_2p,measured_thru]# Idealsと同じ順番でリストにする

cal = SOLT(
    ideals=ideals,
    measured=measured,
    n_ports=2,  # 2ポート校正であることを明示
    # isolation=data['iso']['ntwk']
        )






class Communicate(QObject):
    data_available = QtCore.Signal()

@dataclass
class BatchSweepConfig:
    out_dir: str
    base_name: str
    rest_s: float
    count: int
    save_temperature: bool = True

class BatchSweepDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, default_dir: str = "", default_base: str = "sweep"):
        super().__init__(parent)
        self.setWindowTitle("Batch sweep setup")

        layout = QtWidgets.QFormLayout(self)

        # Directory
        dir_row = QtWidgets.QHBoxLayout()
        self.dir_edit = QtWidgets.QLineEdit(default_dir)
        btn_browse = QtWidgets.QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(btn_browse)
        layout.addRow("Save directory:", dir_row)

        # Base name
        self.base_edit = QtWidgets.QLineEdit(default_base)
        layout.addRow("Base filename:", self.base_edit)

        # Rest time
        self.rest_spin = QtWidgets.QDoubleSpinBox()
        self.rest_spin.setRange(0.0, 3600.0 * 24)
        self.rest_spin.setDecimals(3)
        self.rest_spin.setValue(1.0)
        self.rest_spin.setSuffix(" s")
        layout.addRow("Rest time:", self.rest_spin)

        # Count
        self.count_spin = QtWidgets.QSpinBox()
        self.count_spin.setRange(1, 10_000_000)
        self.count_spin.setValue(30)
        layout.addRow("Number of sweeps:", self.count_spin)

        # Temperature logging
        self.temp_checkbox = QtWidgets.QCheckBox("Save temperature log")
        self.temp_checkbox.setChecked(True)
        layout.addRow("Temperature:", self.temp_checkbox)

        # Buttons
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)        

    # def _browse_dir(self):
    #     d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select directory", self.dir_edit.text() or os.path.expanduser("~"))
    #     if d:
    #         self.dir_edit.setText(d)

    def _browse_dir(self):
        start_dir = self.dir_edit.text() or os.path.expanduser("~")

        d = get_existing_directory(
            self,
            "Select directory",
            start_dir,
        )

        if d:
            self.dir_edit.setText(d)

    def get_config(self) -> "BatchSweepConfig":
        out_dir = self.dir_edit.text().strip()
        base = self.base_edit.text().strip() or "sweep"
        rest_s = float(self.rest_spin.value())
        count = int(self.count_spin.value())
        # return BatchSweepConfig(out_dir=out_dir, base_name=base, rest_s=rest_s, count=count)
        save_temperature = self.temp_checkbox.isChecked()
        return BatchSweepConfig(
                out_dir=out_dir,
                base_name=base,
                rest_s=rest_s,
                count=count,
                save_temperature=save_temperature,
        )
    
class NanoVNASaver(QWidget):
    version = VERSION
    scale_factor = 1.0

    def __init__(self) -> None:
        super().__init__()
        self.communicate = Communicate()
        self.s21att = 0.0
        self.setWindowIcon(get_window_icon())
        # TODO APP_SETTINGS should be used instead app.setting\
        self.settings: AppSettings = APP_SETTINGS
        app_config = self.settings.restore_config()
        self.threadpool = QtCore.QThreadPool()
        self.sweep = Sweep()
        self.worker = SweepWorker(self)

        self.worker.signals.updated.connect(self.dataUpdated)
        self.worker.signals.finished.connect(self.sweepFinished)

        self.worker.signals.finished.connect(self._on_sweep_finished_for_batch)  

        self.worker.signals.sweep_error.connect(self.showSweepError)

        self.markers: list[Marker] = []
        self.marker_ref = False

        self.marker_column = QtWidgets.QVBoxLayout()
        self.marker_frame = QtWidgets.QFrame()
        self.marker_column.setContentsMargins(0, 0, 0, 0)
        self.marker_frame.setLayout(self.marker_column)

        self.interface = Interface("serial", "None")
        self.vna: VNA = VNA(self.interface)

        self.calibration: Calibration = Calibration()
        self.sweep_control = SweepControl(self)
        self.marker_control = MarkerControl(self)
        self.serial_control = SerialControl(self)

        # ---- Sweep snapshot 初期化 ----
        self._sweep_snapshot = self.sweep.copy()

        # ---- connect_device をラップ（接続直前に保存）----
        _orig_connect_device = self.serial_control.connect_device

        def _connect_device_wrapped(*args, **kwargs):
            self._sweep_snapshot = self.sweep.copy()
            return _orig_connect_device(*args, **kwargs)

        self.serial_control.connect_device = _connect_device_wrapped

        # ---- 接続後に sweep を復元 ----
        self.serial_control.connected.connect(
            self._restore_all_sweep_after_connect
        )

        # 既存のボタン更新も残す
        self.serial_control.connected.connect(
            self.sweep_control.update_sweep_btn
        )

        self.bands: BandsModel = BandsModel()

        self.dataLock = threading.Lock()
        self.data: Touchstone = Touchstone()
        self.ref_data: Touchstone = Touchstone()

        # Batch sweep state
        self._batch_active = False
        self._batch_cfg: BatchSweepConfig | None = None
        self._batch_index = 0
        self._batch_last_sweep_failed = False
        self._batch_last_error_message = ""

        self.temperature_logger = TemperatureLoggingManager(
            pico_port="/dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_e664a836a33ba327-if00",
            csv_path=""
        )

        self.sweepSource = ""
        self.referenceSource = ""

        logger.debug("Building user interface")

        self.baseTitle = f"NanoVNA Saver {NanoVNASaver.version}"
        self.updateTitle()
        layout = QtWidgets.QBoxLayout(
            QtWidgets.QBoxLayout.Direction.LeftToRight
        )

        scrollarea = QtWidgets.QScrollArea()
        outer = QtWidgets.QVBoxLayout()
        outer.addWidget(scrollarea)
        self.setLayout(outer)
        scrollarea.setWidgetResizable(True)
        self.resize(*self._safe_initial_window_size(app_config))
        scrollarea.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.MinimumExpanding,
            QtWidgets.QSizePolicy.Policy.MinimumExpanding,
        )
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.MinimumExpanding,
            QtWidgets.QSizePolicy.Policy.MinimumExpanding,
        )
        widget = QWidget()
        widget.setLayout(layout)
        scrollarea.setWidget(widget)

        self.charts = {
            "s11": {
                "capacitance": CapacitanceChart("S11 Serial C"),
                "group_delay": GroupDelayChart("S11 Group Delay"),
                "inductance": InductanceChart("S11 Serial L"),
                "log_mag": LogMagChart("S11 Return Loss"),
                "db_phase": LogMagPhaseChart("S11 db/phase"),
                "magnitude": MagnitudeChart("|S11|"),
                "magnitude_z": MagnitudeZChart("S11 |Z|"),
                "permeability": PermeabilityChart(
                    "S11 R/\N{GREEK SMALL LETTER OMEGA} &"
                    " X/\N{GREEK SMALL LETTER OMEGA}"
                ),
                "phase": PhaseChart("S11 Phase"),
                "q_factor": QualityFactorChart("S11 Quality Factor"),
                "real_imag": RealImaginaryZChart("S11 R+jX"),
                "real_imag_mu": RealImaginaryMuChart(
                    "S11 \N{GREEK SMALL LETTER MU}"
                ),
                "smith": SmithChart("S11 Smith Chart"),
                "s_parameter": SParameterChart("S11 Real/Imaginary"),
                "vswr": VSWRChart("S11 VSWR"),
                "sa_dbm": LogMagChart("Signal Analyser dBm"),
            },
            "s21": {
                "group_delay": GroupDelayChart(
                    "S21 Group Delay", reflective=False
                ),
                "log_mag": LogMagChart("S21 Gain"),
                "db_phase": LogMagPhaseChart("S21 db/phase"),
                "magnitude": MagnitudeChart("|S21|"),
                "magnitude_z_shunt": MagnitudeZShuntChart("S21 |Z| shunt"),
                "magnitude_z_series": MagnitudeZSeriesChart("S21 |Z| series"),
                "real_imag_shunt": RealImaginaryZShuntChart("S21 R+jX shunt"),
                "real_imag_series": RealImaginaryZSeriesChart(
                    "S21 R+jX series"
                ),
                "phase": PhaseChart("S21 Phase"),
                "polar": PolarChart("S21 Polar Plot"),
                "s_parameter": SParameterChart("S21 Real/Imaginary"),
            },
            "combined": {
                "log_mag": CombinedLogMagChart("S11 & S21 LogMag"),
            },
            "calibration": {
                "S11_db_phase": LogMagPhaseChart("Cal S11 db/phase"),
                "S21_db_phase": LogMagPhaseChart("Cal S21 db/phase"),
                "real_imag_shunt": RealImaginaryZShuntChart("Cal S21 R+jX shunt"),
            },
        }
        self.tdr_chart: TDRChart = TDRChart("TDR")
        self.tdr_mainwindow_chart = TDRChart("TDR")

        # List of all the S11 charts, for selecting
        self.s11charts = list(self.charts["s11"].values())

        # List of all the S21 charts, for selecting
        self.s21charts = list(self.charts["s21"].values())

        # List of all charts that use both S11 and S21
        self.combinedCharts = list(self.charts["combined"].values())

        # List of all charts that use both S11 and S21
        self.calibrationCharts = list(self.charts["calibration"].values())

        # List of all charts that can be selected for display
        self.selectable_charts = (
            self.s11charts
            + self.s21charts
            + self.combinedCharts
            + self.calibrationCharts
            + [
                self.tdr_mainwindow_chart,
            ]
        )

        # List of all charts that subscribe to updates (including duplicates!)
        self.subscribing_charts = []
        self.subscribing_charts.extend(self.selectable_charts)
        self.subscribing_charts.append(self.tdr_chart)

        for c in self.subscribing_charts:
            c.popout_requested.connect(self.popoutChart)

        self.charts_layout = QtWidgets.QGridLayout()

        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Q"), self, self.close)

        ###############################################################
        #  Create main layout
        ###############################################################

        left_column = QtWidgets.QVBoxLayout()
        right_column = QtWidgets.QVBoxLayout()
        right_column.addLayout(self.charts_layout)
        self.marker_frame.setHidden(app_config.gui.markers_hidden)
        chart_widget = QWidget()
        chart_widget.setLayout(right_column)
        self.splitter = QtWidgets.QSplitter()
        self.splitter.addWidget(self.marker_frame)
        self.splitter.addWidget(chart_widget)

        if not self.splitter.restoreState(app_config.gui.splitter_sizes):
            self.splitter.setSizes([220, 900])

        layout.addLayout(left_column)
        layout.addWidget(self.splitter, 2)

        ###############################################################
        #  Windows
        ###############################################################

        self.windows: dict[str, QtWidgets.QDialog] = {
            "about": AboutWindow(self),
            "analysis": AnalysisWindow(self),
            "calibration": CalibrationWindow(self),
            "device_settings": DeviceSettingsWindow(self),
            "file": FilesWindow(self),
            "sweep_settings": SweepSettingsWindow(self),
            "setup": DisplaySettingsWindow(self),
            "tdr": TDRWindow(self),
        }

        ###############################################################
        #  Sweep control
        ###############################################################

        left_column.addWidget(self.sweep_control)

        # Batch sweep buttons
        batch_box = QtWidgets.QGroupBox()
        batch_box.setTitle("Batch sweep")
        batch_layout = QtWidgets.QVBoxLayout(batch_box)

        self.btn_batch_start = QtWidgets.QPushButton("Batch save ...")
        self.btn_batch_start.setMinimumHeight(20)
        self.btn_batch_start.clicked.connect(self.batch_setup_and_start)

        self.btn_batch_stop = QtWidgets.QPushButton("Stop batch")
        self.btn_batch_stop.setMinimumHeight(20)
        self.btn_batch_stop.setDisabled(True)
        self.btn_batch_stop.clicked.connect(self.batch_stop)

        batch_layout.addWidget(self.btn_batch_start)
        batch_layout.addWidget(self.btn_batch_stop)
        left_column.addWidget(batch_box)

        # ###############################################################
        #  Marker control
        ###############################################################

        left_column.addWidget(self.marker_control)

        for c in self.subscribing_charts:
            c.setMarkers(self.markers)
            c.setBands(self.bands)

        self.marker_data_layout = QtWidgets.QVBoxLayout()
        self.marker_data_layout.setContentsMargins(0, 0, 0, 0)

        for m in self.markers:
            self.marker_data_layout.addWidget(m.get_data_layout())

        scroll2 = QtWidgets.QScrollArea()
        scroll2.setWidgetResizable(True)
        scroll2.setVisible(True)

        widget2 = QWidget()
        widget2.setLayout(self.marker_data_layout)
        scroll2.setWidget(widget2)
        self.marker_column.addWidget(scroll2)

        # init delta marker (but assume only one marker exists)
        self.delta_marker = DeltaMarker("Delta Marker 2 - Marker 1")
        self.delta_marker_layout = self.delta_marker.get_data_layout()
        self.delta_marker_layout.hide()
        self.marker_column.addWidget(self.delta_marker_layout)

        ###############################################################
        #  Statistics/analysis
        ###############################################################

        s11_control_box = QtWidgets.QGroupBox()
        s11_control_box.setTitle("S11")
        s11_control_layout = QtWidgets.QFormLayout()
        s11_control_layout.setVerticalSpacing(0)
        s11_control_box.setLayout(s11_control_layout)

        self.s11_min_swr_label = QtWidgets.QLabel()
        s11_control_layout.addRow("Min VSWR:", self.s11_min_swr_label)
        self.s11_min_rl_label = QtWidgets.QLabel()
        s11_control_layout.addRow("Return loss:", self.s11_min_rl_label)

        self.marker_column.addWidget(s11_control_box)

        s21_control_box = QtWidgets.QGroupBox()
        s21_control_box.setTitle("S21")
        s21_control_layout = QtWidgets.QFormLayout()
        s21_control_layout.setVerticalSpacing(0)
        s21_control_box.setLayout(s21_control_layout)

        self.s21_min_gain_label = QtWidgets.QLabel()
        s21_control_layout.addRow("Min gain:", self.s21_min_gain_label)

        self.s21_max_gain_label = QtWidgets.QLabel()
        s21_control_layout.addRow("Max gain:", self.s21_max_gain_label)

        self.marker_column.addWidget(s21_control_box)

        # self.marker_column.addStretch(1)

        btn_show_analysis = QtWidgets.QPushButton("Analysis ...")
        btn_show_analysis.setMinimumHeight(20)
        btn_show_analysis.clicked.connect(
            lambda: self.display_window("analysis")
        )
        self.marker_column.addWidget(btn_show_analysis)

        ###############################################################
        # TDR
        ###############################################################

        self.tdr_chart.tdrWindow = self.windows["tdr"]
        self.tdr_mainwindow_chart.tdrWindow = self.windows["tdr"]
        self.windows["tdr"].updated.connect(self.tdr_chart.update)
        self.windows["tdr"].updated.connect(self.tdr_mainwindow_chart.update)

        tdr_control_box = QtWidgets.QGroupBox()
        tdr_control_box.setTitle("TDR")
        tdr_control_layout = QtWidgets.QFormLayout()
        tdr_control_box.setLayout(tdr_control_layout)

        self.tdr_result_label = QtWidgets.QLabel()
        self.tdr_result_label.setMinimumHeight(20)
        tdr_control_layout.addRow(
            "Estimated cable length:", self.tdr_result_label
        )

        self.tdr_button = QtWidgets.QPushButton("Time Domain Reflectometry ...")
        self.tdr_button.setMinimumHeight(20)
        self.tdr_button.clicked.connect(lambda: self.display_window("tdr"))

        tdr_control_layout.addRow(self.tdr_button)

        left_column.addWidget(tdr_control_box)

        ###############################################################
        #  Spacer
        ###############################################################

        left_column.addSpacerItem(
            QtWidgets.QSpacerItem(
                1,
                1,
                QtWidgets.QSizePolicy.Policy.Fixed,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
        )

        ###############################################################
        #  Reference control
        ###############################################################

        reference_control_box = QtWidgets.QGroupBox()
        reference_control_box.setTitle("Reference sweep")
        reference_control_layout = QtWidgets.QFormLayout(reference_control_box)

        btn_set_reference = QtWidgets.QPushButton("Set current as reference")
        btn_set_reference.setMinimumHeight(20)
        btn_set_reference.clicked.connect(self.setReference)
        self.btnResetReference = QtWidgets.QPushButton("Reset reference")
        self.btnResetReference.setMinimumHeight(20)
        self.btnResetReference.clicked.connect(self.resetReference)
        self.btnResetReference.setDisabled(True)

        reference_control_layout.addRow(btn_set_reference)
        reference_control_layout.addRow(self.btnResetReference)

        left_column.addWidget(reference_control_box)

        ###############################################################
        #  Serial control
        ###############################################################

        left_column.addWidget(self.serial_control)

        ###############################################################
        #  Calibration
        ###############################################################

        btnOpenCalibrationWindow = QtWidgets.QPushButton("Calibration ...")
        btnOpenCalibrationWindow.setMinimumHeight(20)
        self.calibrationWindow = CalibrationWindow(self)
        btnOpenCalibrationWindow.clicked.connect(
            lambda: self.display_window("calibration")
        )

        ###############################################################
        #  Display setup
        ###############################################################

        btn_display_setup = QtWidgets.QPushButton("Display setup ...")
        btn_display_setup.setMinimumHeight(20)
        btn_display_setup.clicked.connect(lambda: self.display_window("setup"))

        btn_about = QtWidgets.QPushButton("About ...")
        btn_about.setMinimumHeight(20)

        btn_about.clicked.connect(lambda: self.display_window("about"))

        btn_open_file_window = QtWidgets.QPushButton("Files ...")
        btn_open_file_window.setMinimumHeight(20)

        btn_open_file_window.clicked.connect(
            lambda: self.display_window("file")
        )

        button_grid = QtWidgets.QGridLayout()
        button_grid.addWidget(btn_open_file_window, 0, 0)
        button_grid.addWidget(btnOpenCalibrationWindow, 0, 1)
        button_grid.addWidget(btn_display_setup, 1, 0)
        button_grid.addWidget(btn_about, 1, 1)
        left_column.addLayout(button_grid)

        logger.debug("Finished building interface")

        # self.temperature_logger = TemperatureLoggingManager(
        #     pico_port="/dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_e664a836a33ba327-if00",
        #     csv_path=""
        # )

        # self.temperature_logging_enabled = True

        

    def _safe_initial_window_size(self, app_config) -> tuple[int, int]:
        default = self.sizeHint()
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            max_width = max(default.width(), available.width())
            max_height = max(default.height(), available.height())
        else:
            max_width = 3840
            max_height = 2160

        width = int(app_config.gui.window_width)
        height = int(app_config.gui.window_height)
        if not (MIN_WINDOW_WIDTH <= width <= max_width):
            logger.warning("Ignoring invalid saved window width: %s", width)
            width = min(default.width(), max_width)
        if not (MIN_WINDOW_HEIGHT <= height <= max_height):
            logger.warning("Ignoring invalid saved window height: %s", height)
            height = min(default.height(), max_height)
        return width, height

    def _restore_all_sweep_after_connect(self):
        try:
            if not self.vna or not hasattr(self.vna, "serial") or not self.vna.serial or not self.vna.serial.is_open:
                logger.warning("Skip restoring sweep: serial not open yet")
                return
            # 1) アプリ側 sweep を丸ごと復元（segments / averaging / log sweep など含む）
            self.sweep = self._sweep_snapshot.copy()

            # 2) デバイス側に周波数レンジだけは必ず再適用（points設定APIは無いので不要）
            self.vna.resetSweep(int(self.sweep.start), int(self.sweep.end))
            # IF bandwidth を強制設定
            self.vna.bandwidth = 100

            # 3) UI側も復元した sweep に揃える（存在する関数だけ呼ぶ）
            for name in ("updateInputs", "sync_from_sweep", "set_sweep", "update_from_sweep"):
                if hasattr(self.sweep_control, name):
                    getattr(self.sweep_control, name)()
                    break

        except Exception as e:
            logger.warning("Failed to restore sweep after connect: %s", e)

    def auto_connect(
        self,
    ):  # connect if there is exactly one detected serial device
        if self.serial_control.inp_port.count() == 1:
            self.serial_control.connect_device()

    def _sweep_control(self, start: bool = True) -> None:
        self.sweep_control.progress_bar.setValue(0 if start else 100)
        self.sweep_control.btn_start.setDisabled(start)
        self.sweep_control.btn_stop.setDisabled(not start)
        self.sweep_control.toggle_settings(start)

    def sweep_start(self):
        # Run the device data update
        if not self.vna.connected():
            return
        self._sweep_control(start=True)

        for m in self.markers:
            m.resetLabels()
        self.s11_min_rl_label.setText("")
        self.s11_min_swr_label.setText("")
        self.s21_min_gain_label.setText("")
        self.s21_max_gain_label.setText("")
        self.tdr_result_label.setText("")

        logger.debug("Starting worker thread")
        self.worker.start()
        # TODO: Rewrite to make worker a qrunnable with worker signals
        # https://www.pythonguis.com/tutorials/multithreading-pyqt6-applications-qthreadpool/
        # self.threadpool.start(self.worker)

    def saveData(self, data, data21, source=None):
        with self.dataLock:
            self.data.s11 = data
            self.data.s21 = data21
            if self.s21att > 0:
                self.data.s21 = corr_att_data(self.data.s21, self.s21att)
        if source is not None:
            self.sweepSource = source
        else:
            time = strftime("%Y-%m-%d %H:%M:%S", localtime())
            name = self.sweep.properties.name or "nanovna"
            self.sweepSource = f"{name}_{time}"

    def markerUpdated(self, marker: Marker):
        with self.dataLock:
            marker.findLocation(self.data.s11)
            marker.resetLabels()
            marker.updateLabels(self.data.s11, self.data.s21)
            for c in self.subscribing_charts:
                c.update()
        if not self.delta_marker_layout.isHidden():
            m1 = self.markers[0]
            m2 = None
            if self.marker_ref:
                if self.ref_data:
                    m2 = Marker("Reference")
                    m2.location = self.markers[0].location
                    m2.resetLabels()
                    m2.updateLabels(self.ref_data.s11, self.ref_data.s21)
                else:
                    logger.warning("No reference data for marker")

            elif Marker.count() >= 2:
                m2 = self.markers[1]

            if m2 is None:
                logger.error("No data for delta, missing marker or reference")
            else:
                self.delta_marker.set_markers(m1, m2)
                self.delta_marker.resetLabels()
                with contextlib.suppress(IndexError):
                    self.delta_marker.updateLabels()

    def dataUpdated(self):
    
        with self.dataLock:
            s11 = self.data.s11[:]
            s21 = self.data.s21[:]
            ntwk = self.data.to_skrf_network()
            ntwk = make_full_thru_network(ntwk)
            ntwk = average_duplicate_freq(ntwk)
            data_cal = cal.apply_cal(ntwk)
            tscal = Touchstone()   
            tscal.from_skrf_network(data_cal)
            s11_cal = tscal.s11[:]
            s21_cal = tscal.s21[:]
            # print(ntwk.s[:,1,0],s21_cal[0])
            

        for m in self.markers:
            m.resetLabels()
            m.updateLabels(s11, s21)

        for c in self.s11charts:
            c.setData(s11)

        for c in self.s21charts:
            c.setData(s21)

        for c in self.combinedCharts:
            c.setCombinedData(s11, s21)

        # print("raw s11[0] =", s11[0])
        # print("cal s11[0] =", s11_cal[0])
        # print("raw s21[0] =", s21[0])
        # print("cal s21[0] =", s21_cal[0])
        self.calibrationCharts[0].setData(s11_cal)#'S11_db_phase'
        self.calibrationCharts[1].setData(s21_cal)#S21_db_phase
        self.calibrationCharts[2].setData(s21_cal)#real_imag_shunt

        self.sweep_control.progress_bar.setValue(int(self.worker.percentage))
        self.windows["tdr"].updateTDR()

        if s11:
            min_vswr = min(s11, key=lambda data: data.vswr)
            self.s11_min_swr_label.setText(
                f"{format_vswr(min_vswr.vswr)} @"
                f" {format_frequency(min_vswr.freq)}"
            )
            self.s11_min_rl_label.setText(format_gain(min_vswr.gain))
        else:
            self.s11_min_swr_label.setText("")
            self.s11_min_rl_label.setText("")

        if s21:
            min_gain = min(s21, key=lambda data: data.gain)
            max_gain = max(s21, key=lambda data: data.gain)
            self.s21_min_gain_label.setText(
                f"{format_gain(min_gain.gain)}"
                f" @ {format_frequency(min_gain.freq)}"
            )
            self.s21_max_gain_label.setText(
                f"{format_gain(max_gain.gain)}"
                f" @ {format_frequency(max_gain.freq)}"
            )
        else:
            self.s21_min_gain_label.setText("")
            self.s21_max_gain_label.setText("")

        self.updateTitle()
        self.communicate.data_available.emit()

    def sweepFinished(self):
        self._sweep_control(start=False)

        for marker in self.markers:
            marker.frequencyInput.textEdited.emit(marker.frequencyInput.text())

    def setReference(self, s11=None, s21=None, source=None):
        if not s11:
            with self.dataLock:
                s11 = self.data.s11[:]
                s21 = self.data.s21[:]
                ntwk = self.data.to_skrf_network()
                ntwk = make_full_thru_network(ntwk)
                ntwk = average_duplicate_freq(ntwk)
                data_cal = cal.apply_cal(ntwk)
                tscal = Touchstone()   
                tscal.from_skrf_network(data_cal)
                s11_cal = tscal.s11[:]
                s21_cal = tscal.s21[:]

        self.ref_data.s11 = s11
        for c in self.s11charts:
            c.setReference(s11)

        self.ref_data.s21 = s21
        for c in self.s21charts:
            c.setReference(s21)

        for c in self.combinedCharts:
            c.setCombinedReference(s11, s21)


        self.calibrationCharts[0].setReference(s11_cal)#'S11_db_phase'
        self.calibrationCharts[1].setReference(s21_cal)#S21_db_phase
        self.calibrationCharts[2].setReference(s21_cal)#real_imag_shunt

        self.btnResetReference.setDisabled(False)

        self.referenceSource = source or self.sweepSource
        self.updateTitle()

    def updateTitle(self):
        insert = "("
        if self.sweepSource != "":
            insert += (
                f"Sweep: {self.sweepSource} @ {len(self.data.s11)} points"
                f"{', ' if self.referenceSource else ''}"
            )
        if self.referenceSource != "":
            insert += (
                f"Reference: {self.referenceSource} @"
                f" {len(self.ref_data.s11)} points"
            )
        insert += ")"
        title = f"{self.baseTitle} {insert or ''}"
        self.setWindowTitle(title)

    def resetReference(self):
        self.ref_data = Touchstone()
        self.referenceSource = ""
        self.updateTitle()
        for c in self.subscribing_charts:
            c.resetReference()
        self.btnResetReference.setDisabled(True)

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(1100, 950)

    def display_window(self, name):
        self.windows[name].show()
        QtWidgets.QApplication.setActiveWindow(self.windows[name])

    def showError(self, text):
        QtWidgets.QMessageBox.warning(self, "Error", text)

    def showSweepError(self):
        # self.showError(self.worker.error_message)
        # with contextlib.suppress(IOError):
        #     self.vna.flushSerialBuffers()  # Remove any left-over data
        #     self.vna.reconnect()  # try reconnection
        # self.sweepFinished()
        
        msg = self.worker.error_message or "Unknown sweep error"

        # batch sweep中は止めずに記録して継続
        if self._batch_active:
            logger.warning("Batch sweep error: %s", msg)
            self._batch_last_sweep_failed = True
            self._batch_last_error_message = msg

            with contextlib.suppress(IOError, Exception):
                self.vna.flushSerialBuffers()
                self.vna.reconnect()

            self.sweepFinished()
            return

        # 通常 sweep は従来通り
        self.showError(msg)
        with contextlib.suppress(IOError, Exception):
            self.vna.flushSerialBuffers()
            self.vna.reconnect()
        self.sweepFinished()

    def popoutChart(self, chart: Chart):
        logger.debug("Requested popout for chart: %s", chart.name)
        new_chart = self.copyChart(chart)
        new_chart.isPopout = True
        new_chart.show()
        new_chart.setWindowTitle(new_chart.name)
        new_chart.setWindowIcon(get_window_icon())

    def copyChart(self, chart: Chart):
        new_chart = chart.copy()
        self.subscribing_charts.append(new_chart)
        if chart in self.s11charts:
            self.s11charts.append(new_chart)
        if chart in self.s21charts:
            self.s21charts.append(new_chart)
        if chart in self.combinedCharts:
            self.combinedCharts.append(new_chart)
        if chart in self.calibrationCharts:
            self.calibrationCharts.append(new_chart)
        new_chart.popout_requested.connect(self.popoutChart)
        return new_chart

    def closeEvent(self, a0: QtGui.QCloseEvent) -> None:
        self.worker.quit()
        self.worker.wait(WORKING_KILL_TIME_MS)
        for marker in self.markers:
            marker.update_settings()
        self.settings.sync()
        self.bands.saveSettings()
        self.threadpool.waitForDone(2500)

        app_config = get_app_config()
        app_config.chart.marker_count = Marker.count()
        app_config.gui.window_width = self.width()
        app_config.gui.window_height = self.height()
        app_config.gui.splitter_sizes = self.splitter.saveState()

        self.sweep_control.store_settings()

        self.settings.store_config()

        # Dosconnect connected devices and release serial port
        self.serial_control.disconnect_device()

        a0.accept()

    def changeFont(self, font: QtGui.QFont) -> None:
        qf_new = QtGui.QFontMetricsF(font)
        normal_font = QtGui.QFont(font)
        normal_font.setPointSize(8)
        qf_normal = QtGui.QFontMetricsF(normal_font)
        # Characters we would normally display
        standard_string = "0.123456789 0.123456789 MHz \N{OHM SIGN}"
        new_width = qf_new.horizontalAdvance(standard_string)
        old_width = qf_normal.horizontalAdvance(standard_string)
        self.scale_factor = new_width / old_width
        logger.debug(
            "New font width: %f, normal font: %f, factor: %f",
            new_width,
            old_width,
            self.scale_factor,
        )
        # TODO: Update all the fixed widths to account for the scaling
        for m in self.markers:
            m.get_data_layout().setFont(font)
            m.setScale(self.scale_factor)

    def update_sweep_title(self):
        for c in self.subscribing_charts:
            c.setSweepTitle(self.sweep.properties.name)

    def batch_setup_and_start(self):
        default_dir = os.path.expanduser("~")
        default_base = self.sweep.properties.name or "sweep"
        dlg = BatchSweepDialog(self, default_dir=default_dir, default_base=default_base)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return

        cfg = dlg.get_config()
        if not cfg.out_dir:
            self.showError("Please select a save directory.")
            return
        os.makedirs(cfg.out_dir, exist_ok=True)

        self._batch_cfg = cfg
        self._batch_index = 0
        self._batch_active = True

        self.btn_batch_start.setDisabled(True)
        self.btn_batch_stop.setDisabled(False)

        # start first sweep
        self._batch_start_next()

    def batch_stop(self):
        self._batch_active = False
        self.btn_batch_stop.setDisabled(True)
        self.btn_batch_start.setDisabled(False)
        # stop current sweep if running
        with contextlib.suppress(Exception):
            self.worker.quit()

    def _batch_start_next(self):
        if not self._batch_active or not self._batch_cfg:
            return
        if self._batch_index >= self._batch_cfg.count:
            # done
            self._batch_active = False
            self.btn_batch_stop.setDisabled(True)
            self.btn_batch_start.setDisabled(False)
            return

        self._batch_index += 1
        self.sweep_start()

    # def _on_sweep_finished_for_batch(self):
        # # called on every sweep finish; only act if batch is active
        # if not self._batch_active or not self._batch_cfg:
        #     return

        # try:
        #     self._batch_save_current_touchstone()
        # except Exception as exc:  # noqa: BLE001
        #     self.showError(f"Failed saving sweep {self._batch_index}: {exc}")
        #     self._batch_active = False
        #     self.btn_batch_stop.setDisabled(True)
        #     self.btn_batch_start.setDisabled(False)
        #     return

        # # schedule next sweep after rest time
        # rest_ms = int(self._batch_cfg.rest_s * 1000)
        # QtCore.QTimer.singleShot(rest_ms, self._batch_start_next)
    def _on_sweep_finished_for_batch(self):
        # called on every sweep finish; only act if batch is active
        if not self._batch_active or not self._batch_cfg:
            return

        if self._batch_last_sweep_failed:
            logger.warning(
                "Skipping save for failed sweep %d: %s",
                self._batch_index,
                self._batch_last_error_message,
            )

            # 失敗ログだけ残す
            fail_log = os.path.join(
                self._batch_cfg.out_dir,
                f"{self._batch_cfg.base_name}_errors.csv"
            )
            new_file = not os.path.exists(fail_log)
            with open(fail_log, "a", encoding="utf-8") as f:
                if new_file:
                    f.write("index,error\n")
                f.write(f'{self._batch_index},"{self._batch_last_error_message}"\n')

            self._batch_last_sweep_failed = False
            self._batch_last_error_message = ""
        else:
            try:
                self._batch_save_current_touchstone()
            except Exception as exc:
                logger.exception("Failed saving sweep %d", self._batch_index)

                fail_log = os.path.join(
                    self._batch_cfg.out_dir,
                    f"{self._batch_cfg.base_name}_errors.csv"
                )
                new_file = not os.path.exists(fail_log)
                with open(fail_log, "a", encoding="utf-8") as f:
                    if new_file:
                        f.write("index,error\n")
                    f.write(f'{self._batch_index},"save failed: {exc}"\n')

        # schedule next sweep after rest time
        rest_ms = int(self._batch_cfg.rest_s * 1000)
        QtCore.QTimer.singleShot(rest_ms, self._batch_start_next)

    def _batch_save_current_touchstone(self):
        assert self._batch_cfg is not None
        cfg = self._batch_cfg
        idx = self._batch_index

        # copy data safely
        with self.dataLock:
            s11 = self.data.s11[:]
            s21 = self.data.s21[:]

        if not s11:
            raise ValueError("No S11 data to save.")
        if not s21:
            # allow saving even if S21 is empty: fill zeros
            s21 = [Datapoint(dp.freq, 0.0, 0.0) for dp in s11]

        # filename with serial number
        fname = f"{cfg.base_name}_{idx:08d}.s2p"
        path = os.path.join(cfg.out_dir, fname)

        # Write S2P (S11 S21 S12 S22), fill S12/S22 with zeros
        # Field order is consistent with Touchstone.FIELD_ORDER ("11","21","12","22") :contentReference[oaicite:5]{index=5}
        with open(path, "w", encoding="utf-8") as f:
            f.write("# HZ S RI R 50\n")
            for i, dp11 in enumerate(s11):
                dp21 = s21[i] if i < len(s21) else Datapoint(dp11.freq, 0.0, 0.0)
                # S12, S22 = 0
                f.write(
                    f"{dp11.freq} {dp11.re} {dp11.im} "
                    f"{dp21.re} {dp21.im} "
                    f"0 0 0 0\n"
                )
        # Temperature logging for batch sweep only
        # if cfg.save_temperature:
        #     self.temperature_logger.csv_path = os.path.join(
        #         cfg.out_dir, "temperature_log.csv"
        #     )

        #     try:
        #         result = self.temperature_logger.log_temperature_for_file(path)
        #         if result is not None:
        #             idx_logged, temp_c = result
        #             logger.info(
        #                 "Temperature logged: index=%s temp=%.4f C",
        #                 idx_logged, temp_c
        #             )
        #     except Exception as exc:
        #         self.showError(
        #             f"Temperature logging failed for sweep {idx}: {exc}"
        #         )
        # Temperature logging for batch sweep only
        if cfg.save_temperature:
            self.temperature_logger.csv_path = os.path.join(
                cfg.out_dir, f"{cfg.base_name}_temperature.csv"
            )

            try:
                raw_data = self.temperature_logger.log_raw_for_file(path)
                if raw_data is not None:
                    logger.info(
                        "Temperature logged: raw=%s resistance=%.6f temp=%s fault=%s",
                        raw_data["raw"],
                        raw_data["resistance_ohm"],
                        raw_data["temp_c"],
                        raw_data["fault"],
                    )
            except Exception as exc:
                self.showError(
                    f"Temperature logging failed for sweep {idx}: {exc}"
                )
