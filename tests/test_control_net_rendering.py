import pytest
from types import SimpleNamespace

from step_explorer.geometry import ControlNet, Point3
from step_explorer.visualization.vtk_view import vtk


@pytest.mark.skipif(vtk is None, reason="VTK is not installed")
def test_control_net_renders_as_one_actor_with_shared_grid_points(view):
    control_net = ControlNet(
        42,
        (
            (Point3(0.0, 0.0, 0.0), Point3(0.0, 1.0, 0.0), Point3(0.0, 2.0, 0.0)),
            (Point3(1.0, 0.0, 0.0), Point3(1.0, 1.0, 0.0), Point3(1.0, 2.0, 0.0)),
        ),
    )
    view._add_control_net(control_net, selected=False, labels=False, label_scale=1.0, label_text="#42")

    assert len(view._actors) == 1
    data = view._actors[0].GetMapper().GetInput()
    assert data.GetNumberOfPoints() == 6
    assert data.GetNumberOfVerts() == 6
    assert data.GetNumberOfLines() == 5


@pytest.mark.skipif(vtk is None, reason="VTK is not installed")
def test_control_net_density_changes_only_the_rendered_grid_resolution(view):
    control_net = ControlNet(
        42,
        tuple(
            tuple(Point3(float(row), float(column), 0.0) for column in range(4))
            for row in range(4)
        ),
    )
    view._last_control_net_densities[42] = 0

    view._add_control_net(control_net.at_density(view._last_control_net_densities[42]), False, False, 1.0, "#42")

    data = view._actors[0].GetMapper().GetInput()
    assert data.GetNumberOfPoints() == 4
    assert data.GetNumberOfLines() == 4
    assert control_net.row_count == 4
    assert control_net.column_count == 4


@pytest.mark.parametrize('density, incremental', [(100, True), (50, False), (0, False)])
def test_new_surface_density_preserves_incremental_rendering_only_at_default(view, density, incremental):
    view._fit_camera_on_next_render = False
    view.normal_display = 'Hidden'
    view._analysis_face_id = None
    view._rendered_style_signature = view._render_style_signature(False, 'index', {})
    # Match playback: discovering a surface updates the existing density map.
    view._last_control_net_densities[42] = density
    snapshot = SimpleNamespace(render_updates={})
    assert view._can_render_incrementally(snapshot, False, 'index', None) is incremental


def test_restoring_default_density_still_invalidates_previous_custom_density(view):
    view._fit_camera_on_next_render = False
    view.normal_display = 'Hidden'
    view._analysis_face_id = None
    view._last_control_net_densities[42] = 50
    view._rendered_style_signature = view._render_style_signature(False, 'index', None)
    view._last_control_net_densities[42] = 100
    assert not view._can_render_incrementally(SimpleNamespace(render_updates={}), False, 'index', None)
