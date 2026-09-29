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
import math
import threading
import warnings
from time import localtime, monotonic, strftime
import skrf as rf
import numpy as np
from skrf.calibration import SOLT
from skrf.constants import c as RF_C
from skrf.frequency import InvalidFrequencyWarning
from skrf.media.media import DefinedGammaZ0
from skrf.network import two_port_reflect

warnings.filterwarnings("ignore", category=InvalidFrequencyWarning)

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
    RealImaginaryZComputedChart,
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
from .Hardware.VNA import VNA, _reset_usb_device_for_serial
from .Marker.Delta import DeltaMarker
from .Marker.Widget import Marker
from .RFTools import Datapoint, corr_att_data
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

from pathlib import Path
from .temperature_logging import TemperatureLoggingManager

logger = logging.getLogger(__name__)

WORKING_KILL_TIME_MS = 10 * 1000
MIN_WINDOW_WIDTH = 800
MIN_WINDOW_HEIGHT = 600
BATCH_SWEEP_STALL_TIMEOUT_S = 180
BATCH_WATCHDOG_INTERVAL_MS = 30 * 1000
BATCH_SCHEDULE_GRACE_S = 60
BATCH_CONNECTION_REFRESH_SWEEPS = 100
BATCH_CONNECTION_REFRESH_INTERVAL_S = 60 * 60
BATCH_ERROR_RECOVERY_COOLDOWN_MS = 15 * 1000
BATCH_MAX_RECOVERY_FAILURES = 5
REBOOT_RECONNECT_INTERVAL_MS = 1000
REBOOT_RECONNECT_ATTEMPTS = 20


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
    s11 = s[:, 0, 0] if s.shape[1] >= 1 else np.zeros(n, dtype=complex)
    s21 = s[:, 1, 0] if s.shape[1] >= 2 else np.zeros(n, dtype=complex)
    s_full[:, 0, 0] = s11
    s_full[:, 1, 1] = s11
    s_full[:, 1, 0] = s21
    s_full[:, 0, 1] = s21
    nt = ntwk.copy()
    nt.s = s_full
    return nt


DEFAULT_EXTERNAL_CALPATH = (
    "/home/keisuke/Documents/Spara/caldata/nanoVNAH4/"
    "20260710_nanoVNAH4_woDCcut_logarithmic_whitecable_10k1G/"
)
VNA6000_EXTERNAL_CALPATH = (
    "/home/keisuke/Documents/Spara/caldata/VNA6000/"
    "20260709_VNA6000_100k_6G_woDCcut/"
)
datalist = ['short','open','load','thru']
SOCKET_SHORT_NAME = "socket_short"
SOCKET_OPEN_NAME = "socket_open"
kVNApath = '/home/keisuke/Documents/Spara/caldata/20260407_30k-9G_IF100Hz_-10dBm_801pt_ave10_150mm/'
ansdata = {}
for i in ['short_P1', 'open_P1', 'load_P1', 'short_P2', 'open_P2', 'load_P2', 'thru']:
    answer_path = Path(kVNApath) / f"S_{i}.s2p"
    if answer_path.exists():
        ansdata[i] = rf.Network(str(answer_path))
    else:
        logger.warning("Calibration answer file not found: %s", answer_path)

calpath = ""
caldata = {}
freq = None
media = None
ideals = []
ans_ideals = []
measured = []
cal = None
_cal_cache_key = ()
_cal_cache = None


def _ensure_calpath(path: str) -> str:
    return path if path.endswith("/") else f"{path}/"


def _load_external_calibration(path: str):
    loaded_caldata = {}
    for name in datalist:
        loaded_caldata[name] = {
            'ntwk': get_S2P_nVNA(f'{path}S_{name}.s2p')
        }
    socket_short_path = Path(path) / "S_socket_short.s2p"
    if socket_short_path.exists():
        loaded_caldata[SOCKET_SHORT_NAME] = {
            "ntwk": get_S2P_nVNA(str(socket_short_path))
        }
    else:
        logger.warning("Socket short file not found: %s", socket_short_path)
    socket_open_path = Path(path) / "S_socket_open.s2p"
    if socket_open_path.exists():
        loaded_caldata[SOCKET_OPEN_NAME] = {
            "ntwk": get_S2P_nVNA(str(socket_open_path))
        }

    loaded_freq = loaded_caldata['short']['ntwk'].frequency
    beta = 2*np.pi*loaded_freq.f / RF_C   # 自由空間の β（簡易）
    loaded_media = DefinedGammaZ0(
        frequency=loaded_freq, z0=50, gamma=1j*beta
    )
    loaded_ideals = [
        loaded_media.short(nports=2),
        loaded_media.open(nports=2),
        loaded_media.match(nports=2),
        loaded_media.thru(),
    ]

    fixed_ans = {
        name: ansdata[name].interpolate(
            loaded_freq, kind='linear', fill_value='extrapolate'
        )
        for name in (
            'short_P1',
            'open_P1',
            'load_P1',
            'short_P2',
            'open_P2',
            'load_P2',
            'thru',
        )
    }
    loaded_ans_ideals = [
        two_port_reflect(fixed_ans['short_P1'].s11, fixed_ans['short_P2'].s22),
        two_port_reflect(fixed_ans['open_P1'].s11, fixed_ans['open_P2'].s22),
        two_port_reflect(fixed_ans['load_P1'].s11, fixed_ans['load_P2'].s22),
        fixed_ans['thru'],
    ]

    measured_thru = loaded_caldata['thru']['ntwk']
    short_meas_2p = two_port_reflect(
        loaded_caldata['short']['ntwk'].s11,
        loaded_caldata['short']['ntwk'].s11,
    )
    open_meas_2p = two_port_reflect(
        loaded_caldata['open']['ntwk'].s11,
        loaded_caldata['open']['ntwk'].s11,
    )
    load_meas_2p = two_port_reflect(
        loaded_caldata['load']['ntwk'].s11,
        loaded_caldata['load']['ntwk'].s11,
    )
    loaded_measured = [
        short_meas_2p,
        open_meas_2p,
        load_meas_2p,
        measured_thru,
    ]

    loaded_cal = SOLT(
        ideals=loaded_ans_ideals,
        measured=loaded_measured,
        n_ports=2,
    )
    return (
        loaded_caldata,
        loaded_freq,
        loaded_media,
        loaded_ideals,
        loaded_ans_ideals,
        loaded_measured,
        loaded_cal,
    )


