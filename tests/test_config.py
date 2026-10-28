from types import SimpleNamespace

from PySide6.QtCore import QSettings

from step_explorer.config import AppConfig, load_config
from step_explorer.ui.main_window import MainWindow


def test_mouse_rotation_setting_is_loaded_from_toml(tmp_path):
    config_path = tmp_path / "steposcope.toml"
    config_path.write_text(
        "invert_mouse_rotation = true\nshow_entity_labels = true\nautozoom = false\nlabel_mode = \"type\"\nmax_entity_labels = 400\n",
        encoding="utf-8",
    )

    assert load_config(config_path) == AppConfig(
        invert_mouse_horizontal=True,
        invert_mouse_vertical=True,
        show_entity_labels=True,
        autozoom=False,
        label_mode="type",
        max_entity_labels=400,
    )


def test_label_limit_is_clamped(tmp_path):
    config_path = tmp_path / "labels.toml"
    config_path.write_text("max_entity_labels = 5000\n", encoding="utf-8")

    assert load_config(config_path).max_entity_labels == 2000


def test_missing_and_invalid_config_use_defaults(tmp_path):
    assert load_config(tmp_path / "missing.toml") == AppConfig()

    config_path = tmp_path / "invalid.toml"
    config_path.write_text("invert_mouse_rotation = \"yes\"\nautozoom = \"no\"\n", encoding="utf-8")
    assert load_config(config_path) == AppConfig()


def test_mouse_axes_can_be_configured_independently(tmp_path):
    config_path = tmp_path / "steposcope.toml"
    config_path.write_text(
        "invert_mouse_rotation = true\n"
        "invert_mouse_horizontal = false\n"
        "invert_mouse_vertical = true\n",
        encoding="utf-8",
    )

    assert load_config(config_path) == AppConfig(
        invert_mouse_horizontal=False,
        invert_mouse_vertical=True,
    )


def test_saved_axis_settings_override_legacy_rotation_setting(tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("invert_mouse_rotation", True)
    settings.setValue("invert_mouse_horizontal", False)
    window = SimpleNamespace(settings=settings, config=AppConfig())

    assert MainWindow._load_invert_mouse_axis(window, "horizontal") is False
    assert MainWindow._load_invert_mouse_axis(window, "vertical") is True
