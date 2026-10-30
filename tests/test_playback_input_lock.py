from unittest.mock import Mock

from PySide6.QtCore import QEvent, QPointF, QSettings, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QLineEdit
from PySide6.QtTest import QTest

from step_explorer.ui.main_window import MainWindow


def test_playback_blocks_viewport_input_and_ctrl_p_restores_navigation(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        'step_explorer.ui.main_window.QSettings',
        lambda *_: QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat),
    )
    source = tmp_path / 'points.step'
    source.write_text("ISO-10303-21;DATA;#1=CARTESIAN_POINT('',(0.,0.,0.));"
                      "#2=CARTESIAN_POINT('',(1.,0.,0.));ENDSEC;END-ISO-10303-21;")
    window = MainWindow()
    try:
        window.open_path(str(source))
        view = window.viewport
        view._orbit_camera = Mock()
        view._pick_face = Mock()
        reset = Mock()
        monkeypatch.setattr(view, 'reset_camera', reset)
        window.play_button.setChecked(True)
        assert view.playback_locked
        assert not view._playback_notice.isHidden()
        assert 'Ctrl+P to pause' in view._playback_notice.text()
        assert not window.fit_view_button.isEnabled()
        assert not window.reset_view_action.isEnabled()

        def drag():
            for kind, point, button, buttons in (
                (QEvent.Type.MouseButtonPress, QPointF(10, 10), Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton),
                (QEvent.Type.MouseMove, QPointF(30, 20), Qt.MouseButton.NoButton, Qt.MouseButton.MiddleButton),
                (QEvent.Type.MouseButtonRelease, QPointF(30, 20), Qt.MouseButton.MiddleButton, Qt.MouseButton.NoButton),
            ):
                QApplication.sendEvent(view.widget, QMouseEvent(kind, point, point, button, buttons,
                                                               Qt.KeyboardModifier.NoModifier))

        drag()
        view._orbit_camera.assert_not_called()
        assert view.eventFilter(view.widget, QEvent(QEvent.Type.Wheel)) is True
        QApplication.sendEvent(view.widget, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_R,
                                                       Qt.KeyboardModifier.NoModifier))
        reset.assert_not_called()
        # Space is no longer a playback hotkey, and is consumed in the locked viewport.
        QApplication.sendEvent(view.widget, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space,
                                                       Qt.KeyboardModifier.NoModifier))
        assert view.playback_locked
        override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_P,
                             Qt.KeyboardModifier.ControlModifier)
        assert window.eventFilter(view.widget, override) is False
        assert view.eventFilter(view.widget, override) is False
        assert window.play_pause_action.shortcut().toString() == 'Ctrl+P'
        assert not window.play_pause_action.autoRepeat()
        window.play_pause_action.trigger()
        assert not window.play_button.isChecked()
        assert not window.playback_timer.isActive()
        assert not view.playback_locked
        assert view._playback_notice.isHidden()
        assert window.fit_view_button.isEnabled()
        assert window.reset_view_action.isEnabled()
        assert view._navigation_action is None
        drag()
        view._orbit_camera.assert_called_once()
    finally:
        window.close()


def test_ctrl_p_pauses_with_text_field_or_button_focus(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr('step_explorer.ui.main_window.QSettings',
                        lambda *_: QSettings(str(tmp_path / 'focus.ini'), QSettings.Format.IniFormat))
    source = tmp_path / 'focus.step'
    source.write_text("ISO-10303-21;DATA;#1=CARTESIAN_POINT('',(0.,0.,0.));"
                      "#2=CARTESIAN_POINT('',(1.,0.,0.));ENDSEC;END-ISO-10303-21;")
    window = MainWindow()
    try:
        window.open_path(str(source))
        window.viewport.hide()  # Exercise Qt shortcuts without creating a native render surface.
        field = QLineEdit(window)
        field.setText('inspect')
        window.show()
        window.activateWindow()
        qapp.processEvents()
        for focus in (field, window.open_button):
            window.play_pause_action.trigger()
            window.playback_timer.stop()  # Keep the tiny fixture from reaching EOF during event delivery.
            assert window.play_button.isChecked()
            focus.setFocus()
            qapp.processEvents()
            QTest.keyClick(focus, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier)
            assert not window.play_button.isChecked()
            assert not window.viewport.playback_locked
        assert field.text() == 'inspect'
    finally:
        window.close()
