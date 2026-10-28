from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from step_explorer.geometry.builder import ControlNet, GeometryBuilder, Mesh, Point3
from step_explorer.step.parser import StepDocument
from step_explorer.ui.main_window import MainWindow
from step_explorer.visualization.vtk_view import _orbit_position


def test_turntable_orbit_preserves_target_radius_and_world_up():
    focal = (4.0, -2.0, 1.0)
    start = (14.0, -2.0, 1.0)
    moved = _orbit_position(start, focal, 100, 50, 500)
    offset = tuple(moved[i] - focal[i] for i in range(3))
    assert sum(value * value for value in offset) ** 0.5 == pytest.approx(10.0)
    assert moved[2] > start[2]
    assert _orbit_position(moved, focal, -100, -50, 500) == pytest.approx(start)
    assert _orbit_position(start, focal, 100, 50, 500, True, True) == pytest.approx(
        _orbit_position(start, focal, -100, -50, 500)
    )


def test_turntable_axis_inversion_is_independent():
    start = (10.0, 0.0, 0.0)
    focal = (0.0, 0.0, 0.0)
    assert _orbit_position(start, focal, 40, 30, 500, invert_horizontal=True) == pytest.approx(
        _orbit_position(start, focal, -40, 30, 500)
    )
    assert _orbit_position(start, focal, 40, 30, 500, invert_vertical=True) == pytest.approx(
        _orbit_position(start, focal, 40, -30, 500)
    )


