from types import SimpleNamespace
from unittest.mock import Mock

from NanoVNASaver.NanoVNASaver import NanoVNASaver
from NanoVNASaver.Settings.Sweep import Sweep


def test_litevna_defaults_apply_once() -> None:
    settings = SimpleNamespace(
        averages_input=Mock(),
        truncates_input=Mock(),
    )
    app = SimpleNamespace(
        _litevna_defaults_applied=False,
        vna=SimpleNamespace(name="LiteVNA-64", datapoints=201),
        sweep=Sweep(),
        windows={"sweep_settings": settings},
    )

    assert NanoVNASaver._apply_litevna_defaults(app)
    assert (app.sweep.start, app.sweep.end) == (50_000, 6_000_000_000)
    assert app.sweep.properties.averages == (3, 0)
    settings.averages_input.setText.assert_called_once_with("3")
    settings.truncates_input.setText.assert_called_once_with("0")

    app.sweep.update(1_000_000, 2_000_000, 6, 201)
    assert not NanoVNASaver._apply_litevna_defaults(app)
    assert (app.sweep.start, app.sweep.end) == (1_000_000, 2_000_000)
