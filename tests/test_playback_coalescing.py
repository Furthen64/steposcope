from dataclasses import replace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QSettings

from step_explorer.ui.main_window import MainWindow


@pytest.fixture
def playback(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr('step_explorer.ui.main_window.QSettings',
                        lambda *_: QSettings(str(tmp_path / 'batch.ini'), QSettings.Format.IniFormat))
    clock = [0.0]
    monkeypatch.setattr('step_explorer.ui.main_window.monotonic', lambda: clock[0])
    source = tmp_path / 'points.step'
    source.write_text('ISO-10303-21;DATA;' + ''.join(
        f"#{i}=CARTESIAN_POINT('',({i}.,0.,0.));" for i in range(1, 13)
    ) + 'ENDSEC;END-ISO-10303-21;')
    window = MainWindow()
    window.open_path(str(source))
    window.fast_preview_checkbox.setChecked(True)
    window.FAST_PREVIEW_BATCH_SIZE = 1
    window.play_button.setChecked(True)
    window._present_playback_order = Mock(wraps=window._present_playback_order)
    window.tree.focus_playback_entity = Mock(wraps=window.tree.focus_playback_entity)
    try:
        yield window, clock, source
    finally:
        window.close()


def test_slices_do_not_update_gui_until_deadline_and_merge_all_changes(playback):
    window, clock, _ = playback
    for instant in (0.05, 0.1):
        clock[0] = instant
        window._playback_tick()
    assert window.playback_order == 2
    assert window.playback_progress.value() == 0
    window._present_playback_order.assert_not_called()
    window.tree.focus_playback_entity.assert_not_called()

    clock[0] = 0.3
    window._playback_tick()
    window._present_playback_order.assert_called_once()
    snapshot = window._present_playback_order.call_args.args[0]
    assert set(snapshot.render_updates) == {2, 3, 4}
    assert set(snapshot.points) == {1, 2, 3, 4}
    assert window.playback_progress.value() == 3
    window.tree.focus_playback_entity.assert_called_once_with(4)
    assert window._pending_playback_snapshot is None


def test_pause_flushes_latest_state_before_marker_inspection(playback):
    window, _, _ = playback
    window.show_positions_action.setChecked(False)
    window._playback_tick()
    assert window.current_snapshot.points.keys() == {1}
    window.play_button.setChecked(False)
    assert window.current_snapshot.points.keys() == {1, 2}
    assert window.playback_progress.value() == 1
    assert window.viewport._last_show_points
    assert not window.viewport.playback_locked
    assert window._pending_playback_snapshot is None


def test_seek_discards_stale_deltas_and_uses_complete_snapshot(playback):
    window, _, _ = playback
    window._playback_tick()
    window._set_playback_order(0)
    assert window._pending_playback_snapshot is None
    assert window.current_snapshot.points.keys() == {1}
    assert window._present_playback_order.call_args.kwargs['incremental'] is False
    window._playback_tick()
    window._set_playback_order(2)  # Even a forward seek must include deferred changes.
    assert window.current_snapshot.points.keys() == {1, 2, 3}
    assert window._present_playback_order.call_args.kwargs['incremental'] is False


def test_end_of_file_flushes_without_waiting_for_deadline(playback):
    window, _, _ = playback
    window._set_playback_order(10)
    window._present_playback_order.reset_mock()
    window._playback_tick()
    assert not window.play_button.isChecked()
    assert window.playback_progress.value() == 11
    assert len(window.current_snapshot.points) == 12
    assert window._pending_playback_snapshot is None
    window._present_playback_order.assert_called_once()


def test_open_and_full_redraw_do_not_leave_deferred_old_scene(playback):
    window, _, source = playback
    window._playback_tick()
    window.show_positions_action.setChecked(False)  # Style refresh promotes the latest built state.
    assert window.current_snapshot.points.keys() == {1, 2}
    assert window._pending_playback_snapshot is None
    window.play_button.setChecked(False)
    assert window.playback_progress.value() == 1
    window.play_button.setChecked(True)
    window._playback_tick()
    window.open_path(str(source))
    assert window._pending_playback_snapshot is None
    assert window.playback_order == 0
    assert window.current_snapshot.points.keys() == {1}


def test_full_redraw_requirement_survives_later_incremental_deltas(playback):
    window, _, _ = playback
    base = window.current_snapshot
    window._queue_playback_update(replace(base, render_updates=None))
    window._queue_playback_update(replace(base, render_updates={99: None}))
    window._flush_playback_updates()
    assert window._present_playback_order.call_args.args[0].render_updates is None
    assert window._present_playback_order.call_args.kwargs['incremental'] is False


def test_render_time_does_not_eat_next_progress_interval(playback):
    window, clock, _ = playback
    present = window._present_playback_order

    def expensive_render(*args, **kwargs):
        present(*args, **kwargs)
        clock[0] += 1.0

    window._present_playback_order = Mock(side_effect=expensive_render)
    clock[0] = 0.3
    window._playback_tick()
    assert clock[0] == 1.3
    clock[0] = 1.32
    window._playback_tick()
    assert window._present_playback_order.call_count == 1
    assert window._pending_playback_snapshot is not None


def test_heavy_file_waits_ten_seconds_between_presentations(playback):
    window, clock, _ = playback
    window.HEAVY_PLAYBACK_ENTITY_COUNT = 10
    assert window._playback_present_interval() == 10.0
    clock[0] = 9.9
    window._playback_tick()
    window._present_playback_order.assert_not_called()
    clock[0] = 10.0
    window._playback_tick()
    window._present_playback_order.assert_called_once()
    assert window.current_snapshot.points.keys() == {1, 2, 3}


def test_exit_discards_pending_work_without_paused_redraw(playback):
    window, _, _ = playback
    window._playback_tick()
    assert window._pending_playback_snapshot is not None
    window.close()
    assert window._pending_playback_snapshot is None
    assert not window.playback_timer.isActive()
    window._present_playback_order.assert_not_called()
