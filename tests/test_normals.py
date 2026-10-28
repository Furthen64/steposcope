import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from step_explorer.geometry.builder import GeometryBuilder, Mesh, Point3
from step_explorer.step.parser import parse_step
from step_explorer.ui.main_window import MainWindow
from step_explorer.visualization.vtk_view import VtkView, vtk
from PySide6.QtWidgets import QComboBox

from test_headless import _box_source


def _heal_pair(flipped_face=2):
    document = parse_step(_box_source(flipped_face=flipped_face))
    builder = GeometryBuilder(document, heal_orientations=False)
    raw = builder.build()
    builder.heal_orientations = True
    healed = builder.build()
    return raw, healed


def test_unhealed_preview_preserves_boundary_orientation():
    raw, healed = _heal_pair()
    assert len(raw.faces) == len(healed.faces) == 6
    flipped = raw.faces[2]
    assert flipped.orientation_warning is not None
    for original, aligned in zip(raw.faces, healed.faces):
        assert original.points == aligned.points
        assert aligned.orientation_warning is None
        expected = (tuple((a, c, b) for a, b, c in aligned.triangles)
                    if original.orientation_warning else aligned.triangles)
        assert original.triangles == expected


def test_healed_preview_winds_every_face_the_same_way(view):
    raw, healed = _heal_pair()
    assert [mesh.entity_id for mesh in raw.faces] == [mesh.entity_id for mesh in healed.faces]


def test_orientation_balloon_click_opens_face_diagnostic(qapp, view):
    view._position_warning_balloons = Mock()
    handler = Mock()
    view.orientation_warning_clicked.connect(handler)
    builder = GeometryBuilder(parse_step(_box_source(flipped_face=2)), heal_orientations=False)
    view._update_warning_balloons(builder.build())
    assert len(view._warning_balloons) == 1
    button, _ = view._warning_balloons[0]
    flagged = button.text().split()[0]
    assert flagged.startswith("#")
    button.click()
    entity_id, explanation = handler.call_args.args
    assert flagged == f"#{entity_id}"
    assert explanation
    builder.heal_orientations = True
    view._update_warning_balloons(builder.build())
    assert view._warning_balloons == []


def test_heal_toggle_rebuilds_current_preview():
    window = SimpleNamespace(geometry_builder=SimpleNamespace(heal_orientations=False),
                             _refresh_viewport=Mock(), settings=Mock())
    MainWindow._heal_changed(window, True)
    assert window.geometry_builder.heal_orientations
    window._refresh_viewport.assert_called_once_with()


@pytest.mark.parametrize("surface_type", ["PLANE", "CYLINDRICAL_SURFACE"])
@pytest.mark.parametrize("sense, expected", [("T", (0, 1, 2)), ("F", (0, 2, 1))])
def test_face_preview_winding_follows_step_surface_sense(surface_type, sense, expected):
    surface_arguments = "'',#3" if surface_type == "PLANE" else "'',#3,1."
    source = f"""ISO-10303-21;DATA;
    #1=ADVANCED_FACE('',(),#2,.{sense}.);
    #2={surface_type}({surface_arguments});
    #3=AXIS2_PLACEMENT_3D('',#4,#5,$);
    #4=CARTESIAN_POINT('',(0.,0.,0.));
    #5=DIRECTION('',(0.,0.,1.));
    ENDSEC;END-ISO-10303-21;"""
    document = parse_step(source)
    builder = GeometryBuilder(document)
    points = (
        (Point3(0, 0, 0), Point3(1, 0, 0), Point3(0, 1, 0))
        if surface_type == "PLANE" else
        (Point3(1, 0, 0), Point3(0.5, 0.8660254, 0), Point3(1, 0, 1))
    )

    assert builder._oriented_face_triangles(document.entity(1), document.entity(2), points, ((0, 1, 2),)) == (expected,)


@pytest.mark.skipif(vtk is None, reason="VTK is not installed")
def test_face_orientation_colors_front_and_back_without_transparency(view):
    view.normal_display = "Hidden"
    view.face_color_mode = "normals"
    mesh = Mesh(7, (Point3(0, 0, 0), Point3(1, 0, 0), Point3(0, 1, 0)), ((0, 1, 2),))

    view._add_mesh(mesh, selected=False, labels=False, label_scale=1.0, label_text="")

    actor = view._actors[0]
    assert actor.GetProperty().GetOpacity() == 1.0
    assert actor.GetProperty().GetColor() == pytest.approx((0.2, 0.5, 1.0))
    assert actor.GetBackfaceProperty().GetColor() == pytest.approx((1.0, 0.25, 0.2))


@pytest.mark.skipif(vtk is None, reason="VTK is not installed")
def test_random_face_coloring_is_stable_per_face(view):
    view.normal_display = "Hidden"
    view.face_color_mode = "random"
    first = Mesh(7, (Point3(0, 0, 0), Point3(1, 0, 0), Point3(0, 1, 0)), ((0, 1, 2),))
    second = Mesh(8, (Point3(2, 0, 0), Point3(3, 0, 0), Point3(2, 1, 0)), ((0, 1, 2),))

    view._add_mesh(first, selected=False, labels=False, label_scale=1.0, label_text="")
    view._add_mesh(second, selected=False, labels=False, label_scale=1.0, label_text="")

    assert view._actors[0].GetProperty().GetOpacity() == 1.0
    assert view._actors[0].GetProperty().GetColor() == pytest.approx(view._mesh_color(7))
    assert view._actors[0].GetProperty().GetColor() != pytest.approx(view._actors[1].GetProperty().GetColor())


def test_face_orientation_selects_normal_face_colors(qapp, monkeypatch):
    face_color_mode = QComboBox()
    face_color_mode.addItems(VtkView.FACE_COLOR_MODES)
    window = SimpleNamespace(face_color_mode=face_color_mode,
                             viewport=SimpleNamespace(set_normal_display=Mock()), settings=Mock())

    MainWindow._normal_display_changed(window, "Face orientation")

    assert face_color_mode.currentText() == "normals"
    window.viewport.set_normal_display.assert_called_once_with("Face orientation")
    face_color_mode.close()


@pytest.mark.skipif(vtk is None, reason="VTK is not installed")
def test_normal_vectors_add_directional_arrows(view):
    view.normal_display = "Vectors"
    view._normal_length = 1.0
    mesh = Mesh(7, (Point3(0, 0, 0), Point3(1, 0, 0), Point3(0, 1, 0)), ((0, 1, 2),))

    view._add_mesh(mesh, selected=False, labels=False, label_scale=1.0, label_text="")

    assert len(view._actors) == 2
    mapper = view._actors[1].GetMapper()
    mapper.Update()
    assert mapper.GetInput().GetNumberOfPoints() > 0
    assert mapper.GetInput().GetBounds()[5] == pytest.approx(1.0)


def test_normal_arrow_limit_is_distributed_across_faces():
    faces = [
        Mesh(entity_id, (Point3(0, 0, 0), Point3(1, 0, 0), Point3(0, 1, 0)),
             ((0, 1, 2),) * triangle_count, curved=True)
        for entity_id, triangle_count in ((1, 40), (2, 20), (3, 10))
    ]

    quotas = VtkView._allocate_normal_arrow_quotas(faces, 5)

    assert sum(quotas.values()) == 5
    assert all(0 <= quota <= 24 for quota in quotas.values())
    assert sorted(quotas.values()) == [1, 2, 2]
    assert VtkView._allocate_normal_arrow_quotas(faces, 0) == {1: 0, 2: 0, 3: 0}