def test_turntable_orbit_stops_at_poles_without_flipping():
    position = _orbit_position((10.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0, 10000, 500)
    assert position[2] > 9.9
    assert position[0] > 0.0


def test_left_drag_reaches_turntable_orbit_instead_of_vtk_trackball(view):
    view.set_navigation_preset("CAD")
    camera = view.renderer.GetActiveCamera()
    camera.SetFocalPoint(0, 0, 0)
    camera.SetPosition(10, 10, 10)
    camera.SetViewUp(0, 0, 1)
    view.widget.GetRenderWindow().Render = Mock()
    view._orbit_camera = Mock(wraps=view._orbit_camera)
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(100, 100), QPointF(100, 100),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    move = QMouseEvent(
        QEvent.Type.MouseMove, QPointF(20, 100), QPointF(20, 100),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(20, 100), QPointF(20, 100),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )

    QApplication.sendEvent(view.widget, press)
    QApplication.sendEvent(view.widget, move)
    QApplication.sendEvent(view.widget, release)

    view._orbit_camera.assert_called_once_with(-80.0, -0.0)
    assert view._orbit_button == Qt.MouseButton.NoButton
    assert camera.GetPosition()[2] == pytest.approx(10.0)
    assert camera.GetPosition()[0] != pytest.approx(10.0)


def test_horizontal_turntable_drag_does_not_tilt_camera(view):
    camera = view.renderer.GetActiveCamera()
    camera.SetFocalPoint(0, 0, 0)
    camera.SetPosition(10, 10, 10)
    camera.SetViewUp(0, 0, 1)
    view.widget.GetRenderWindow().Render = Mock()

    view._orbit_camera(-80, 0)

    assert camera.GetPosition()[2] == pytest.approx(10.0)
    direction = camera.GetDirectionOfProjection()
    up = camera.GetViewUp()
    right_z = direction[0] * up[1] - direction[1] * up[0]
    assert right_z == pytest.approx(0.0, abs=1e-9)


def test_analysis_uses_complete_model_when_face_has_forward_references():
    source = Path(__file__).resolve().parents[1] / "tests/corpus/analytic_curved/cylindercut1.STEP"
    document = StepDocument.from_file(source)
    builder = GeometryBuilder(document)
    face = next(entity for entity in document.entities if entity.type_name == "ADVANCED_FACE")
    assert not any(mesh.entity_id == face.entity_id for mesh in builder.build(face.order).faces)

    window = SimpleNamespace(
        document=document,
        current_id=face.entity_id,
        geometry_builder=builder,
        viewport=SimpleNamespace(set_face_analysis=Mock()),
        _show_snapshot=Mock(),
        _update_face_analysis_actions=Mock(),
    )
    MainWindow._analyze_current_face(window)

    assert any(mesh.entity_id == face.entity_id for mesh in window._analysis_snapshot.faces)
    window._show_snapshot.assert_called_once_with(window._analysis_snapshot, face.entity_id)


def test_face_analysis_opacity_and_normal_length_affect_actors(view):
    mesh = Mesh(7, (Point3(0, 0, 0), Point3(1, 0, 0), Point3(0, 1, 0)), ((0, 1, 2),))
    view._analysis_face_id = 7
    view._analysis_face_opacity = 0.25
    view._analysis_show_normals = True
    view._analysis_normal_scale = 2.0
    view._normal_length = 1.0
    view._normal_arrow_quotas = {7: 0}

    view._add_mesh(mesh, selected=False, labels=False, label_scale=1.0, label_text="", highlighted=True)

    assert len(view._actors) == 2
    assert view._actors[0].GetProperty().GetOpacity() == pytest.approx(0.25)
    assert view._actors[0].GetProperty().GetEdgeVisibility()
    mapper = view._actors[1].GetMapper()
    mapper.Update()
    assert mapper.GetInput().GetBounds()[5] == pytest.approx(2.0)

    view._analysis_context_opacity = 0.0
    view._add_polyline(8, mesh.points, False, "Geometry", False, 1.0, "", dimmed=True)
    assert view._actors[-1].GetProperty().GetOpacity() == 0.0


def test_normal_length_updates_existing_glyph_without_rebuilding_scene(view):
    mesh = Mesh(7, (Point3(0, 0, 0), Point3(1, 0, 0), Point3(0, 1, 0)), ((0, 1, 2),))
    view._analysis_face_id = 7
    view._analysis_show_normals = True
    view._normal_length = 1.0
    view._normal_arrow_quotas = {7: 0}
    view._add_mesh(mesh, selected=False, labels=False, label_scale=1.0, label_text="", highlighted=True)
    glyph = view._analysis_normal_glyph
    view._initialized = True
    view._last_snapshot = SimpleNamespace(faces=[mesh])
    view._render_snapshot = Mock()
    view.widget.GetRenderWindow().Render = Mock()

    view.set_face_analysis_display(show_normals=True, normal_scale=2.0)

    assert glyph.GetScaleFactor() == pytest.approx(2.0)
    view._render_snapshot.assert_not_called()
    view.widget.GetRenderWindow().Render.assert_called_once()


def test_normal_length_control_is_only_shown_with_face_normals(qapp):
    window = MainWindow()
    assert window.face_normal_scale_row.isHidden()
    assert not window.face_normal_scale_slider.isEnabled()

    window.face_normals_checkbox.setChecked(True)
    assert not window.face_normal_scale_row.isHidden()
    assert window.face_normal_scale_slider.isEnabled()

    window.face_normals_checkbox.setChecked(False)
    assert window.face_normal_scale_row.isHidden()
    window.close()


def test_fit_face_uses_control_net_when_preview_mesh_is_missing(view):
    net = ControlNet(42, (
        (Point3(2, 3, 4), Point3(3, 3, 4)),
        (Point3(2, 4, 4), Point3(3, 4, 4)),
    ))
    view._analysis_face_id = 7
    view._analysis_surface_id = 42
    view._last_snapshot = SimpleNamespace(faces=[], control_nets=[net], polylines=[], vertices={})
    view._initialized = True
    view._render_snapshot = Mock()

    view.fit_analysis_face()

    assert view.renderer.GetActiveCamera().GetFocalPoint() == pytest.approx((2.5, 3.5, 4.0))
    view._render_snapshot.assert_called_once()
