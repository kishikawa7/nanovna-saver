import importlib
import os
import subprocess
import sys
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6 import QtWidgets

from NanoVNASaver.NanoVNASaver import BatchSweepDialog
from NanoVNASaver.RFTools import Datapoint
from NanoVNASaver.temperature_logging import TemperatureLoggingManager
from NanoVNASaver.Touchstone import Touchstone
from tools import setuptools_wrapper


def test_ui_build_uses_current_python_and_reports_failure(monkeypatch):
    run = Mock(side_effect=subprocess.CalledProcessError(1, "ui-compile"))
    monkeypatch.setattr(setuptools_wrapper.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        setuptools_wrapper.compile_ui()
    run.assert_called_once_with(
        [sys.executable, "-m", "src.tools.ui_compile"], check=True
    )


def test_build_requirements_do_not_compile_before_qt_is_installed(monkeypatch):
    compile_ui = Mock()
    monkeypatch.setattr(setuptools_wrapper, "compile_ui", compile_ui)
    monkeypatch.setattr(
        setuptools_wrapper._orig, "get_requires_for_build_wheel",
        Mock(return_value=[]),
    )
    requirements = setuptools_wrapper.get_requires_for_build_editable()
    assert "pyside6~=6.8" in requirements
    compile_ui.assert_not_called()


def test_usb_reset_skips_linux_system_calls_on_windows(monkeypatch):
    module = importlib.import_module("NanoVNASaver.Hardware.VNA")
    resolve = Mock()
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module, "_usb_device_node_for_serial", resolve)
    assert not module._reset_usb_device_for_serial("COM12")
    resolve.assert_not_called()


def test_batch_dialog_accepts_windows_pico_port(monkeypatch):
    module = importlib.import_module("NanoVNASaver.NanoVNASaver")
    monkeypatch.setattr(
        module.list_ports, "comports",
        lambda: [SimpleNamespace(device="COM12")],
    )
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    parent = QtWidgets.QWidget()
    parent.temperature_logger = TemperatureLoggingManager("")
    dialog = BatchSweepDialog(parent)
    assert not dialog.get_config().save_temperature
    dialog.temp_checkbox.setChecked(True)
    dialog.pico_port_combo.setCurrentText("COM12")
    assert dialog.get_config().pico_port == "COM12"
    assert dialog.get_config().save_temperature
    assert dialog.pico_port_combo.isEnabled()
    assert qt_app is not None
    dialog.close()
    parent.close()


def test_failed_pico_connection_releases_windows_port(monkeypatch):
    module = importlib.import_module("NanoVNASaver.temperature_logging")
    client = Mock()
    client.open.side_effect = RuntimeError("Wrong device on COM12")
    monkeypatch.setattr(module, "PicoTempClient", Mock(return_value=client))
    manager = TemperatureLoggingManager("COM12")
    assert not manager.connect()
    client.close.assert_called_once()
    assert manager.client is None


def test_changing_pico_port_closes_previous_connection():
    manager = TemperatureLoggingManager("COM12")
    previous_client = Mock()
    manager.client = previous_client
    manager.set_port("COM14")
    previous_client.close.assert_called_once()
    assert manager.pico_port == "COM14"
    assert manager.client is None


def test_reference_folder_reload_clears_calibration_caches(
    tmp_path, monkeypatch
):
    module = importlib.import_module("NanoVNASaver.NanoVNASaver")
    folder = tmp_path / "校正データ"
    folder.mkdir()
    sample = Path(__file__).parent / "data" / "valid.s2p"
    names = (
        "short_P1", "open_P1", "load_P1",
        "short_P2", "open_P2", "load_P2", "thru",
    )
    for name in names:
        (folder / f"S_{name}.s2p").write_bytes(sample.read_bytes())
    # Save/restore all module state, including values changed by the setter.
    for name in (
        "kVNApath", "ansdata", "calpath", "caldata", "freq", "media",
        "ideals", "ans_ideals", "measured", "cal", "_cal_cache_key",
        "_cal_cache", "_ideal_cal_cache_key", "_ideal_cal_cache",
    ):
        monkeypatch.setattr(module, name, getattr(module, name))
    monkeypatch.setattr(module, "caldata", {"old": True})
    monkeypatch.setattr(module, "calpath", str(tmp_path / "measurements"))
    monkeypatch.setattr(module, "_cal_cache_key", (1, 2))
    monkeypatch.setattr(module, "_ideal_cal_cache_key", (1, 2))
    load = Mock(return_value=({"new": True}, None, None, [], [], [], None))
    monkeypatch.setattr(module, "_load_external_calibration", load)

    assert module.set_external_reference_path(str(folder))
    assert set(module.ansdata) == set(names)
    assert Path(module.kVNApath) == folder
    assert load.call_args.args[1] is module.ansdata
    assert module.caldata == {"new": True}
    assert module._cal_cache_key == ()
    assert module._ideal_cal_cache_key == ()

    # An incomplete replacement must preserve the currently usable calibration.
    old_references = module.ansdata
    assert not module.set_external_reference_path(str(tmp_path / "missing"))
    assert module.ansdata is old_references
    assert Path(module.kVNApath) == folder
    assert module.caldata == {"new": True}


@pytest.mark.parametrize("ideal_available", [False, True])
def test_batch_preserves_raw_sweep_without_reference_files(
    tmp_path, monkeypatch, ideal_available
):
    module = importlib.import_module("NanoVNASaver.NanoVNASaver")

    def calibrate(network, ideal=False):
        if ideal and ideal_available:
            return network.copy()
        raise FileNotFoundError("Calibration files have not been copied yet")

    monkeypatch.setattr(module, "apply_external_calibration", calibrate)
    monkeypatch.setattr(module, "calpath", str(tmp_path / "measurements"))
    app = SimpleNamespace(
        _batch_cfg=SimpleNamespace(
            out_dir=str(tmp_path), base_name="sweep", save_temperature=False
        ),
        _batch_index=1,
        dataLock=Lock(),
        data=SimpleNamespace(
            s11=[Datapoint(1_000_000, 0.1, 0.0)],
            s21=[Datapoint(1_000_000, 0.2, 0.0)],
        ),
    )
    module.NanoVNASaver._batch_save_current_touchstone(app)
    raw = Touchstone(str(tmp_path / "sweep_00000001.s2p"))
    raw.load()
    assert raw.s11 == app.data.s11
    assert raw.s21 == app.data.s21
    assert not (tmp_path / "sweep_00000001_cal.s2p").exists()
    assert (tmp_path / "sweep_00000001_ical.s2p").exists() == ideal_available
    assert (tmp_path / "sweep_00000001_ical.md").exists() == ideal_available