def set_external_calibration_path(path: str) -> bool:
    global calpath, caldata, freq, media, ideals, ans_ideals, measured, cal
    global _cal_cache_key, _cal_cache

    path = _ensure_calpath(path)
    if path == calpath and _cal_cache is not None:
        return True

    try:
        (
            new_caldata,
            new_freq,
            new_media,
            new_ideals,
            new_ans_ideals,
            new_measured,
            new_cal,
        ) = _load_external_calibration(path)
    except Exception as exc:
        logger.warning(
            "Failed loading external calibration from %s: %s", path, exc
        )
        return False

    calpath = path
    caldata = new_caldata
    freq = new_freq
    media = new_media
    ideals = new_ideals
    ans_ideals = new_ans_ideals
    measured = new_measured
    cal = new_cal
    _cal_cache_key = ()
    _cal_cache = new_cal
    logger.info("External calibration path: %s", calpath)
    return True


set_external_calibration_path(DEFAULT_EXTERNAL_CALPATH)


def _frequency_key(frequency) -> tuple[int, ...]:
    return tuple(int(round(f)) for f in frequency.f)


def _network_at_frequency(ntwk, frequency):
    return ntwk.interpolate(frequency, kind="linear", fill_value="extrapolate")


def _calibration_at_frequency(frequency):
    global _cal_cache_key, _cal_cache

    key = _frequency_key(frequency)
    if key == _cal_cache_key:
        return _cal_cache

    fixed_ans = {
        name: _network_at_frequency(ansdata[name], frequency)
        for name in (
            "short_P1",
            "open_P1",
            "load_P1",
            "short_P2",
            "open_P2",
            "load_P2",
            "thru",
        )
    }
    target_ideals = [
        two_port_reflect(fixed_ans["short_P1"].s11, fixed_ans["short_P2"].s22),
        two_port_reflect(fixed_ans["open_P1"].s11, fixed_ans["open_P2"].s22),
        two_port_reflect(fixed_ans["load_P1"].s11, fixed_ans["load_P2"].s22),
        fixed_ans["thru"],
    ]

    fixed_meas = {
        name: _network_at_frequency(caldata[name]["ntwk"], frequency)
        for name in datalist
    }
    target_measured = [
        two_port_reflect(fixed_meas["short"].s11, fixed_meas["short"].s11),
        two_port_reflect(fixed_meas["open"].s11, fixed_meas["open"].s11),
        two_port_reflect(fixed_meas["load"].s11, fixed_meas["load"].s11),
        fixed_meas["thru"],
    ]

    _cal_cache_key = key
    _cal_cache = SOLT(
        ideals=target_ideals,
        measured=target_measured,
        n_ports=2,
    )
    return _cal_cache


def apply_external_calibration(ntwk):
    return _calibration_at_frequency(ntwk.frequency).apply_cal(ntwk)


def calculate_cal_shunt_z(
    s11: list[Datapoint], s21: list[Datapoint]
) -> list[Datapoint]:
    z_data = []
    for dp11, dp21 in zip(s11, s21):
        numerator = 50 * (dp21.z + dp21.z)
        denominator = (1 - dp11.z) * (1 - dp11.z) - dp21.z * dp21.z
        try:
            z = numerator / denominator
        except ZeroDivisionError:
            z = complex(float("inf"), float("inf"))
        z_data.append(Datapoint(dp11.freq, z.real, z.imag))
    return z_data


def subtract_impedance(
    z_data: list[Datapoint], offset_data: list[Datapoint]
) -> list[Datapoint]:
    corrected = []
    for z_dp, offset_dp in zip(z_data, offset_data):
        z = z_dp.z - offset_dp.z
        corrected.append(Datapoint(z_dp.freq, z.real, z.imag))
    return corrected


def _admittance(z: complex) -> complex:
    if not math.isfinite(z.real) or not math.isfinite(z.imag):
        return 0j
    try:
        return 1 / z
    except ZeroDivisionError:
        return complex(float("inf"), float("inf"))


def subtract_admittance(
    z_data: list[Datapoint], offset_data: list[Datapoint]
) -> list[Datapoint]:
    corrected = []
    for z_dp, offset_dp in zip(z_data, offset_data):
        y = _admittance(z_dp.z) - _admittance(offset_dp.z)
        try:
            z = 1 / y
        except ZeroDivisionError:
            z = complex(float("inf"), float("inf"))
        corrected.append(Datapoint(z_dp.freq, z.real, z.imag))
    return corrected


def calculate_socket_z(socket_name: str, frequency) -> list[Datapoint]:
    socket_ntwk = caldata.get(socket_name, {}).get("ntwk")
    if socket_ntwk is None:
        return []
    socket_at_frequency = _network_at_frequency(socket_ntwk, frequency)
    socket_cal = apply_external_calibration(socket_at_frequency)
    ts_socket = Touchstone()
    ts_socket.from_skrf_network(socket_cal)
    return calculate_cal_shunt_z(
        ts_socket.s11[:],
        ts_socket.s21[:],
    )


def calculate_socket_short_z(frequency) -> list[Datapoint]:
    return calculate_socket_z(SOCKET_SHORT_NAME, frequency)


def calculate_socket_open_z(frequency) -> list[Datapoint]:
    return calculate_socket_z(SOCKET_OPEN_NAME, frequency)


def calculate_socket_short_corrected_shunt_z(
    cal_shunt_z: list[Datapoint], frequency
) -> list[Datapoint]:
    socket_short_z = calculate_socket_short_z(frequency)
    if not socket_short_z:
        return []
    short_corrected = subtract_impedance(cal_shunt_z, socket_short_z)

    socket_open_z = calculate_socket_open_z(frequency)
    if not socket_open_z:
        return short_corrected

    socket_open_short_corrected = subtract_impedance(
        socket_open_z, socket_short_z
    )
    return subtract_admittance(
        short_corrected, socket_open_short_corrected
    )


