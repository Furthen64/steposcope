from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from step_explorer.geometry.builder import Mesh, Point3


def test_projection_modes_switch_without_changing_focal_point(view):
    camera = view.renderer.GetActiveCamera()
    camera.SetFocalPoint(2, 3, 4)
    camera.SetPosition(2, 3, 14)
    view.set_projection_mode("Isometric")
    assert camera.GetParallelProjection()
    assert camera.GetFocalPoint() == pytest.approx((2, 3, 4))
    assert camera.GetPosition()[0] - 2 == pytest.approx(camera.GetPosition()[1] - 3)
    view.set_projection_mode("Perspective")
    assert not camera.GetParallelProjection()
    assert camera.GetFocalPoint() == pytest.approx((2, 3, 4))


def test_focus_face_moves_orbit_target_without_changing_camera_offset(view):
    mesh = Mesh(7, (Point3(2, 4, 6), Point3(4, 4, 6), Point3(2, 8, 6)), ((0, 1, 2),))
    view._last_snapshot = SimpleNamespace(faces=[mesh])
    camera = view.renderer.GetActiveCamera()
    camera.SetFocalPoint(0, 0, 0)
    camera.SetPosition(10, 10, 10)
    camera.SetViewUp(0, 0, 1)
    before_offset = camera.GetPosition()
    assert view.focus_face(7)
    assert camera.GetFocalPoint() == pytest.approx((3, 6, 6))
    assert tuple(camera.GetPosition()[i] - camera.GetFocalPoint()[i] for i in range(3)) == pytest.approx(before_offset)
    assert not view.focus_face(99)


def test_left_click_picks_face_but_drag_does_not(view):
    picked = []
    backgrounds = []
    view.face_picked.connect(picked.append)
    view.background_clicked.connect(lambda: backgrounds.append(True))
    view._pick_face = Mock(return_value=7)

    def mouse_event(event_type, point, button, buttons):
        return QMouseEvent(event_type, point, point, button, buttons, Qt.KeyboardModifier.NoModifier)

    QApplication.sendEvent(view.widget, mouse_event(QEvent.Type.MouseButtonPress, QPointF(100, 100), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    QApplication.sendEvent(view.widget, mouse_event(QEvent.Type.MouseButtonRelease, QPointF(100, 100), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    assert picked == [7]

    QApplication.sendEvent(view.widget, mouse_event(QEvent.Type.MouseButtonPress, QPointF(100, 100), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    QApplication.sendEvent(view.widget, mouse_event(QEvent.Type.MouseMove, QPointF(120, 100), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
    QApplication.sendEvent(view.widget, mouse_event(QEvent.Type.MouseButtonRelease, QPointF(120, 100), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    assert picked == [7]
    view._pick_face.assert_called_once()
    assert backgrounds == []

    view._pick_face.return_value = None
    QApplication.sendEvent(view.widget, mouse_event(QEvent.Type.MouseButtonPress, QPointF(100, 100), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    QApplication.sendEvent(view.widget, mouse_event(QEvent.Type.MouseButtonRelease, QPointF(100, 100), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    assert backgrounds == [True]
    assert picked == [7]


def test_double_click_picks_face_once_and_ignores_background(view):
    picked = []
    double_clicked = []
    backgrounds = []
    view.face_picked.connect(picked.append)
    view.face_double_clicked.connect(double_clicked.append)
    view.background_clicked.connect(lambda: backgrounds.append(True))
    view._pick_face = Mock(return_value=7)

    point = QPointF(100, 100)

    def mouse_event(event_type, buttons):
        return QMouseEvent(event_type, point, point, Qt.MouseButton.LeftButton,
                           buttons, Qt.KeyboardModifier.NoModifier)

    QApplication.sendEvent(view.widget, mouse_event(QEvent.Type.MouseButtonDblClick, Qt.MouseButton.LeftButton))
    QApplication.sendEvent(view.widget, mouse_event(QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton))

    assert double_clicked == [7]
    assert picked == []
    assert backgrounds == []
    view._pick_face.assert_called_once_with(point)


def test_picker_identifies_rendered_face(view):
    mesh = Mesh(7, (Point3(0, 0, 0), Point3(1, 0, 0), Point3(0, 1, 0)), ((0, 1, 2),))
    view.widget.resize(400, 400)
    view.widget.GetRenderWindow().SetSize(400, 400)
    view._add_mesh(mesh, selected=False, labels=False, label_scale=1.0, label_text="")
    camera = view.renderer.GetActiveCamera()
    camera.SetFocalPoint(0.3, 0.3, 0)
    camera.SetPosition(0.3, 0.3, 2)
    camera.SetViewUp(0, 1, 0)
    view.renderer.ResetCameraClippingRange()

    assert view.has_rendered_face(7)
    assert view._pick_face(QPointF(200, 200)) == 7
