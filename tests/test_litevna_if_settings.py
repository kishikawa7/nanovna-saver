import os
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6 import QtWidgets

from NanoVNASaver.Windows.DeviceSettings import DeviceSettingsWindow


def test_litevna_if_average_is_not_read_or_editable() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    vna = SimpleNamespace(
        name="LiteVNA-64",
        connected=Mock(return_value=True),
        getCalibration=Mock(return_value="Unknown"),
        get_features=Mock(return_value={"Customizable data points"}),
        read_if_average=Mock(return_value=5),
        set_if_average=Mock(),
        bandwidth=10,
        datapoints=201,
        valid_datapoints=(51, 101, 201),
        sweep_points_min=1,
        sweep_points_max=65535,
        version="2.2",
        hardware_revision="2.2",
        SN="NOT SUPPORTED",
    )
    app = SimpleNamespace(
        vna=vna,
        settings=SimpleNamespace(value=Mock(return_value=False)),
        worker=SimpleNamespace(isRunning=Mock(return_value=False)),
        calibration=SimpleNamespace(isCalculated=False),
    )
    window = DeviceSettingsWindow(app)

    window.updateFields()

    assert qt_app is not None
    assert not hasattr(window, "if_average")
    assert not window.bandwidth.isHidden()
    vna.read_if_average.assert_not_called()
    vna.set_if_average.assert_not_called()