def log_external_calibration_delta(
    raw_s11: list[Datapoint],
    raw_s21: list[Datapoint],
    cal_s11: list[Datapoint],
    cal_s21: list[Datapoint],
) -> None:
    if not raw_s11 or not raw_s21 or not cal_s11 or not cal_s21:
        return
    index = min(len(raw_s11), len(raw_s21), len(cal_s11), len(cal_s21)) // 2
    raw11 = raw_s11[index].z
    raw21 = raw_s21[index].z
    cal11 = cal_s11[index].z
    cal21 = cal_s21[index].z
    logger.info(
        "External SOLT applied at %d Hz: "
        "S11 raw=%s cal=%s |delta|=%.6g, "
        "S21 raw=%s cal=%s |delta|=%.6g",
        cal_s11[index].freq,
        raw11,
        cal11,
        abs(cal11 - raw11),
        raw21,
        cal21,
        abs(cal21 - raw21),
    )






class Communicate(QObject):
    data_available = QtCore.Signal()

@dataclass
class BatchSweepConfig:
    out_dir: str
    base_name: str
    rest_s: float
    count: int
    reboot_every: int
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
        self.count_spin.setValue(20)
        layout.addRow("Number of sweeps:", self.count_spin)

        # Reboot interval
        self.reboot_every_spin = QtWidgets.QSpinBox()
        self.reboot_every_spin.setRange(1, 10_000_000)
        self.reboot_every_spin.setValue(100)
        layout.addRow("Reboot every N sweeps:", self.reboot_every_spin)

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
        reboot_every = int(self.reboot_every_spin.value())
        # return BatchSweepConfig(out_dir=out_dir, base_name=base, rest_s=rest_s, count=count)
        save_temperature = self.temp_checkbox.isChecked()
        return BatchSweepConfig(
                out_dir=out_dir,
                base_name=base,
                rest_s=rest_s,
                count=count,
                reboot_every=reboot_every,
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
        self.external_calibration_user_path = self.settings.value(
            "ExternalSOLTPath", "", str
        )
        self.threadpool = QtCore.QThreadPool()
        self.sweep = Sweep()
        self.worker = SweepWorker(self)
        self._connect_worker_signals()

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
        self._litevna_defaults_applied = False

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
        self.calibrated_s11 = []
        self.calibrated_s21 = []
        self.cal_shunt_z = []
        self.cal_shunt_z_short_corrected = []

        # Batch sweep state
        self._batch_active = False
        self._batch_cfg: BatchSweepConfig | None = None
        self._batch_index = 0
        self._batch_last_completed_index = 0
        self._batch_finished_wait_since = 0.0
        self._batch_last_sweep_failed = False
        self._batch_last_error_message = ""
        self._batch_last_connection_refresh_at = monotonic()
        self._batch_needs_recovery_cooldown = False
        self._batch_recovery_failures = 0
        self._batch_watchdog_progress = -1.0
        self._batch_watchdog_changed_at = monotonic()
        self._batch_schedule_generation = 0
        self._batch_pending_action = None
        self._batch_pending_action_due_at = 0.0
        self._batch_reboot_reconnecting = False
        self._batch_reboot_deadline = 0.0
        self._batch_watchdog = QtCore.QTimer(self)
        self._batch_watchdog.setInterval(BATCH_WATCHDOG_INTERVAL_MS)
        self._batch_watchdog.timeout.connect(self._batch_watchdog_tick)

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
                "real_imag_shunt_formula": RealImaginaryZComputedChart(
                    "Cal ShuntZ formula R+jX"
                ),
                "real_imag_shunt_formula_socket_short": (
                    RealImaginaryZComputedChart(
                        "Cal ShuntZ formula fixture-corrected R+jX"
                    )
                ),
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
            c.setDrawLines(get_app_config().chart.show_lines)
        self._set_chart_frequency_range()

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

        external_solt_box = QtWidgets.QGroupBox("External SOLT")
        external_solt_layout = QtWidgets.QFormLayout(external_solt_box)
        self.external_calibration_path_edit = QtWidgets.QLineEdit(calpath)
        self.external_calibration_path_edit.setReadOnly(True)
        self.external_calibration_path_edit.setMinimumHeight(20)
        self.external_calibration_path_edit.setToolTip(calpath)

        btn_external_solt_select = QtWidgets.QPushButton("Select ...")
        btn_external_solt_select.setMinimumHeight(20)
        btn_external_solt_select.clicked.connect(
            self.select_external_calibration_path
        )
        btn_external_solt_auto = QtWidgets.QPushButton("Auto")
        btn_external_solt_auto.setMinimumHeight(20)
        btn_external_solt_auto.clicked.connect(
            self.reset_external_calibration_path_auto
        )
        external_solt_buttons = QtWidgets.QHBoxLayout()
        external_solt_buttons.addWidget(btn_external_solt_select)
        external_solt_buttons.addWidget(btn_external_solt_auto)

        external_solt_layout.addRow("Folder:", self.external_calibration_path_edit)
        external_solt_layout.addRow(external_solt_buttons)
        left_column.addWidget(external_solt_box)
        if self.external_calibration_user_path:
            self._set_external_calibration_path(
                self.external_calibration_user_path,
                save_user_path=False,
                show_error=False,
                refresh=False,
            )
        else:
            self._update_external_calibration_path_display()

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

        

    def _connect_worker_signals(self) -> None:
        self.worker.signals.updated.connect(self.dataUpdated)
        self.worker.signals.progress.connect(self.sweepProgressUpdated)
        self.worker.signals.finished.connect(self.sweepFinished)
        # Queue the next batch sweep only after the QThread has stopped.
        self.worker.finished.connect(self._on_sweep_finished_for_batch)
        self.worker.signals.sweep_error.connect(self.showSweepError)

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
            logger.debug("Ignoring invalid saved window width: %s", width)
            width = min(default.width(), max_width)
        if not (MIN_WINDOW_HEIGHT <= height <= max_height):
            logger.debug("Ignoring invalid saved window height: %s", height)
            height = min(default.height(), max_height)
        return width, height

    def _restore_all_sweep_after_connect(self, was_connected: bool = True):
        # SerialControl.connected is emitted for both connection state changes.
        # A disconnect closes the serial port before emitting False, so there is
        # no sweep state to restore in that case.
        if not was_connected:
            return

        try:
            if not self.vna or not hasattr(self.vna, "serial") or not self.vna.serial or not self.vna.serial.is_open:
                logger.warning("Skip restoring sweep: serial not open yet")
                return
            # 1) アプリ側 sweep を丸ごと復元（segments / averaging / log sweep など含む）
            self.sweep = self._sweep_snapshot.copy()
            litevna_defaults_applied = self._apply_litevna_defaults()
            self._select_external_calibration_for_connected_vna()
            self._apply_vna6000_defaults()
            self._prefer_segment_log_sweep_for_vna6000()

            # 2) デバイス側に周波数レンジだけは必ず再適用（points設定APIは無いので不要）
            self.vna.resetSweep(int(self.sweep.start), int(self.sweep.end))
            if litevna_defaults_applied:
                default_start = self.sweep.start
                default_end = self.sweep.end
                self.sweep_control.set_start(default_start)
                self.sweep_control.set_end(default_end)
                self.sweep_control.update_center_span()
                self.sweep_control.update_step_size()
            if "Bandwidth" in self.vna.features:
                try:
                    self.vna.set_bandwidth(self.vna.bandwidth)
                except Exception as e:
                    logger.debug("Failed to set IF bandwidth after connect: %s", e)

            # 3) UI側も復元した sweep に揃える（存在する関数だけ呼ぶ）
            for name in ("updateInputs", "sync_from_sweep", "set_sweep", "update_from_sweep"):
                if hasattr(self.sweep_control, name):
                    getattr(self.sweep_control, name)()
                    break

        except Exception as e:
            logger.warning("Failed to restore sweep after connect: %s", e)

    def _apply_litevna_defaults(self) -> bool:
        if (
            self._litevna_defaults_applied
            or getattr(self.vna, "name", "") != "LiteVNA-64"
        ):
            return False
        self.sweep.update(
            start=50_000,
            end=6_000_000_000,
            segments=self.sweep.segments,
            points=self.vna.datapoints,
        )
        self.sweep.set_averages(3, 0)
        sweep_settings = self.windows.get("sweep_settings")
        if sweep_settings is not None:
            sweep_settings.averages_input.setText("3")
            sweep_settings.truncates_input.setText("0")
        self._litevna_defaults_applied = True
        return True

    def _connected_vna_is_vna6000(self) -> bool:
        board_revision = getattr(self.vna, "board_revision", None)
        return str(board_revision) == "2.0.51"

    def _default_external_calibration_path_for_connected_vna(self) -> str:
        return (
            VNA6000_EXTERNAL_CALPATH
            if self._connected_vna_is_vna6000()
            else DEFAULT_EXTERNAL_CALPATH
        )

    def _update_external_calibration_path_display(self) -> None:
        edit = getattr(self, "external_calibration_path_edit", None)
        if edit is None:
            return
        mode = "Manual" if self.external_calibration_user_path else "Auto"
        text = f"{mode}: {calpath}"
        edit.setText(text)
        edit.setToolTip(text)

    def _set_external_calibration_path(
        self,
        path: str,
        *,
        save_user_path: bool,
        show_error: bool = True,
        refresh: bool = True,
    ) -> bool:
        if not path:
            return False
        if not set_external_calibration_path(path):
            if show_error:
                self.showError(
                    "Failed loading external SOLT calibration folder.\n\n"
                    "Required files: S_short.s2p, S_open.s2p, "
                    "S_load.s2p, S_thru.s2p"
                )
            self._update_external_calibration_path_display()
            return False

        if save_user_path:
            self.external_calibration_user_path = _ensure_calpath(path)
            self.settings.setValue(
                "ExternalSOLTPath", self.external_calibration_user_path
            )
            self.settings.sync()

        self._update_external_calibration_path_display()
        if refresh and self.data.s11:
            self.dataUpdated()
        return True

    def select_external_calibration_path(self) -> None:
        start_dir = self.external_calibration_user_path or calpath
        path = get_existing_directory(
            self,
            "Select external SOLT calibration folder",
            start_dir,
        )
        if not path:
            return
        self._set_external_calibration_path(path, save_user_path=True)

    def reset_external_calibration_path_auto(self) -> None:
        self.external_calibration_user_path = ""
        self.settings.remove("ExternalSOLTPath")
        self.settings.sync()
        self._set_external_calibration_path(
            self._default_external_calibration_path_for_connected_vna(),
            save_user_path=False,
        )

    def _select_external_calibration_for_connected_vna(self) -> None:
        path = (
            self.external_calibration_user_path
            or self._default_external_calibration_path_for_connected_vna()
        )
        if not set_external_calibration_path(path):
            logger.warning("Keeping previous external calibration path: %s", calpath)
        self._update_external_calibration_path_display()

    def _apply_vna6000_defaults(self) -> None:
        if not self._connected_vna_is_vna6000():
            return

        logger.info(
            "VNA6000 detected; using 201 datapoints and 10 averages"
        )
        self.vna.datapoints = 201
        self.sweep.set_points(201)
        self.sweep.set_averages(10, 0)

        with contextlib.suppress(Exception):
            self.sweep_control.update_step_size()

        device_settings = self.windows.get("device_settings")
        with contextlib.suppress(Exception):
            device_settings.updateFields()

        sweep_settings = self.windows.get("sweep_settings")
        for attr, value in (
            ("averages_input", "10"),
            ("truncates_input", "0"),
        ):
            widget = getattr(sweep_settings, attr, None)
            if widget is not None:
                widget.setText(value)

    def _prefer_segment_log_sweep_for_vna6000(self) -> None:
        if not self._connected_vna_is_vna6000():
            return

        logger.info(
            "VNA6000 detected; using segmented logarithmic sweep "
            "instead of single-point log-spaced measurement"
        )
        self.sweep.set_logarithmic_measurement(False)
        self.sweep.set_logarithmic(True)

        sweep_settings = self.windows.get("sweep_settings")
        for attr, checked in (
            ("checkbox_logarithmic_measurement", False),
            ("checkbox_logarithmic", True),
        ):
            checkbox = getattr(sweep_settings, attr, None)
            if checkbox is None:
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(checked)
            checkbox.blockSignals(False)

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
        if self.worker.isRunning():
            logger.warning("Sweep start requested while worker is still running")
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
        self._batch_watchdog_progress = -1.0
        self._batch_watchdog_changed_at = monotonic()
        if self._batch_active and not self._batch_watchdog.isActive():
            self._batch_watchdog.start()
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
            marker.updateLabels(
                self.data.s11,
                self.data.s21,
                self.calibrated_s21,
                self.cal_shunt_z,
                self.cal_shunt_z_short_corrected,
            )
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
        worker_progress = float(self.worker.percentage)
        if worker_progress != self._batch_watchdog_progress:
            self._batch_watchdog_progress = worker_progress
            self._batch_watchdog_changed_at = monotonic()
    
        with self.dataLock:
            s11 = self.data.s11[:]
            s21 = self.data.s21[:]
            s11_cal = []
            s21_cal = []
            cal_shunt_z = []
            cal_shunt_z_short_corrected = []
            if s11:
                try:
                    ntwk = self.data.to_skrf_network()
                    ntwk = make_full_thru_network(ntwk)
                    ntwk = average_duplicate_freq(ntwk)
                    data_cal = apply_external_calibration(ntwk)
                    tscal = Touchstone()
                    tscal.from_skrf_network(data_cal)
                    s11_cal = tscal.s11[:]
                    s21_cal = tscal.s21[:]
                    log_external_calibration_delta(
                        s11, s21, s11_cal, s21_cal
                    )
                    cal_shunt_z = calculate_cal_shunt_z(s11_cal, s21_cal)
                    cal_shunt_z_short_corrected = (
                        calculate_socket_short_corrected_shunt_z(
                            cal_shunt_z,
                            data_cal.frequency,
                        )
                    )
                except Exception as exc:
                    logger.warning("External calibration skipped: %s", exc)
            self.calibrated_s11 = s11_cal
            self.calibrated_s21 = s21_cal
            self.cal_shunt_z = cal_shunt_z
            self.cal_shunt_z_short_corrected = cal_shunt_z_short_corrected
            logger.debug(
                "Calibration chart data lengths: raw=%d cal_s11=%d cal_s21=%d",
                len(s11),
                len(s11_cal),
                len(s21_cal),
            )
            # print(ntwk.s[:,1,0],s21_cal[0])
            

        self._set_chart_frequency_range()

        for m in self.markers:
            m.resetLabels()
            m.updateLabels(
                s11,
                s21,
                s21_cal,
                cal_shunt_z,
                cal_shunt_z_short_corrected,
            )

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
        self.calibrationCharts[3].setData(cal_shunt_z)#real_imag_shunt_formula
        self.calibrationCharts[4].setData(cal_shunt_z_short_corrected)

        self.sweep_control.progress_bar.setValue(int(worker_progress))
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

    def _set_chart_frequency_range(self):
        for chart in getattr(self, "subscribing_charts", []):
            if hasattr(chart, "setFrequencyRange"):
                chart.setFrequencyRange(self.sweep.start, self.sweep.end)
            if (
                self.sweep.properties.logarithmic_measurement
                and hasattr(chart, "setLogarithmicX")
            ):
                chart.setLogarithmicX(True)
                if hasattr(chart, "action_set_logarithmic_x"):
                    chart.action_set_logarithmic_x.setChecked(True)
                if hasattr(chart, "action_set_linear_x"):
                    chart.action_set_linear_x.setChecked(False)

    def sweepProgressUpdated(self):
        worker_progress = float(self.worker.percentage)
        if worker_progress != self._batch_watchdog_progress:
            self._batch_watchdog_progress = worker_progress
            self._batch_watchdog_changed_at = monotonic()
        self.sweep_control.progress_bar.setValue(int(worker_progress))

    def sweepFinished(self):
        if not self._batch_active and self._batch_watchdog.isActive():
            self._batch_watchdog.stop()
        self._sweep_control(start=False)

        for marker in self.markers:
            marker.frequencyInput.textEdited.emit(marker.frequencyInput.text())

    def setReference(self, s11=None, s21=None, source=None):
        s11_cal = []
        s21_cal = []
        cal_shunt_z = []
        cal_shunt_z_short_corrected = []
        if not s11:
            with self.dataLock:
                s11 = self.data.s11[:]
                s21 = self.data.s21[:]
                if s11:
                    try:
                        ntwk = self.data.to_skrf_network()
                        ntwk = make_full_thru_network(ntwk)
                        ntwk = average_duplicate_freq(ntwk)
                        data_cal = apply_external_calibration(ntwk)
                        tscal = Touchstone()
                        tscal.from_skrf_network(data_cal)
                        s11_cal = tscal.s11[:]
                        s21_cal = tscal.s21[:]
                        log_external_calibration_delta(
                            s11, s21, s11_cal, s21_cal
                        )
                        cal_shunt_z = calculate_cal_shunt_z(s11_cal, s21_cal)
                        cal_shunt_z_short_corrected = (
                            calculate_socket_short_corrected_shunt_z(
                                cal_shunt_z,
                                data_cal.frequency,
                            )
                        )
                    except Exception as exc:
                        logger.warning(
                            "External reference calibration skipped: %s", exc
                        )

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
        self.calibrationCharts[3].setReference(cal_shunt_z)#real_imag_shunt_formula
        self.calibrationCharts[4].setReference(cal_shunt_z_short_corrected)

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

    def reboot_vna_power(self):
        if not self.vna or not self.vna.connected():
            self.showError("NanoVNA is not connected.")
            return

        reply = QtWidgets.QMessageBox.question(
            self,
            "Reboot NanoVNA",
            "Send reset command to the NanoVNA?\n\n"
            "The USB connection will disconnect, then NanoVNASaver will "
            "try to reconnect automatically.",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        if self._batch_active:
            self._batch_active = False
            self._batch_watchdog.stop()
            self.btn_batch_stop.setDisabled(True)
            self.btn_batch_start.setDisabled(False)

        if self.worker.isRunning():
            self.worker.quit()
            serial_port = getattr(self.vna, "serial", None)
            with contextlib.suppress(Exception):
                if hasattr(serial_port, "cancel_read"):
                    serial_port.cancel_read()
                if hasattr(serial_port, "cancel_write"):
                    serial_port.cancel_write()
            if not self.worker.wait(5000):
                logger.warning("Worker did not stop before VNA reboot")
                self.showError(
                    "Sweep is still running. Stop the sweep before rebooting."
                )
                return
            self._sweep_control(start=False)

        try:
            reconnect_port = self._send_vna_reset_and_close()
        except Exception as exc:
            logger.exception("Failed to reboot NanoVNA: %s", exc)
            self.showError(f"Failed to send NanoVNA reset command: {exc}")
            return

        QtCore.QTimer.singleShot(
            REBOOT_RECONNECT_INTERVAL_MS,
            lambda: self._auto_reconnect_after_reboot(
                reconnect_port, REBOOT_RECONNECT_ATTEMPTS
            ),
        )

    def _send_vna_reset_and_close(self) -> str | None:
        reconnect_port = getattr(getattr(self.vna, "serial", None), "port", None)
        self.vna.power_reset()

        serial_port = getattr(self.vna, "serial", None)
        with contextlib.suppress(Exception):
            if hasattr(serial_port, "cancel_read"):
                serial_port.cancel_read()
            if hasattr(serial_port, "cancel_write"):
                serial_port.cancel_write()
            serial_port.close()
        self.serial_control.mark_disconnected()
        return reconnect_port

    def _auto_reconnect_after_reboot(
        self,
        reconnect_port: str | None,
        attempts_left: int,
        on_connected=None,
        on_failed=None,
        reconnect_serial: str | None = None,
    ) -> None:
        if self.vna and self.vna.connected():
            if on_connected:
                on_connected()
            return

        try:
            self.serial_control.rescanSerialPort()
            selected = False
            if reconnect_serial:
                selected = self.serial_control.select_serial_number(
                    reconnect_serial
                )
            if not selected and reconnect_port:
                selected = self.serial_control.select_port(reconnect_port)
            if not selected and self.serial_control.inp_port.count() == 1:
                self.serial_control.inp_port.setCurrentIndex(0)
                selected = self.serial_control.inp_port.currentData() is not None

            if selected:
                self.serial_control.connect_device()
                if self.vna and self.vna.connected():
                    if on_connected:
                        on_connected()
                    return
        except Exception as exc:
            logger.debug("Auto reconnect after NanoVNA reboot failed: %s", exc)

        attempts_left -= 1
        if attempts_left <= 0:
            if on_failed:
                on_failed()
            else:
                self.showError(
                    "NanoVNA reset command was sent, but automatic reconnect "
                    "timed out."
                )
            return

        QtCore.QTimer.singleShot(
            REBOOT_RECONNECT_INTERVAL_MS,
            lambda: self._auto_reconnect_after_reboot(
                reconnect_port,
                attempts_left,
                on_connected=on_connected,
                on_failed=on_failed,
                reconnect_serial=reconnect_serial,
            ),
        )

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

            self._recover_vna_after_failed_batch_sweep()
            # SweepWorker emits ``finished`` immediately after ``sweep_error``.
            # The finished signal is already connected to sweepFinished() and
            # _on_sweep_finished_for_batch(), so invoking them here would run
            # batch completion twice and schedule two subsequent sweeps.
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
        self._batch_last_completed_index = 0
        self._batch_finished_wait_since = 0.0
        self._batch_active = True
        self._batch_last_sweep_failed = False
        self._batch_last_error_message = ""
        self._batch_last_connection_refresh_at = monotonic()
        self._batch_needs_recovery_cooldown = False
        self._batch_recovery_failures = 0
        self._batch_cancel_pending_action()
        self._batch_reboot_reconnecting = False

        self.btn_batch_start.setDisabled(True)
        self.btn_batch_stop.setDisabled(False)

        # start first sweep
        self._batch_start_next()

    def batch_stop(self):
        self._batch_active = False
        self._batch_cancel_pending_action()
        self._batch_reboot_reconnecting = False
        self._batch_watchdog.stop()
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
            self._batch_cancel_pending_action()
            self._batch_watchdog.stop()
            self.btn_batch_stop.setDisabled(True)
            self.btn_batch_start.setDisabled(False)
            return

        self._batch_index += 1
        self._batch_finished_wait_since = 0.0
        self.sweep_start()

    def _batch_cancel_pending_action(self) -> None:
        self._batch_schedule_generation += 1
        self._batch_pending_action = None
        self._batch_pending_action_due_at = 0.0

    def _schedule_batch_action(self, delay_ms: int, action) -> None:
        """Schedule one tracked batch action, replacing any older action."""
        self._batch_schedule_generation += 1
        generation = self._batch_schedule_generation
        self._batch_pending_action = action
        self._batch_pending_action_due_at = monotonic() + delay_ms / 1000
        QtCore.QTimer.singleShot(
            delay_ms,
            lambda: self._run_scheduled_batch_action(generation),
        )

    def _run_scheduled_batch_action(self, generation: int) -> None:
        if generation != self._batch_schedule_generation:
            return
        action = self._batch_pending_action
        self._batch_pending_action = None
        self._batch_pending_action_due_at = 0.0
        if self._batch_active and action is not None:
            action()

    def _batch_watchdog_tick(self):
        if not self._batch_active or not self._batch_cfg:
            self._batch_watchdog.stop()
            return
        if not self.worker.isRunning():
            if self._batch_reboot_reconnecting:
                if monotonic() <= self._batch_reboot_deadline:
                    return
                message = "scheduled NanoVNA reconnect did not complete"
                logger.error(message)
                self._batch_log_error(self._batch_index, message)
                self._stop_batch_after_unrecoverable_vna_failure()
                return

            action = self._batch_pending_action
            if action is None:
                if self._batch_last_completed_index < self._batch_index:
                    now = monotonic()
                    if not self._batch_finished_wait_since:
                        self._batch_finished_wait_since = now
                        return
                    if (
                        now - self._batch_finished_wait_since
                        <= BATCH_SCHEDULE_GRACE_S
                    ):
                        return
                    message = (
                        "worker stopped without a batch completion callback"
                    )
                    logger.warning(message)
                    self._batch_last_sweep_failed = True
                    self._batch_last_error_message = message
                    self._on_sweep_finished_for_batch()
                    return
                message = "batch has no running worker or scheduled action"
                logger.error(message)
                self._batch_log_error(self._batch_index, message)
                self._stop_batch_after_unrecoverable_vna_failure()
                return

            overdue_s = monotonic() - self._batch_pending_action_due_at
            if overdue_s <= BATCH_SCHEDULE_GRACE_S:
                return

            logger.warning(
                "Batch action is %.0fs overdue; running it now", overdue_s
            )
            generation = self._batch_schedule_generation
            self._run_scheduled_batch_action(generation)
            return

        stalled_s = monotonic() - self._batch_watchdog_changed_at
        if stalled_s < BATCH_SWEEP_STALL_TIMEOUT_S:
            return

        message = (
            f"sweep stalled at {self.worker.percentage:.0f}% "
            f"for {stalled_s:.0f}s"
        )
        logger.warning("Batch sweep %d %s", self._batch_index, message)
        self._batch_last_sweep_failed = True
        self._batch_last_error_message = message
        self._abort_stalled_worker()
        self._batch_needs_recovery_cooldown = True
        self.sweepFinished()
        self._on_sweep_finished_for_batch()

    def _note_batch_worker_activity(self) -> None:
        if self._batch_active:
            self._batch_watchdog_changed_at = monotonic()

    def _abort_stalled_worker(self) -> None:
        self.worker.quit()

        serial_port = getattr(getattr(self, "vna", None), "serial", None)
        with contextlib.suppress(Exception):
            if hasattr(serial_port, "cancel_read"):
                serial_port.cancel_read()
            if hasattr(serial_port, "cancel_write"):
                serial_port.cancel_write()

        if not self.worker.wait(5000):
            logger.warning("Worker did not stop after quit; terminating thread")
            with contextlib.suppress(Exception):
                serial_port.close()
            self.worker.terminate()
            self.worker.wait(2000)

        with contextlib.suppress(Exception):
            self.vna.flushSerialBuffers()
        with contextlib.suppress(Exception):
            self.vna.reconnect()
            self._batch_last_connection_refresh_at = monotonic()

        self.worker = SweepWorker(self)
        self._connect_worker_signals()

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
        if self._batch_last_completed_index == self._batch_index:
            return
        self._batch_last_completed_index = self._batch_index
        self._batch_finished_wait_since = 0.0

        try:
            if self._batch_last_sweep_failed:
                logger.warning(
                    "Skipping save for failed sweep %d: %s",
                    self._batch_index,
                    self._batch_last_error_message,
                )
                self._batch_log_error(
                    self._batch_index, self._batch_last_error_message
                )
                self._batch_last_sweep_failed = False
                self._batch_last_error_message = ""
            else:
                try:
                    self._batch_save_current_touchstone()
                except Exception as exc:
                    logger.exception("Failed saving sweep %d", self._batch_index)
                    self._batch_log_error(
                        self._batch_index, f"save failed: {exc}"
                    )
        finally:
            if self._batch_reboot_reconnecting:
                return
            reboot_due = self._batch_should_reboot_before_next_sweep()
            if self._batch_active and self._batch_cfg and not reboot_due:
                self._refresh_vna_connection_between_batch_sweeps()
            if self._batch_active and self._batch_cfg:
                rest_ms = int(self._batch_cfg.rest_s * 1000)
                if self._batch_needs_recovery_cooldown:
                    rest_ms = max(rest_ms, BATCH_ERROR_RECOVERY_COOLDOWN_MS)
                    self._batch_needs_recovery_cooldown = False

                if reboot_due:
                    self._reboot_vna_for_batch(rest_ms)
                    return

                self._schedule_batch_action(rest_ms, self._batch_start_next)

    def _batch_should_reboot_before_next_sweep(self) -> bool:
        if not self._batch_active or not self._batch_cfg:
            return False
        if self._batch_index >= self._batch_cfg.count:
            return False
        return self._batch_index % self._batch_cfg.reboot_every == 0

    def _reboot_vna_for_batch(self, rest_ms: int) -> None:
        if not self._batch_active or not self._batch_cfg:
            return

        logger.info(
            "Rebooting NanoVNA after batch sweep %d", self._batch_index
        )
        try:
            reconnect_port = self._send_vna_reset_and_close()
        except Exception as exc:
            message = f"scheduled NanoVNA reboot failed: {exc}"
            logger.warning(message)
            self._batch_log_error(self._batch_index, message)
            self._record_vna_recovery_failure(exc)
            if self._batch_active:
                self._schedule_batch_action(
                    BATCH_ERROR_RECOVERY_COOLDOWN_MS,
                    lambda: self._reboot_vna_for_batch(rest_ms),
                )
            return

        def continue_batch() -> None:
            self._batch_reboot_reconnecting = False
            if not self._batch_active:
                return
            self._batch_last_connection_refresh_at = monotonic()
            self._batch_recovery_failures = 0
            self._schedule_batch_action(rest_ms, self._batch_start_next)

        def stop_batch() -> None:
            self._batch_reboot_reconnecting = False
            message = (
                "automatic reconnect after scheduled NanoVNA reboot timed out; "
                "stopping batch"
            )
            logger.error(message)
            self._batch_log_error(self._batch_index, message)
            self._stop_batch_after_unrecoverable_vna_failure()

        self._batch_reboot_reconnecting = True
        reconnect_window_s = (
            REBOOT_RECONNECT_INTERVAL_MS * REBOOT_RECONNECT_ATTEMPTS / 1000
        )
        self._batch_reboot_deadline = (
            monotonic() + reconnect_window_s + BATCH_SCHEDULE_GRACE_S
        )
        QtCore.QTimer.singleShot(
            REBOOT_RECONNECT_INTERVAL_MS,
            lambda: self._auto_reconnect_after_reboot(
                reconnect_port,
                REBOOT_RECONNECT_ATTEMPTS,
                on_connected=continue_batch,
                on_failed=stop_batch,
            ),
        )

    def _recover_vna_after_failed_batch_sweep(self) -> None:
        self._batch_needs_recovery_cooldown = True
        bandwidth = getattr(self.vna, "bandwidth", None)

        with contextlib.suppress(Exception):
            serial_port = getattr(self.vna, "serial", None)
            if hasattr(serial_port, "cancel_read"):
                serial_port.cancel_read()
            if hasattr(serial_port, "cancel_write"):
                serial_port.cancel_write()

        try:
            self.vna.reconnect()
            self.vna.resetSweep(int(self.sweep.start), int(self.sweep.end))
            if bandwidth is not None and "Bandwidth" in self.vna.features:
                self.vna.set_bandwidth(bandwidth)
            self._batch_last_connection_refresh_at = monotonic()
            self._batch_recovery_failures = 0
        except Exception as exc:
            logger.warning("VNA recovery after failed batch sweep failed: %s", exc)
            self._batch_log_error(
                self._batch_index,
                f"recovery failed: {exc}",
            )
            if "write failed" in str(exc).lower():
                self._start_batch_usb_reset_recovery(exc, bandwidth)
                return
            self._record_vna_recovery_failure(exc)

    def _start_batch_usb_reset_recovery(
        self, exc: Exception, bandwidth
    ) -> None:
        """Reset USB after serial recovery fails with a write timeout."""
        serial_port = getattr(self.vna, "serial", None)
        reconnect_port = getattr(serial_port, "port", None)
        reconnect_serial = getattr(serial_port, "usb_serial_number", None)

        with contextlib.suppress(Exception):
            if hasattr(serial_port, "cancel_read"):
                serial_port.cancel_read()
            if hasattr(serial_port, "cancel_write"):
                serial_port.cancel_write()
            serial_port.close()
        self.serial_control.mark_disconnected()

        if not _reset_usb_device_for_serial(reconnect_port):
            message = f"USB reset recovery failed after: {exc}"
            logger.error(message)
            self._batch_log_error(self._batch_index, message)
            self._record_vna_recovery_failure(exc)
            return

        logger.warning(
            "USB reset recovery started for batch sweep %d",
            self._batch_index,
        )
        self._batch_reboot_reconnecting = True
        reconnect_window_s = (
            REBOOT_RECONNECT_INTERVAL_MS * REBOOT_RECONNECT_ATTEMPTS / 1000
        )
        self._batch_reboot_deadline = (
            monotonic() + reconnect_window_s + BATCH_SCHEDULE_GRACE_S
        )

        def continue_batch() -> None:
            self._batch_reboot_reconnecting = False
            if not self._batch_active:
                return
            if bandwidth is not None and "Bandwidth" in self.vna.features:
                with contextlib.suppress(Exception):
                    self.vna.set_bandwidth(bandwidth)
            self._batch_last_connection_refresh_at = monotonic()
            self._batch_recovery_failures = 0
            self._schedule_batch_action(
                BATCH_ERROR_RECOVERY_COOLDOWN_MS,
                self._batch_start_next,
            )

        def stop_batch() -> None:
            self._batch_reboot_reconnecting = False
            message = "automatic reconnect after USB reset timed out"
            logger.error(message)
            self._batch_log_error(self._batch_index, message)
            self._stop_batch_after_unrecoverable_vna_failure()

        QtCore.QTimer.singleShot(
            3000,
            lambda: self._auto_reconnect_after_reboot(
                reconnect_port,
                REBOOT_RECONNECT_ATTEMPTS,
                on_connected=continue_batch,
                on_failed=stop_batch,
                reconnect_serial=reconnect_serial,
            ),
        )

    def _refresh_vna_connection_between_batch_sweeps(self) -> None:
        if not self.vna.connected():
            return

        elapsed_s = monotonic() - self._batch_last_connection_refresh_at
        sweep_refresh_due = (
            self._batch_index > 0
            and self._batch_index % BATCH_CONNECTION_REFRESH_SWEEPS == 0
        )
        time_refresh_due = elapsed_s >= BATCH_CONNECTION_REFRESH_INTERVAL_S
        if not sweep_refresh_due and not time_refresh_due:
            return

        logger.info(
            "Refreshing VNA connection after batch sweep %d (elapsed %.0fs)",
            self._batch_index,
            elapsed_s,
        )
        try:
            bandwidth = self.vna.bandwidth
            self.vna.reconnect()
            self.vna.resetSweep(int(self.sweep.start), int(self.sweep.end))
            if "Bandwidth" in self.vna.features:
                self.vna.set_bandwidth(bandwidth)
            self._batch_last_connection_refresh_at = monotonic()
            self._batch_recovery_failures = 0
        except Exception as exc:
            logger.warning("VNA connection refresh failed: %s", exc)
            self._batch_log_error(
                self._batch_index,
                f"connection refresh failed: {exc}",
            )
            self._record_vna_recovery_failure(exc)

    def _record_vna_recovery_failure(self, exc: Exception) -> None:
        self._batch_recovery_failures += 1
        if self._batch_recovery_failures < BATCH_MAX_RECOVERY_FAILURES:
            return

        message = (
            "VNA communication is not recovering "
            f"after {self._batch_recovery_failures} attempts: {exc}. "
            "Stopping batch; check or power-cycle the VNA."
        )
        logger.error(message)
        self._batch_log_error(self._batch_index, message)
        self._stop_batch_after_unrecoverable_vna_failure()

    def _stop_batch_after_unrecoverable_vna_failure(self) -> None:
        self._batch_active = False
        self._batch_cancel_pending_action()
        self._batch_reboot_reconnecting = False
        self._batch_watchdog.stop()
        self.btn_batch_stop.setDisabled(True)
        self.btn_batch_start.setDisabled(False)
        with contextlib.suppress(Exception):
            self.worker.quit()
        with contextlib.suppress(Exception):
            self.vna.disconnect()

    def _batch_log_error(self, index: int, message: str) -> None:
        if not self._batch_cfg:
            return

        fail_log = os.path.join(
            self._batch_cfg.out_dir,
            f"{self._batch_cfg.base_name}_errors.csv"
        )
        try:
            new_file = not os.path.exists(fail_log)
            with open(fail_log, "a", encoding="utf-8") as f:
                if new_file:
                    f.write("index,error\n")
                f.write(f'{index},"{message}"\n')
        except Exception:
            logger.exception("Failed writing batch error log")

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
            previous_freq = None
            for i, dp11 in enumerate(s11):
                if dp11.freq == previous_freq:
                    continue
                previous_freq = dp11.freq
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
                logger.warning(
                    "Temperature logging failed for sweep %d: %s", idx, exc
                )
                self._batch_log_error(idx, f"temperature failed: {exc}")
