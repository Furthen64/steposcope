"""Appearance presets for the STEP playback listing."""

from .colors import category_for_type

STEP_LINE_PALETTES = {
    "Classic": {
        "Topology": "#ffb74d",
        "Geometry": "#4fc3f7",
        "Structure": "#ce93d8",
        "Metadata": "#90a4ae",
    },
    "Soft": {
        "Topology": "#eac99a",
        "Geometry": "#a6d7e8",
        "Structure": "#d6bae6",
        "Metadata": "#b5c0c9",
    },
    "High contrast": {
        "Topology": "#ffcf40",
        "Geometry": "#59e1ff",
        "Structure": "#ef9dff",
        "Metadata": "#f2f4f8",
    },
}

STEP_LINE_MARKERS = {
    "Topology": "■",
    "Geometry": "●",
    "Structure": "◆",
    "Metadata": "",
}


def step_line_appearance(type_name: str, palette: str, category: str | None = None) -> tuple[str, str]:
    category = category or category_for_type(type_name)
    colors = STEP_LINE_PALETTES.get(palette, STEP_LINE_PALETTES["Classic"])
    if type_name.upper() == "ADVANCED_FACE":
        return "F", colors[category]
    return STEP_LINE_MARKERS[category], colors[category]
