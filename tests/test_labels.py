from step_explorer.ui.main_window import _label_for_entity_type
from step_explorer.visualization.vtk_view import VtkView, _LabelSpec


def test_rendered_entity_types_have_compact_labels():
    assert _label_for_entity_type("VERTEX_POINT") == "Vertex"
    assert _label_for_entity_type("EDGE_CURVE") == "Edge"
    assert _label_for_entity_type("ADVANCED_FACE") == "Face"
    assert _label_for_entity_type("MANIFOLD_SOLID_BREP") == "BREP"
    assert _label_for_entity_type("B_SPLINE_CURVE_WITH_KNOTS") == "NURBS"


def test_label_candidates_are_spatially_thinned_and_capped():
    view = VtkView.__new__(VtkView)
    view.max_entity_labels = 2
    view._world_to_display = lambda position: (*position, 0.5)
    view._label_specs = [
        _LabelSpec(1, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), 1.0, "#1", False, 3),
        _LabelSpec(2, (100.0, 100.0, 0.0), (1.0, 1.0, 1.0), 1.0, "#2", False, 0),
        _LabelSpec(3, (100.0, 100.0, 0.0), (1.0, 1.0, 1.0), 1.0, "#3", False, 3),
        _LabelSpec(4, (200.0, 200.0, 0.0), (1.0, 1.0, 1.0), 1.0, "#4", True, 3),
    ]

    candidates = view._visible_label_candidates(400, 400)

    assert len(candidates) == 2
    assert {candidate[0].entity_id for candidate in candidates} == {2, 4}


def test_newer_labels_are_selected_first_and_oldest_labels_fade():
    view = VtkView.__new__(VtkView)
    view.max_entity_labels = 5
    view._world_to_display = lambda position: (*position, 0.5)
    view._label_specs = [
        _LabelSpec(
            entity_id,
            (entity_id * 100.0, 0.0, 0.0),
            (0.3, 0.7, 1.0),
            1.0,
            f"#{entity_id}",
            False,
            1,
            entity_id,
        )
        for entity_id in range(1, 6)
    ]

    candidates = view._visible_label_candidates(600, 200)

    assert [candidate[0].entity_id for candidate in candidates] == [5, 4, 3, 2, 1]
    assert candidates[0][4] == 0.0
    assert candidates[-1][4] > 0.0
