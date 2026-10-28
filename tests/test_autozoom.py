from step_explorer.visualization.vtk_view import VtkView
from step_explorer.visualization.vtk_view import vtk
from step_explorer.geometry.builder import Point3
from step_explorer.ui.main_window import MainWindow
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent


def test_disabling_autozoom_keeps_the_current_camera_framing():
    view = VtkView.__new__(VtkView)
    view.renderer = None
    view._initialized = False
    view.autozoom = True
    view._fit_camera_on_next_render = False

    view.set_autozoom(False)

    assert view.autozoom is False
    assert view._fit_camera_on_next_render is False


def test_enabling_autozoom_requests_a_camera_fit():
    view = VtkView.__new__(VtkView)
    view.renderer = None
    view._initialized = False
    view.autozoom = False
    view._fit_camera_on_next_render = False

    view.set_autozoom(True)

    assert view.autozoom is True
    assert view._fit_camera_on_next_render is True


@pytest.mark.parametrize("key,method", [(Qt.Key.Key_R, "reset_camera"), (Qt.Key.Key_F, "fit_camera")])
def test_camera_hotkeys_are_handled_before_vtk(key, method):
    viewport = Mock()
    window = SimpleNamespace(viewport=viewport)
    event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
    assert MainWindow.eventFilter(window, viewport, event)
    getattr(viewport, method).assert_called_once_with()


@pytest.mark.parametrize("parallel", [False, True])
def test_reset_camera_restores_home_direction_and_frames_geometry(parallel):
    view = VtkView.__new__(VtkView)
    view.renderer = vtk.vtkRenderer()
    view._initialized = False
    cube = vtk.vtkCubeSource()
    cube.SetCenter(10, 20, 30)
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(cube.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    view.renderer.AddActor(actor)
    camera = view.renderer.GetActiveCamera()
    camera.SetPosition(100, 0, 0)
    camera.SetViewUp(0, 0, 1)
    camera.SetParallelProjection(parallel)
    camera.Zoom(7)
    view.reset_camera()
    assert camera.GetDirectionOfProjection() == pytest.approx((-1 / 3**0.5,) * 3)
    assert camera.GetFocalPoint() == pytest.approx((0, 0, 0))
    assert camera.GetViewAngle() == pytest.approx(30)
    expected_position = camera.GetPosition()
    expected_scale = camera.GetParallelScale()
    camera.Zoom(12)
    camera.Dolly(3)
    camera.Azimuth(42)
    camera.SetFocalPoint(20, 10, -5)
    view.reset_camera()
    assert camera.GetPosition() == pytest.approx(expected_position)
    assert camera.GetParallelScale() == pytest.approx(expected_scale)


def test_origin_axes_stay_small_relative_to_model():
    view = VtkView.__new__(VtkView)
    view._xyz_origin_axes = vtk.vtkAxesActor()
    view._update_xyz_origin_axes([Point3(-50, 0, 0), Point3(50, 0, 0)])
    assert view._xyz_origin_axes.GetTotalLength() == pytest.approx((8, 8, 8))


def test_grid_exists_before_loading_a_model_and_scale_updates_it(view):
    grid = view._xy_grid_actor
    assert view.show_xy_grid
    assert grid.GetVisibility()
    assert grid.GetMapper().GetInput().GetNumberOfLines() > 0
    assert grid.GetBounds() == pytest.approx((-5, 5, -5, 5, 0, 0))

    view.set_xy_grid_scale(2.0)
    assert view.xy_grid_world_size == pytest.approx((20, 20))
    assert grid.GetBounds() == pytest.approx((-10, 10, -10, 10, 0, 0))


def test_camera_clipping_includes_grid_without_using_it_to_frame_model(view):
    cube = vtk.vtkCubeSource()
    cube.SetCenter(0, 0, 1)
    cube.SetXLength(0.2)
    cube.SetYLength(0.2)
    cube.SetZLength(0.2)
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(cube.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    view.renderer.AddActor(actor)
    camera = view.renderer.GetActiveCamera()
    camera.SetPosition(0, 0, 3)
    camera.SetFocalPoint(0, 0, 0)

    view.renderer.ResetCameraClippingRange()
    model_only_far = camera.GetClippingRange()[1]
    view._reset_clipping_range()

    assert model_only_far < 3
    assert camera.GetClippingRange()[1] > 3
    assert view.renderer.ComputeVisiblePropBounds() == pytest.approx(actor.GetBounds())
