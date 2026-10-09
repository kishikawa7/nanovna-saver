import importlib
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from NanoVNASaver.NanoVNASaver import NanoVNASaver
from NanoVNASaver.RFTools import Datapoint
from NanoVNASaver.Touchstone import Touchstone
from NanoVNASaver.Windows.Files import FilesWindow


def test_batch_saves_raw_and_calibrated_s2p(tmp_path, monkeypatch):
    app_module = importlib.import_module("NanoVNASaver.NanoVNASaver")

    def calibrate(network, ideal=False):
        result = network.copy()
        result.s[:, 0, 0] *= 4 if ideal else 2
        result.s[:, 1, 0] *= 5 if ideal else 3
        return result

    monkeypatch.setattr(app_module, "apply_external_calibration", calibrate)
    monkeypatch.setattr(app_module, "calpath", str(tmp_path / "calibration"))
    monkeypatch.setattr(app_module, "kVNApath", str(tmp_path / "reference"))
    app = SimpleNamespace(
        _batch_cfg=SimpleNamespace(
            out_dir=str(tmp_path), base_name="sweep", save_temperature=False
        ),
        _batch_index=7,
        dataLock=Lock(),
        data=SimpleNamespace(
            s11=[Datapoint(1_000_000, 0.1, 0.2)],
            s21=[Datapoint(1_000_000, 0.3, 0.4)],
        ),
    )

    NanoVNASaver._batch_save_current_touchstone(app)

    raw = Touchstone(str(tmp_path / "sweep_00000007.s2p"))
    calibrated = Touchstone(str(tmp_path / "sweep_00000007_cal.s2p"))
    ideal_calibrated = Touchstone(str(tmp_path / "sweep_00000007_ical.s2p"))
    raw.load()
    calibrated.load()
    ideal_calibrated.load()
    assert (raw.s11[0].re, raw.s21[0].re) == (0.1, 0.3)
    assert (calibrated.s11[0].re, calibrated.s21[0].re) == pytest.approx(
        (0.2, 0.9)
    )
    assert (ideal_calibrated.s11[0].re, ideal_calibrated.s21[0].re) == pytest.approx(
        (0.4, 1.5)
    )
    for data in (raw, calibrated, ideal_calibrated):
        assert data.s12 == data.s21
        assert data.s22 == data.s11
    note = (tmp_path / "sweep_00000007_cal.md").read_text(encoding="utf-8")
    assert str(tmp_path / "calibration" / "S_short.s2p") in note
    assert str(tmp_path / "reference" / "S_short_P1.s2p") in note
    ideal_note = (tmp_path / "sweep_00000007_ical.md").read_text(encoding="utf-8")
    assert "ideal SOLT" in ideal_note
    assert "S_short_P1.s2p" not in ideal_note


def test_manual_calibrated_export_adds_cal_suffix(tmp_path, monkeypatch):
    from NanoVNASaver.Windows import Files as files_module

    filename = tmp_path / "measurement.s2p"
    dialog = Mock()
    dialog.exec.return_value = True
    dialog.selectedFiles.return_value = [str(filename)]
    monkeypatch.setattr(files_module, "safe_file_dialog", lambda parent: dialog)
    window = SimpleNamespace(
        export_calibration=SimpleNamespace(currentIndex=lambda: 1),
        app=SimpleNamespace(
            dataLock=Lock(),
            data=SimpleNamespace(
                s11=[Datapoint(1_000_000, 0.1, 0.0)],
                s21=[Datapoint(1_000_000, 0.2, 0.0)],
            ),
            calibrated_s11=[Datapoint(1_000_000, 0.3, 0.0)],
            calibrated_s21=[Datapoint(1_000_000, 0.4, 0.0)],
            calibrated_calpath=str(tmp_path / "calibration"),
            calibrated_reference_path=str(tmp_path / "reference"),
        ),
    )

    FilesWindow.exportFile(window, 4)

    assert not filename.exists()
    exported = Touchstone(str(tmp_path / "measurement_cal.s2p"))
    exported.load()
    assert (exported.s11[0].re, exported.s21[0].re) == (0.3, 0.4)
    assert exported.s12 == exported.s21
    assert exported.s22 == exported.s11
    note = (tmp_path / "measurement_cal.md").read_text(encoding="utf-8")
    assert str(tmp_path / "calibration" / "S_thru.s2p") in note
    assert str(tmp_path / "reference" / "S_thru.s2p") in note


def test_manual_ideal_export_uses_ical_suffix(tmp_path, monkeypatch):
    from NanoVNASaver.Windows import Files as files_module

    dialog = Mock()
    dialog.exec.return_value = True
    dialog.selectedFiles.return_value = [str(tmp_path / "measurement.s2p")]
    monkeypatch.setattr(files_module, "safe_file_dialog", lambda parent: dialog)
    window = SimpleNamespace(
        export_calibration=SimpleNamespace(currentIndex=lambda: 2),
        app=SimpleNamespace(
            dataLock=Lock(),
            ideal_calibrated_s11=[Datapoint(1_000_000, 0.5, 0.0)],
            ideal_calibrated_s21=[Datapoint(1_000_000, 0.6, 0.0)],
            calibrated_calpath=str(tmp_path / "calibration"),
        ),
    )

    FilesWindow.exportFile(window, 4)

    exported = Touchstone(str(tmp_path / "measurement_ical.s2p"))
    exported.load()
    assert (exported.s11[0].re, exported.s21[0].re) == (0.5, 0.6)
    note = (tmp_path / "measurement_ical.md").read_text(encoding="utf-8")
    assert "ideal SOLT" in note
    assert "S_short_P1.s2p" not in note


def test_ideal_solt_uses_zero_load_reflection(monkeypatch):
    import numpy as np
    from skrf.constants import c
    from skrf.frequency import Frequency
    from skrf.media.media import DefinedGammaZ0

    app_module = importlib.import_module("NanoVNASaver.NanoVNASaver")
    frequency = Frequency.from_f([1e6, 2e6, 3e6], unit="hz")
    beta = 2 * np.pi * frequency.f / c
    media = DefinedGammaZ0(frequency=frequency, z0=50, gamma=1j * beta)
    standards = (
        media.short(nports=2), media.open(nports=2),
        media.match(nports=2), media.thru(),
    )
    monkeypatch.setattr(app_module, "caldata", {
        name: {"ntwk": standard}
        for name, standard in zip(
            ("short", "open", "load", "thru"), standards, strict=True
        )
    })
    monkeypatch.setattr(app_module, "_ideal_cal_cache_key", ())

    calibration = app_module._calibration_at_frequency(frequency, ideal=True)
    assert np.allclose(calibration.ideals[0].s[:, 0, 0], -1)
    assert np.allclose(calibration.ideals[1].s[:, 0, 0], 1)
    assert np.allclose(calibration.ideals[2].s[:, 0, 0], 0)
    assert np.allclose(calibration.ideals[3].s[:, 1, 0], 1)
    assert np.allclose(
        app_module.apply_external_calibration(standards[3], ideal=True).s,
        standards[3].s,
    )
