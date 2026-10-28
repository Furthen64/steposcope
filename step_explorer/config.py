"""Application settings loaded from an optional TOML file."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from math import isfinite
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    """Settings that affect the interactive application."""

    invert_mouse_horizontal: bool = False
    invert_mouse_vertical: bool = False
    show_entity_labels: bool = False
    autozoom: bool = True
    label_mode: str = "index"
    max_entity_labels: int = 250
    show_xyz_origin: bool = True
    show_xy_grid: bool = True
    xy_grid_color: str = "#4d5966"
    xy_grid_scale: float = 1.0
    background_color: str = "#46596d"


def default_config_paths() -> tuple[Path, ...]:
    """Return config locations in precedence order.

    A local file is convenient for a portable checkout, while the XDG path
    gives an installed application a normal per-user config location.
    ``STEPOSCOPE_CONFIG`` is useful when launching the application from a
    desktop shortcut or when testing a configuration in isolation.
    """

    paths: list[Path] = []
    configured_path = os.environ.get("STEPOSCOPE_CONFIG")
    if configured_path:
        paths.append(Path(configured_path).expanduser())
    paths.append(Path.cwd() / "steposcope.toml")

    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    paths.append(config_home / "steposcope" / "config.toml")
    return tuple(paths)


def find_config_path() -> Path | None:
    """Find the first existing config file, if any."""

    return next((path for path in default_config_paths() if path.is_file()), None)


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load settings from *path* or the first discovered config file.

    Configuration is optional. A missing, unreadable, malformed, or invalid
    file falls back to the safe defaults so a typo cannot prevent the viewer
    from starting.
    """

    config_path = Path(path).expanduser() if path is not None else find_config_path()
    if config_path is None:
        return AppConfig()

    try:
        with config_path.open("rb") as config_file:
            values = tomllib.load(config_file)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return AppConfig()

    legacy_invert = values.get("invert_mouse_rotation", False)
    legacy_invert = legacy_invert if isinstance(legacy_invert, bool) else False
    invert_horizontal = values.get("invert_mouse_horizontal", legacy_invert)
    invert_vertical = values.get("invert_mouse_vertical", legacy_invert)
    show_labels = values.get("show_entity_labels", False)
    autozoom = values.get("autozoom", True)
    label_mode = values.get("label_mode", "index")
    max_entity_labels = values.get("max_entity_labels", 250)
    show_xyz_origin = values.get("show_xyz_origin", True)
    show_xy_grid = values.get("show_xy_grid", True)
    xy_grid_color = values.get("xy_grid_color", "#4d5966")
    xy_grid_scale = values.get("xy_grid_scale", 1.0)
    background_color = values.get("background_color", "#46596d")
    if not isinstance(label_mode, str) or label_mode not in {"index", "type"}:
        label_mode = "index"
    if isinstance(max_entity_labels, bool) or not isinstance(max_entity_labels, int):
        max_entity_labels = 250
    else:
        max_entity_labels = max(1, min(2000, max_entity_labels))
    if not isinstance(xy_grid_color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", xy_grid_color):
        xy_grid_color = "#4d5966"
    if not isinstance(background_color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", background_color):
        background_color = "#46596d"
    if (
        isinstance(xy_grid_scale, bool)
        or not isinstance(xy_grid_scale, (int, float))
        or not isfinite(float(xy_grid_scale))
    ):
        xy_grid_scale = 1.0
    else:
        xy_grid_scale = max(0.25, min(10.0, float(xy_grid_scale)))
    return AppConfig(
        invert_mouse_horizontal=invert_horizontal if isinstance(invert_horizontal, bool) else False,
        invert_mouse_vertical=invert_vertical if isinstance(invert_vertical, bool) else False,
        show_entity_labels=show_labels if isinstance(show_labels, bool) else False,
        autozoom=autozoom if isinstance(autozoom, bool) else True,
        label_mode=label_mode,
        max_entity_labels=max_entity_labels,
        show_xyz_origin=show_xyz_origin if isinstance(show_xyz_origin, bool) else True,
        show_xy_grid=show_xy_grid if isinstance(show_xy_grid, bool) else True,
        xy_grid_color=xy_grid_color,
        xy_grid_scale=xy_grid_scale,
        background_color=background_color,
    )


__all__ = ["AppConfig", "default_config_paths", "find_config_path", "load_config"]
