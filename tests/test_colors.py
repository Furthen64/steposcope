from step_explorer.step.parser import parse_step
from step_explorer.ui.colors import category_for_entity, category_for_type, color_for_category, color_for_depth


def test_semantic_colors_shade_with_depth():
    shallow = color_for_depth("Topology", 0)
    deep = color_for_depth("Topology", 6)
    assert shallow != deep
    assert all(deep_component < shallow_component for deep_component, shallow_component in zip(deep, shallow))


def test_control_net_color_is_distinct_from_geometry():
    assert color_for_category("Control Net") != color_for_category("Geometry")


def test_spline_surfaces_and_geometric_complex_instances_are_geometry():
    assert category_for_type("B_SPLINE_SURFACE_WITH_KNOTS") == "Geometry"
    assert category_for_type("CONICAL_SURFACE") == "Geometry"
    document = parse_step(
        "ISO-10303-21;DATA;"
        "#1=(B_SPLINE_SURFACE() RATIONAL_B_SPLINE_SURFACE());"
        "#2=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));"
        "ENDSEC;END-ISO-10303-21;"
    )
    assert category_for_entity(document.entity(1)) == "Geometry"
    assert category_for_entity(document.entity(2)) == "Structure"
