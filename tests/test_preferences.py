from pathlib import Path

from PySide6.QtCore import QSettings

from step_explorer.config import AppConfig
from step_explorer.ui.main_window import MainWindow
from step_explorer.ui.preferences import PreferencesDialog


def test_friendly_playback_defaults_and_saved_overrides(qapp, tmp_path, monkeypatch):
    settings_path = tmp_path / "first_run.ini"
    monkeypatch.setattr(
        "step_explorer.ui.main_window.QSettings",
        lambda *_args: QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    window = MainWindow(config=AppConfig())
    try:
        assert window.autozoom_checkbox.isChecked()
        assert window.playback_speed_slider.value() == 100
        assert not window.fast_preview_checkbox.isChecked()
    finally:
        window.close()

    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    settings.setValue("autozoom", False)
    settings.setValue("playback_speed_percent", 55)
    settings.setValue("fast_preview", True)
    settings.sync()
    window = MainWindow(config=AppConfig())
    try:
        assert not window.autozoom_checkbox.isChecked()
        assert window.playback_speed_slider.value() == 55
        assert window.fast_preview_checkbox.isChecked()
    finally:
        window.close()


def test_preferences_categories_update_existing_controls_and_save_settings(qapp, tmp_path):
    window = MainWindow(config=AppConfig())
    window.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    dialog = PreferencesDialog(window)

    edit_menu = next(action.menu() for action in window.menuBar().actions() if action.text() == "Edit")
    preferences_action = next(action for action in edit_menu.actions() if action.text() == "Preferences…")
    assert {shortcut.toString() for shortcut in preferences_action.shortcuts()} == {
        "Ctrl+,", "Ctrl+Shift+P",
    }

    assert [dialog.categories.item(index).text() for index in range(dialog.categories.count())] == [
        "Navigation", "Viewport", "Labels", "Playback", "STEP Lines",
    ]
    initial_horizontal = window.invert_mouse_horizontal_action.isChecked()
    dialog.horizontal_check.setChecked(not initial_horizontal)
    assert window.invert_mouse_horizontal_action.isChecked() is not initial_horizontal
    assert window.viewport.invert_mouse_horizontal is not initial_horizontal
    assert window.settings.value("invert_mouse_horizontal", type=bool) is not initial_horizontal

    dialog.grid_scale.setValue(2.5)
    assert window.xy_grid_scale == 2.5
    assert window.settings.value("xy_grid_scale", type=float) == 2.5
    dialog.label_limit.setValue(120)
    assert window.viewport.max_entity_labels == 120
    assert window.settings.value("max_entity_labels", type=int) == 120
    dialog.playback_speed.setValue(70)
    assert window.playback_speed_slider.value() == 70
    assert window.playback_timer.interval() == 300
    assert window.settings.value("playback_speed_percent", type=int) == 70
    dialog.fast_preview_check.setChecked(True)
    dialog.fast_preview_check.setChecked(False)
    assert window.fast_preview_checkbox.isChecked() is False
    assert window.settings.value("fast_preview", type=bool) is False
    dialog.normal_display.setCurrentText("Vectors")
    assert window.normal_display.currentText() == "Vectors"
    assert window.settings.value("normal_display") == "Vectors"

    dialog.step_lines_palette.setCurrentText("High contrast")
    dialog.step_lines_palette.setCurrentText("Soft")
    dialog.step_lines_emojis.setChecked(True)
    dialog.step_lines_emojis.setChecked(False)
    dialog.step_lines_color_source.setChecked(False)
    dialog.step_lines_color_source.setChecked(True)
    preview = dialog.step_lines_preview.topLevelItem(0)
    assert dialog.step_lines_preview.isColumnHidden(1)
    assert preview.toolTip(1) == "VECTOR"
    assert preview.text(2).startswith("#115=VECTOR")
    assert preview.foreground(2).color().name() == "#a6d7e8"
    metadata_preview = dialog.step_lines_preview.topLevelItem(3)
    assert metadata_preview.text(1) == ""
    assert metadata_preview.foreground(2).color().name() == "#b5c0c9"
    assert window.settings.value("step_lines_palette") == "Soft"
    assert window.settings.value("step_lines_show_emojis", type=bool) is False
    assert window.settings.value("step_lines_color_source", type=bool) is True

    dialog.close()
    window.close()


def test_each_preferences_page_resets_live_controls_and_saved_settings(qapp, tmp_path):
    config = AppConfig(
        show_xyz_origin=False,
        show_xy_grid=False,
        xy_grid_color="#123456",
        xy_grid_scale=2.5,
        background_color="#345678",
        show_entity_labels=True,
        label_mode="type",
        max_entity_labels=77,
        autozoom=False,
    )
    window = MainWindow(config=config)
    window.settings = QSettings(str(tmp_path / "reset.ini"), QSettings.Format.IniFormat)
    dialog = PreferencesDialog(window)
    assert set(dialog.reset_buttons) == {
        "Navigation", "Viewport", "Labels", "Playback", "STEP Lines",
    }

    dialog.navigation_preset.setCurrentText("CAD")
    dialog.orbit_method.setCurrentText("Trackball")
    dialog.reset_buttons["Navigation"].click()
    assert dialog.navigation_preset.currentText() == "Blender"
    assert dialog.orbit_method.currentText() == "Turntable"
    assert not dialog.horizontal_check.isChecked()
    assert dialog.vertical_check.isChecked()
    assert window.settings.value("navigation_preset") == "Blender"

    dialog.axes_check.setChecked(True)
    dialog.grid_check.setChecked(True)
    dialog.grid_scale.setValue(4.0)
    window._set_xy_grid_color("#ffffff")
    window._set_background_color("#000000")
    dialog.mesh_edges_check.setChecked(True)
    dialog.edge_opacity.setValue(20)
    dialog.normal_display.setCurrentText("Vectors")
    dialog.arrow_limit.setValue(12)
    dialog.face_colors.setCurrentText("random")
    dialog.reset_buttons["Viewport"].click()
    assert not dialog.axes_check.isChecked() and not dialog.grid_check.isChecked()
    assert dialog.grid_scale.value() == 2.5
    assert dialog.grid_color.text() == "#123456"
    assert dialog.background_color.text() == "#345678"
    assert not dialog.mesh_edges_check.isChecked() and dialog.edge_opacity.value() == 60
    assert dialog.normal_display.currentText() == "Hidden"
    assert dialog.arrow_limit.value() == 48
    assert dialog.face_colors.currentText() == "normals"
    assert window.settings.value("xy_grid_color") == "#123456"

    dialog.labels_check.setChecked(False)
    dialog.label_content.setCurrentIndex(0)
    dialog.label_limit.setValue(120)
    dialog.reset_buttons["Labels"].click()
    assert dialog.labels_check.isChecked()
    assert dialog.label_content.currentData() == "type"
    assert dialog.label_limit.value() == 77
    assert window.settings.value("max_entity_labels", type=int) == 77

    dialog.autozoom_check.setChecked(True)
    dialog.playback_speed.setValue(70)
    dialog.fast_preview_check.setChecked(True)
    dialog.heal_check.setChecked(True)
    dialog.reset_buttons["Playback"].click()
    assert not dialog.autozoom_check.isChecked()
    assert dialog.playback_speed.value() == 100
    assert not dialog.fast_preview_check.isChecked()
    assert not dialog.heal_check.isChecked()
    assert window.settings.value("playback_speed_percent", type=int) == 100

    dialog.step_lines_palette.setCurrentText("Soft")
    dialog.step_lines_emojis.setChecked(False)
    dialog.step_lines_color_source.setChecked(True)
    dialog.reset_buttons["STEP Lines"].click()
    assert dialog.step_lines_palette.currentText() == "Classic"
    assert dialog.step_lines_emojis.isChecked()
    assert not dialog.step_lines_color_source.isChecked()
    assert not dialog.step_lines_preview.isColumnHidden(1)
    assert window.settings.value("step_lines_palette") == "Classic"

    dialog.close()
    window.close()


def test_playback_speed_slider_has_cpu_limited_maximum_and_zero_pauses(qapp, tmp_path):
    window = MainWindow(config=AppConfig())
    window.settings = QSettings(str(tmp_path / "speed.ini"), QSettings.Format.IniFormat)
    example = Path(__file__).resolve().parents[1] / "tests/corpus/planar_only/box125.step"
    window.open_path(str(example))
    assert "CPU" in window.playback_speed_help.toolTip()

    window.playback_speed_slider.setValue(100)
    assert window.playback_timer.interval() == 0
    window.play_button.setChecked(True)
    assert window.playback_timer.isActive()
    window.playback_speed_slider.setValue(0)
    assert not window.play_button.isChecked()
    assert not window.playback_timer.isActive()
    assert window.playback_timer.interval() == 1000
    assert window.playback_speed_value.text() == "0%"
    assert window.settings.value("playback_speed_percent", type=int) == 0
    window.close()


def test_enabling_turbo_sets_maximum_speed(qapp, tmp_path):
    window = MainWindow(config=AppConfig())
    window.settings = QSettings(str(tmp_path / "turbo.ini"), QSettings.Format.IniFormat)
    window.fast_preview_checkbox.setChecked(False)
    window.playback_speed_slider.setValue(35)

    window.fast_preview_checkbox.click()
    assert window.fast_preview_checkbox.isChecked()
    assert window.playback_speed_slider.value() == 100
    assert window.playback_timer.interval() == 0
    assert window.settings.value("playback_speed_percent", type=int) == 100

    window.playback_speed_slider.setValue(50)
    window.fast_preview_checkbox.click()
    assert not window.fast_preview_checkbox.isChecked()
    assert window.playback_speed_slider.value() == 50
    window.close()
