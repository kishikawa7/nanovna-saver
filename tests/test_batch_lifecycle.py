from types import SimpleNamespace
from unittest.mock import Mock

from NanoVNASaver.NanoVNASaver import NanoVNASaver


def test_batch_completion_skips_refresh_before_reboot() -> None:
    app = SimpleNamespace(
        _batch_active=True,
        _batch_cfg=SimpleNamespace(rest_s=1),
        _batch_index=100,
        _batch_last_completed_index=99,
        _batch_finished_wait_since=0.0,
        _batch_last_sweep_failed=False,
        _batch_reboot_reconnecting=False,
        _batch_needs_recovery_cooldown=False,
        _batch_save_current_touchstone=Mock(),
        _batch_should_reboot_before_next_sweep=Mock(return_value=True),
        _refresh_vna_connection_between_batch_sweeps=Mock(),
        _reboot_vna_for_batch=Mock(),
    )

    NanoVNASaver._on_sweep_finished_for_batch(app)
    NanoVNASaver._on_sweep_finished_for_batch(app)

    app._batch_save_current_touchstone.assert_called_once_with()
    app._refresh_vna_connection_between_batch_sweeps.assert_not_called()
    app._reboot_vna_for_batch.assert_called_once_with(1000)


def test_watchdog_waits_for_completion_callback() -> None:
    app = SimpleNamespace(
        _batch_active=True,
        _batch_cfg=object(),
        _batch_index=1511,
        _batch_last_completed_index=1510,
        _batch_finished_wait_since=0.0,
        _batch_reboot_reconnecting=False,
        _batch_pending_action=None,
        worker=Mock(),
        _stop_batch_after_unrecoverable_vna_failure=Mock(),
    )
    app.worker.isRunning.return_value = False

    NanoVNASaver._batch_watchdog_tick(app)

    assert app._batch_finished_wait_since > 0
    app._stop_batch_after_unrecoverable_vna_failure.assert_not_called()
