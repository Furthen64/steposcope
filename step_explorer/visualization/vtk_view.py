from __future__ import annotations

from dataclasses import dataclass
from colorsys import hsv_to_rgb
from math import atan2, ceil, cos, dist, exp, floor, hypot, log10, pi, sin, sqrt, tan
from random import Random

from ..geometry.builder import Point3
from ..ui.colors import color_for_category

try:
    from PySide6.QtCore import QEvent, QTimer, Signal, Qt
    from PySide6.QtWidgets import QLabel, QPushButton, QToolTip, QVBoxLayout, QWidget
except ImportError:  # pragma: no cover - allows parser use without GUI extras
    QLabel = QVBoxLayout = QWidget = object  # type: ignore[misc,assignment]

try:
    from ..vtk_lite import vtk
    from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
except ImportError:  # pragma: no cover - allows a Qt-only install to open files
    QVTKRenderWindowInteractor = None
    vtk = None


@dataclass(frozen=True)
class _LabelSpec:
    entity_id: int
    anchor: tuple[float, float, float]
    color: tuple[float, float, float]
    scale: float
    text: str
    selected: bool
    priority: int
    arrival_order: int = 0
    dimmed: bool = False
    force_visible: bool = False


def _reset_isometric_camera(renderer) -> None:
    """Reset orbit, pan and both perspective/parallel zoom about the origin."""
    camera = renderer.GetActiveCamera()
    camera.SetFocalPoint(0.0, 0.0, 0.0)
    camera.SetPosition(1.0, 1.0, 1.0)
    camera.SetViewUp(0.0, 0.0, 1.0)
    camera.SetViewAngle(30.0)
    camera.SetWindowCenter(0.0, 0.0)
    camera.SetViewShear(0.0, 0.0, 1.0)
    camera.OrthogonalizeViewUp()
    bounds = renderer.ComputeVisiblePropBounds()
    if all(bounds[i] <= bounds[i + 1] for i in (0, 2, 4)):
        # Symmetric bounds preserve the origin as the target while fitting
        # geometry that is offset from it. Ignore the previous camera zoom.
        extents = [max(abs(bounds[i]), abs(bounds[i + 1]), 1e-3) for i in (0, 2, 4)]
        renderer.ResetCamera(tuple(value for extent in extents for value in (-extent, extent)))
    else:
        camera.SetPosition(10.0, 10.0, 10.0)
        camera.SetParallelScale(1.0)
    renderer.ResetCameraClippingRange()


def _orbit_position(
    position, focal, dx: float, dy: float, height: int,
    invert_horizontal: bool = False, invert_vertical: bool = False,
):
    """Orbit about a fixed target with world Z up and no camera roll."""

    offset = tuple(position[i] - focal[i] for i in range(3))
    horizontal = hypot(offset[0], offset[1])
    radius = hypot(horizontal, offset[2])
    if radius <= 1e-12:
        return tuple(position)
    sensitivity = pi / max(height, 1)
    yaw_direction = -1 if invert_horizontal else 1
    pitch_direction = -1 if invert_vertical else 1
    yaw = atan2(offset[1], offset[0]) - yaw_direction * dx * sensitivity
    pitch = atan2(offset[2], horizontal) + pitch_direction * dy * sensitivity
    pitch = max(-pi / 2 + 0.01, min(pi / 2 - 0.01, pitch))
    return (
        focal[0] + radius * cos(pitch) * cos(yaw),
        focal[1] + radius * cos(pitch) * sin(yaw),
        focal[2] + radius * sin(pitch),
    )


class VtkView(QWidget):
    """Render simple primitives; all STEP interpretation stays in GeometryBuilder."""

    orientation_warning_clicked = Signal(int, str)
    face_picked = Signal(int)
    face_double_clicked = Signal(int)
    background_clicked = Signal()

    NORMAL_DISPLAY_MODES = ("Hidden", "Vectors", "Face orientation")
    FACE_COLOR_MODES = ("all-the-same", "normals", "random")

    # Labels are deliberately bounded: each label is a VTK actor and collision
    # placement is performed in display space. A few hundred useful labels are
    # much more responsive than tens of thousands of overlapping actors.
    DEFAULT_MAX_ENTITY_LABELS = 250
    DEFAULT_CONTROL_NET_DENSITY = 100
    DEFAULT_NORMAL_ARROW_LIMIT = 48
    MAX_NORMAL_ARROW_LIMIT = 200
    LABEL_SPACING = 42.0
    LABEL_GRID_SIZE = 48.0

    def __init__(
        self,
        parent=None,
        navigation_preset: str = "Blender",
        orbit_method: str = "Turntable",
        invert_mouse_horizontal: bool = False,
        invert_mouse_vertical: bool = False,
        autozoom: bool = True,
        max_entity_labels: int = DEFAULT_MAX_ENTITY_LABELS,
        show_xyz_origin: bool = True,
        show_xy_grid: bool = True,
        xy_grid_color: str = "#4d5966",
        xy_grid_scale: float = 1.0,
        show_mesh_edges: bool = False,
        mesh_edge_opacity: float = 0.6,
        background_color: str = "#46596d",
    ):
        super().__init__(parent)
        self.navigation_preset = navigation_preset
        self.orbit_method = orbit_method
        self.invert_mouse_horizontal = bool(invert_mouse_horizontal)
        self.invert_mouse_vertical = bool(invert_mouse_vertical)
        self.autozoom = bool(autozoom)
        self.normal_display = "Hidden"
        self.normal_arrow_limit = self.DEFAULT_NORMAL_ARROW_LIMIT
        self._normal_arrow_quotas: dict[int, int] = {}
        self.face_color_mode = "normals"
        self.max_entity_labels = self._clamp_label_limit(max_entity_labels)
        self.show_xyz_origin = bool(show_xyz_origin)
        self.show_xy_grid = bool(show_xy_grid)
        self.show_mesh_edges = bool(show_mesh_edges)
        self.mesh_edge_opacity = max(0.0, min(1.0, float(mesh_edge_opacity)))
        self.xy_grid_color = self._parse_hex_color(xy_grid_color) or (0.30, 0.35, 0.40)
        self.background_color = self._parse_hex_color(background_color) or (0.27, 0.35, 0.43)
        self.xy_grid_scale = self._clamp_xy_grid_scale(xy_grid_scale)
        self.xy_grid_world_size = (10.0 * self.xy_grid_scale, 10.0 * self.xy_grid_scale)
        self._xyz_origin_axes = None
        self._xy_grid_actor = None
        self._xy_grid_mapper = None
        self._actors = []
        self._actors_by_entity: dict[int, list] = {}
        self._face_actors: dict[int, object] = {}
        self._selection_flash_face_id: int | None = None
        self._selection_flash_timer = QTimer(self)
        self._selection_flash_timer.setSingleShot(True)
        self._selection_flash_timer.timeout.connect(self._finish_selection_flash)
        self._active_entity_id: int | None = None
        self._rendered_items: dict[int, tuple[str, object]] = {}
        self._rendered_selected_id: int | None = None
        self._rendered_style_signature = None
        self._rendered_bounds: tuple[float, float, float, float, float, float] | None = None
        self._mesh_edge_actors = []
        self._initialized = False
        self._fit_camera_on_next_render = True
        self._last_snapshot = None
        self._last_selected_id = None
        self._last_labels = False
        self._last_label_mode = "index"
        self._last_label_texts: dict[int, str] = {}
        self._last_label_orders: dict[int, int] = {}
        self._last_label_time = 0
        self._last_control_net_densities: dict[int, int] = {}
        self._last_show_points = True
        self._last_fast_preview = False
        self._analysis_face_id: int | None = None
        self._analysis_surface_id: int | None = None
        self._analysis_boundary_edge_ids: frozenset[int] = frozenset()
        self._analysis_boundary_vertex_ids: frozenset[int] = frozenset()
        self._analysis_face_opacity = 0.86
        self._analysis_context_opacity = 0.07
        self._analysis_triangle_edges = True
        self._analysis_show_normals = False
        self._analysis_normal_scale = 1.0
        self._analysis_normal_glyph = None
        self._orbit_button = Qt.MouseButton.NoButton
        self._navigation_action = None
        self._last_orbit_pos = None
        self._press_pos = None
        self._active_label_orders: dict[int, int] = {}
        self._active_label_time = 0
        self._model_check_tooltip = ""
        self._model_check_actor = None
        self._issue_edge_id: int | None = None
        self._issue_edge_actor = None
        self._issue_face_properties = []
        self._issue_overview_properties = []
        self._issue_overview_edge_actors = []
        self._issue_blink_timer = QTimer(self)
        self._issue_blink_timer.setSingleShot(True)
        self._issue_blink_timer.timeout.connect(self._advance_issue_blink)
        self._issue_blink_steps: list[tuple[str, bool]] = []
        self._issue_blink_index = 0
        if vtk is None or QVTKRenderWindowInteractor is None:
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("VTK is not installed.\nParser and traversal remain available."))
            self.renderer = None
            return
        self.widget = QVTKRenderWindowInteractor(self)
        self.widget.installEventFilter(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.widget)
        self.interactor_style = vtk.vtkInteractorStyleTrackballCamera()
        self.widget.GetRenderWindow().GetInteractor().SetInteractorStyle(self.interactor_style)
        self.renderer = vtk.vtkRenderer()
        self._warning_balloons = []
        self._warning_balloons_by_id = {}
        self.renderer.AddObserver("EndEvent", self._position_warning_balloons)
        self.renderer.SetBackground(*self.background_color)
        self.widget.GetRenderWindow().AddRenderer(self.renderer)
        self._model_check_actor = vtk.vtkTextActor()
        self._model_check_actor.SetTextScaleModeToNone()
        self._model_check_actor.GetPositionCoordinate().SetCoordinateSystemToNormalizedViewport()
        self._model_check_actor.SetPosition(0.018, 0.025)
        text_style = self._model_check_actor.GetTextProperty()
        text_style.SetColor(0.95, 0.96, 0.98)
        text_style.SetFontSize(13)
        text_style.SetBold(True)
        text_style.SetShadow(True)
        text_style.SetVerticalJustificationToBottom()
        self._model_check_actor.SetVisibility(False)
        self.renderer.AddViewProp(self._model_check_actor)
        self._xyz_origin_axes = vtk.vtkAxesActor()
        self._xyz_origin_axes.SetTotalLength(1.0, 1.0, 1.0)
        self._xyz_origin_axes.GetZAxisShaftProperty().SetColor(0.0, 1.0, 1.0)
        self._xyz_origin_axes.GetZAxisTipProperty().SetColor(0.0, 1.0, 1.0)
        self._xyz_origin_axes.GetZAxisCaptionActor2D().GetCaptionTextProperty().SetColor(0.0, 1.0, 1.0)
        self._xyz_origin_axes.SetNormalizedLabelPosition(0.9, 0.9, 0.9)
        for caption_getter in (
            self._xyz_origin_axes.GetXAxisCaptionActor2D,
            self._xyz_origin_axes.GetYAxisCaptionActor2D,
            self._xyz_origin_axes.GetZAxisCaptionActor2D,
        ):
            caption = caption_getter()
            caption.GetTextActor().SetTextScaleModeToNone()
            text = caption.GetCaptionTextProperty()
            text.SetFontSize(14)
            text.SetBold(False)
        self._xyz_origin_axes.SetPickable(False)
        # Keep the coordinate marker from changing camera framing.
        self._xyz_origin_axes.SetUseBounds(False)
        self._xyz_origin_axes.SetVisibility(self.show_xyz_origin)
        self.renderer.AddActor(self._xyz_origin_axes)
        grid_data = vtk.vtkPolyData()
        grid_mapper = vtk.vtkPolyDataMapper()
        grid_mapper.SetInputData(grid_data)
        self._xy_grid_mapper = grid_mapper
        self._xy_grid_actor = vtk.vtkActor()
        self._xy_grid_actor.SetMapper(grid_mapper)
        self._xy_grid_actor.GetProperty().SetColor(*self.xy_grid_color)
        self._xy_grid_actor.GetProperty().SetOpacity(0.55)
        self._xy_grid_actor.GetProperty().SetLineWidth(1.0)
        self._xy_grid_actor.GetProperty().LightingOff()
        self._xy_grid_actor.SetPickable(False)
        self._xy_grid_actor.SetUseBounds(False)
        self._xy_grid_actor.SetVisibility(self.show_xy_grid)
        self.renderer.AddActor(self._xy_grid_actor)
        self._update_xy_grid(())
        self._reset_clipping_range()

    def set_invert_mouse_horizontal(self, enabled: bool) -> None:
        self.invert_mouse_horizontal = bool(enabled)

    def set_invert_mouse_vertical(self, enabled: bool) -> None:
        self.invert_mouse_vertical = bool(enabled)

    def set_navigation_preset(self, preset: str) -> None:
        if preset in {"Blender", "CAD", "Plasticity", "FreeCAD"}:
            self.navigation_preset = preset

    def set_orbit_method(self, method: str) -> None:
        if method in {"Turntable", "Trackball"}:
            self.orbit_method = method

    def _mouse_navigation_action(self, buttons, modifiers):
        left = bool(buttons & Qt.MouseButton.LeftButton)
        middle = bool(buttons & Qt.MouseButton.MiddleButton)
        right = bool(buttons & Qt.MouseButton.RightButton)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        control = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        if self.navigation_preset == "Blender":
            if middle:
                return "pan" if shift else "orbit"
        elif self.navigation_preset == "CAD":
            if middle:
                return "orbit" if left else "pan"
            if left:
                return "orbit"
        elif self.navigation_preset == "Plasticity":
            if middle:
                if control:
                    return "zoom"
                return "orbit"
            if right:
                return "pan"
        elif self.navigation_preset == "FreeCAD":
            if middle:
                return "orbit" if left else "pan"
        return None

    def eventFilter(self, watched, event):
        if watched is getattr(self, "widget", None) and self.renderer is not None:
            event_type = event.type()
            if event_type == QEvent.Type.ToolTip and self._model_check_tooltip:
                size = [0.0, 0.0]
                self._model_check_actor.GetSize(self.renderer, size)
                pos = event.pos()
                if pos.x() <= size[0] + 18 and pos.y() >= self.widget.height() - size[1] - 28:
                    QToolTip.showText(event.globalPos(), self._model_check_tooltip, self.widget)
                    return True
            if event_type == QEvent.Type.MouseButtonPress:
                buttons = event.buttons() | event.button()
                action = self._mouse_navigation_action(buttons, event.modifiers())
                # Consume unbound drags too. Passing them to VTK's trackball
                # style reintroduces its independent yaw/pitch camera motion.
                self._navigation_action = action or "ignore"
                self._orbit_button = event.button()
                self._last_orbit_pos = event.position()
                self._press_pos = event.position() if event.button() == Qt.MouseButton.LeftButton else None
                return True
            if event_type == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                self._navigation_action = "ignore"
                self._press_pos = None
                face_id = self._pick_face(event.position())
                if face_id is not None:
                    self.face_double_clicked.emit(face_id)
                return True
            if event_type == QEvent.Type.MouseMove and self._navigation_action is not None:
                if self._navigation_action == "ignore":
                    if not event.buttons():
                        self._navigation_action = None
                        self._last_orbit_pos = None
                    return True
                if self._mouse_navigation_action(event.buttons(), event.modifiers()) is not None:
                    position = event.position()
                    previous_position = self._last_orbit_pos
                    delta = position - self._last_orbit_pos
                    self._last_orbit_pos = position
                    if self._navigation_action == "orbit":
                        if self.orbit_method == "Trackball":
                            self._trackball_orbit_camera(previous_position, position)
                        else:
                            self._orbit_camera(delta.x(), -delta.y())
                    elif self._navigation_action == "zoom":
                        self._zoom_camera(delta.y())
                    else:
                        self._pan_camera(delta.x(), delta.y())
                else:
                    self._navigation_action = None
                    self._orbit_button = Qt.MouseButton.NoButton
                    self._last_orbit_pos = None
                return True
            if event_type == QEvent.Type.MouseButtonRelease and self._navigation_action is not None:
                if (event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None
                        and (event.position() - self._press_pos).manhattanLength() <= 4):
                    face_id = self._pick_face(event.position())
                    if face_id is not None:
                        self.face_picked.emit(face_id)
                    else:
                        self.background_clicked.emit()
                self._press_pos = None
                remaining = event.buttons() & ~event.button()
                action = self._mouse_navigation_action(remaining, event.modifiers())
                if action is None:
                    self._navigation_action = None
                    self._orbit_button = Qt.MouseButton.NoButton
                    self._last_orbit_pos = None
                elif action != self._navigation_action:
                    self._navigation_action = action
                    self._last_orbit_pos = event.position()
                return True
        return super().eventFilter(watched, event)

    def _pick_face(self, position) -> int | None:
        if not self._face_actors or self.renderer is None:
            return None
        picker = vtk.vtkCellPicker()
        picker.PickFromListOn()
        picker.SetTolerance(0.002)
        for actor in self._face_actors.values():
            picker.AddPickList(actor)
        if not picker.Pick(position.x(), self.widget.height() - position.y() - 1, 0, self.renderer):
            return None
        picked = picker.GetActor()
        return next((entity_id for entity_id, actor in self._face_actors.items() if actor == picked), None)

    def has_rendered_face(self, entity_id: int | None) -> bool:
        return entity_id in self._face_actors

    def focus_face(self, entity_id: int) -> bool:
        """Center the orbit target on a rendered face without changing camera distance."""
        if self.renderer is None or self._last_snapshot is None:
            return False
        face = next((item for item in self._last_snapshot.faces if item.entity_id == entity_id), None)
        if face is None or not face.points:
            return False
        center = tuple(
            (min(getattr(point, axis) for point in face.points)
             + max(getattr(point, axis) for point in face.points)) / 2
            for axis in ("x", "y", "z")
        )
        camera = self.renderer.GetActiveCamera()
        old_focal = camera.GetFocalPoint()
        old_position = camera.GetPosition()
        camera.SetFocalPoint(*center)
        camera.SetPosition(*(old_position[i] + center[i] - old_focal[i] for i in range(3)))
        self._reset_clipping_range()
        if self._initialized:
            self.widget.GetRenderWindow().Render()
        return True

    def set_projection_mode(self, mode: str) -> None:
        if self.renderer is None or mode not in {"Isometric", "Perspective"}:
            return
        camera = self.renderer.GetActiveCamera()
        perspective_scale = max(camera.GetDistance() * tan(camera.GetViewAngle() * pi / 360.0), 1e-6)
        if mode == "Isometric":
            if not camera.GetParallelProjection():
                camera.SetParallelScale(perspective_scale)
            focal = camera.GetFocalPoint()
            distance = max(camera.GetDistance(), 1e-6) / sqrt(3.0)
            camera.SetPosition(*(value + distance for value in focal))
            camera.SetViewUp(0.0, 0.0, 1.0)
            camera.OrthogonalizeViewUp()
            camera.ParallelProjectionOn()
        else:
            if camera.GetParallelProjection():
                distance = camera.GetParallelScale() / max(tan(camera.GetViewAngle() * pi / 360.0), 1e-6)
                focal = camera.GetFocalPoint()
                offset = tuple(camera.GetPosition()[i] - focal[i] for i in range(3))
                length = max(sqrt(sum(value * value for value in offset)), 1e-6)
                camera.SetPosition(*(focal[i] + distance * offset[i] / length for i in range(3)))
            camera.ParallelProjectionOff()
        self._reset_clipping_range()
        if self._initialized:
            self.widget.GetRenderWindow().Render()

    def set_model_check_text(self, summary: str, tooltip: str = "") -> None:
        self._model_check_tooltip = tooltip if summary else ""
        if self._model_check_actor is not None:
            self._model_check_actor.SetInput(summary)
            self._model_check_actor.SetVisibility(bool(summary))

    def _pan_camera(self, dx: float, dy: float) -> None:
        # Blender and Plasticity map a drag to moving the scene with the
        # pointer. The CAD and FreeCAD presets retain the camera-pan direction.
        if self.navigation_preset in {"Blender", "Plasticity"}:
            dx, dy = -dx, -dy
        camera = self.renderer.GetActiveCamera()
        position = camera.GetPosition()
        focal = camera.GetFocalPoint()
        view_up = camera.GetViewUp()
        direction = camera.GetDirectionOfProjection()
        right = (
            direction[1] * view_up[2] - direction[2] * view_up[1],
            direction[2] * view_up[0] - direction[0] * view_up[2],
            direction[0] * view_up[1] - direction[1] * view_up[0],
        )
        height = max(self.widget.height(), 1)
        if camera.GetParallelProjection():
            world_per_pixel = 2.0 * camera.GetParallelScale() / height
        else:
            world_per_pixel = 2.0 * camera.GetDistance() * tan(camera.GetViewAngle() * pi / 360.0) / height
        offset = tuple(world_per_pixel * (dx * right[i] - dy * view_up[i]) for i in range(3))
        camera.SetPosition(*(position[i] + offset[i] for i in range(3)))
        camera.SetFocalPoint(*(focal[i] + offset[i] for i in range(3)))
        self._reset_clipping_range()
        self.widget.GetRenderWindow().Render()

    def _zoom_camera(self, dy: float) -> None:
        if not dy:
            return
        camera = self.renderer.GetActiveCamera()
        factor = exp(-dy * 0.01)
        if camera.GetParallelProjection():
            camera.SetParallelScale(max(1e-6, camera.GetParallelScale() / factor))
        else:
            camera.Dolly(factor)
        self._reset_clipping_range()
        self.widget.GetRenderWindow().Render()

    def _trackball_orbit_camera(self, previous, current) -> None:
        width = max(self.widget.width(), 1)
        height = max(self.widget.height(), 1)
        scale = max(min(width, height) * 0.5, 1.0)

        def project(point):
            x = (point.x() - width * 0.5) / scale
            y = (height * 0.5 - point.y()) / scale
            if self.invert_mouse_horizontal:
                x = -x
            if self.invert_mouse_vertical:
                y = -y
            distance_squared = x * x + y * y
            z = sqrt(max(0.0, 1.0 - distance_squared)) if distance_squared <= 0.5 else 0.5 / sqrt(distance_squared)
            length = sqrt(x * x + y * y + z * z) or 1.0
            return x / length, y / length, z / length

        start = project(previous)
        end = project(current)
        axis_camera = (
            start[1] * end[2] - start[2] * end[1],
            start[2] * end[0] - start[0] * end[2],
            start[0] * end[1] - start[1] * end[0],
        )
        dot = max(-1.0, min(1.0, sum(start[i] * end[i] for i in range(3))))
        axis_length = sqrt(sum(value * value for value in axis_camera))
        if axis_length <= 1e-12:
            return
        angle = atan2(axis_length, dot)
        axis_camera = tuple(value / axis_length for value in axis_camera)

        camera = self.renderer.GetActiveCamera()
        direction = camera.GetDirectionOfProjection()
        view_up = camera.GetViewUp()
        right = (
            direction[1] * view_up[2] - direction[2] * view_up[1],
            direction[2] * view_up[0] - direction[0] * view_up[2],
            direction[0] * view_up[1] - direction[1] * view_up[0],
        )
        toward_camera = tuple(-value for value in direction)
        axis = tuple(
            axis_camera[0] * right[i] + axis_camera[1] * view_up[i] + axis_camera[2] * toward_camera[i]
            for i in range(3)
        )
        axis_length = sqrt(sum(value * value for value in axis))
        if axis_length <= 1e-12:
            return
        axis = tuple(value / axis_length for value in axis)

        def rotate(vector):
            cross = (
                axis[1] * vector[2] - axis[2] * vector[1],
                axis[2] * vector[0] - axis[0] * vector[2],
                axis[0] * vector[1] - axis[1] * vector[0],
            )
            projection = sum(axis[i] * vector[i] for i in range(3))
            c, s = cos(angle), sin(angle)
            return tuple(vector[i] * c + cross[i] * s + axis[i] * projection * (1.0 - c) for i in range(3))

        focal = camera.GetFocalPoint()
        offset = tuple(camera.GetPosition()[i] - focal[i] for i in range(3))
        moved_offset = rotate(offset)
        camera.SetPosition(*(focal[i] + moved_offset[i] for i in range(3)))
        camera.SetViewUp(*rotate(view_up))
        camera.OrthogonalizeViewUp()
        self._reset_clipping_range()
        self.widget.GetRenderWindow().Render()

    def _orbit_camera(self, dx: float, dy: float) -> None:
        if not (dx or dy):
            return
        camera = self.renderer.GetActiveCamera()
        camera.SetPosition(*_orbit_position(
            camera.GetPosition(), camera.GetFocalPoint(),
            dx, dy, self.widget.height(),
            self.invert_mouse_horizontal, self.invert_mouse_vertical,
        ))
        camera.SetViewUp(0.0, 0.0, 1.0)
        camera.OrthogonalizeViewUp()
        self._reset_clipping_range()
        self.widget.GetRenderWindow().Render()

    def set_autozoom(self, enabled: bool) -> None:
        """Choose whether every snapshot refits the visible geometry."""

        self.autozoom = bool(enabled)
        if self.autozoom:
            self.fit_camera()

    def reset_camera(self) -> None:
        """Restore an isometric view toward the origin, including zoom."""
        if self.renderer is None:
            return
        _reset_isometric_camera(self.renderer)
        self._reset_clipping_range()
        if self._initialized:
            self.widget.GetRenderWindow().Render()

    def set_show_xyz_origin(self, enabled: bool) -> None:
        """Show or hide the world XYZ axes rooted at (0, 0, 0)."""

        self.show_xyz_origin = bool(enabled)
        if self._xyz_origin_axes is not None:
            self._xyz_origin_axes.SetVisibility(self.show_xyz_origin)
        if self.renderer is not None and self._initialized:
            self.widget.GetRenderWindow().Render()

    def set_show_xy_grid(self, enabled: bool) -> None:
        """Show or hide the world XY grid, which expands to fit visible geometry."""

        self.show_xy_grid = bool(enabled)
        if self._xy_grid_actor is not None:
            self._xy_grid_actor.SetVisibility(self.show_xy_grid)
        if self.renderer is not None and self._initialized:
            self._reset_clipping_range()
            self.widget.GetRenderWindow().Render()

    def set_xy_grid_color(self, color: str) -> None:
        rgb = self._parse_hex_color(color)
        if rgb is None:
            return
        self.xy_grid_color = rgb
        if self._xy_grid_actor is not None:
            self._xy_grid_actor.GetProperty().SetColor(*rgb)
        if self.renderer is not None and self._initialized:
            self.widget.GetRenderWindow().Render()

    def set_background_color(self, color: str) -> None:
        rgb = self._parse_hex_color(color)
        if rgb is None:
            return
        self.background_color = rgb
        if self.renderer is not None:
            self.renderer.SetBackground(*rgb)
            if self._initialized:
                self.widget.GetRenderWindow().Render()

    def set_xy_grid_scale(self, scale: float) -> None:
        self.xy_grid_scale = self._clamp_xy_grid_scale(scale)
        if self._last_snapshot is not None:
            self._update_xy_grid(self._snapshot_points(
                self._last_snapshot, include_point_markers=self._last_show_points,
            ))
        else:
            self._update_xy_grid(())
        if self.renderer is not None and self._initialized:
            self._reset_clipping_range()
            self.widget.GetRenderWindow().Render()

    @staticmethod
    def _parse_hex_color(color: str) -> tuple[float, float, float] | None:
        if not isinstance(color, str) or len(color) != 7 or not color.startswith("#"):
            return None
        try:
            return tuple(int(color[index:index + 2], 16) / 255.0 for index in (1, 3, 5))
        except ValueError:
            return None

    @staticmethod
    def _clamp_xy_grid_scale(scale: float) -> float:
        try:
            value = float(scale)
        except (TypeError, ValueError):
            return 1.0
        if value != value or value in (float("inf"), float("-inf")):
            return 1.0
        return max(0.25, min(10.0, value))

    def set_normal_display(self, mode: str) -> None:
        if mode not in self.NORMAL_DISPLAY_MODES:
            raise ValueError(f"Unknown normal display mode: {mode}")
        self.normal_display = mode
        if self.renderer is not None and self._initialized and self._last_snapshot is not None:
            self._render_snapshot(
                self._last_snapshot,
                self._last_selected_id,
                self._last_labels,
                self._last_label_mode,
                self._last_label_texts,
                self._last_label_orders,
                self._last_label_time,
            )

    def set_normal_arrow_limit(self, limit: int) -> None:
        self.normal_arrow_limit = max(0, min(self.MAX_NORMAL_ARROW_LIMIT, int(limit)))
        if self.renderer is not None and self._initialized and self._last_snapshot is not None:
            self._render_snapshot(
                self._last_snapshot,
                self._last_selected_id,
                self._last_labels,
                self._last_label_mode,
                self._last_label_texts,
                self._last_label_orders,
                self._last_label_time,
            )

    @staticmethod
    def _allocate_normal_arrow_quotas(faces, limit: int) -> dict[int, int]:
        """Distribute a model-wide arrow limit across faces fairly."""
        ordered = sorted(faces, key=lambda mesh: (len(mesh.triangles), mesh.entity_id), reverse=True)
        capacities = {
            mesh.entity_id: min(24, len(mesh.triangles)) if mesh.curved else min(1, len(mesh.triangles))
            for mesh in ordered
        }
        quotas = {mesh.entity_id: 0 for mesh in ordered}
        remaining = max(0, min(int(limit), sum(capacities.values())))
        while remaining:
            progressed = False
            for mesh in ordered:
                entity_id = mesh.entity_id
                if quotas[entity_id] >= capacities[entity_id]:
                    continue
                quotas[entity_id] += 1
                remaining -= 1
                progressed = True
                if remaining == 0:
                    break
            if not progressed:
                break
        return quotas

    def set_face_color_mode(self, mode: str) -> None:
        if mode not in self.FACE_COLOR_MODES:
            raise ValueError(f"Unknown face color mode: {mode}")
        self.face_color_mode = mode
        if self.renderer is not None and self._initialized and self._last_snapshot is not None:
            self._render_snapshot(
                self._last_snapshot,
                self._last_selected_id,
                self._last_labels,
                self._last_label_mode,
                self._last_label_texts,
                self._last_label_orders,
                self._last_label_time,
            )

    def set_show_mesh_edges(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.show_mesh_edges == enabled:
            return
        self.show_mesh_edges = enabled
        if self.renderer is not None and self._initialized and self._last_snapshot is not None:
            self._render_snapshot(
                self._last_snapshot,
                self._last_selected_id,
                self._last_labels,
                self._last_label_mode,
                self._last_label_texts,
                self._last_label_orders,
                self._last_label_time,
            )

    def set_mesh_edge_opacity(self, opacity: float) -> None:
        self.mesh_edge_opacity = max(0.0, min(1.0, float(opacity)))
        for actor in self._mesh_edge_actors:
            actor.GetProperty().SetEdgeOpacity(self.mesh_edge_opacity)
            back = actor.GetBackfaceProperty()
            if back is not None:
                back.SetEdgeOpacity(self.mesh_edge_opacity)
        if self.renderer is not None and self._initialized and self._mesh_edge_actors:
            self.widget.GetRenderWindow().Render()

    def set_max_entity_labels(self, limit: int) -> None:
        self.max_entity_labels = self._clamp_label_limit(limit)
        if self.renderer is not None and self._initialized and self._last_snapshot is not None and self._last_labels:
            self._render_snapshot(
                self._last_snapshot,
                self._last_selected_id,
                self._last_labels,
                self._last_label_mode,
                self._last_label_texts,
                self._last_label_orders,
                self._last_label_time,
            )

    def set_face_analysis(
        self,
        face_id: int | None,
        surface_id: int | None = None,
        boundary_edge_ids=(),
        boundary_vertex_ids=(),
        *,
        refresh: bool = True,
    ) -> None:
        """Highlight one face, its boundary topology, and its control points."""

        self._analysis_face_id = face_id
        self._analysis_surface_id = surface_id if face_id is not None else None
        self._analysis_boundary_edge_ids = frozenset(boundary_edge_ids) if face_id is not None else frozenset()
        self._analysis_boundary_vertex_ids = frozenset(boundary_vertex_ids) if face_id is not None else frozenset()
        if refresh and self.renderer is not None and self._initialized and self._last_snapshot is not None:
            self._render_snapshot(
                self._last_snapshot,
                self._last_selected_id,
                self._last_labels,
                self._last_label_mode,
                self._last_label_texts,
                self._last_label_orders,
                self._last_label_time,
            )

    def set_face_analysis_display(
        self,
        face_opacity: float = 0.86,
        context_opacity: float = 0.07,
        triangle_edges: bool = True,
        show_normals: bool = False,
        normal_scale: float = 1.0,
    ) -> None:
        """Update face-analysis rendering options without rebuilding geometry."""

        new_opacity = max(0.05, min(1.0, float(face_opacity)))
        new_context_opacity = max(0.0, min(0.3, float(context_opacity)))
        new_triangle_edges = bool(triangle_edges)
        new_show_normals = bool(show_normals)
        new_normal_scale = max(0.25, min(3.0, float(normal_scale)))
        style_changed = (
            new_opacity != self._analysis_face_opacity
            or new_context_opacity != self._analysis_context_opacity
            or new_triangle_edges != self._analysis_triangle_edges
            or new_show_normals != self._analysis_show_normals
        )
        scale_changed = new_normal_scale != self._analysis_normal_scale
        self._analysis_face_opacity = new_opacity
        self._analysis_context_opacity = new_context_opacity
        self._analysis_triangle_edges = new_triangle_edges
        self._analysis_show_normals = new_show_normals
        self._analysis_normal_scale = new_normal_scale
        if not style_changed and not scale_changed:
            return
        if (not style_changed and self._analysis_normal_glyph is not None
                and self.renderer is not None and self._initialized):
            self._analysis_normal_glyph.SetScaleFactor(self._normal_length * new_normal_scale)
            self.widget.GetRenderWindow().Render()
            return
        if self.renderer is not None and self._initialized and self._last_snapshot is not None:
            self._render_snapshot(
                self._last_snapshot,
                self._last_selected_id,
                self._last_labels,
                self._last_label_mode,
                self._last_label_texts,
                self._last_label_orders,
                self._last_label_time,
            )

    def fit_next_snapshot(self) -> None:
        """Ensure the next rendered snapshot is framed, even with autozoom off."""

        self._fit_camera_on_next_render = True

    def fit_camera(self) -> None:
        """Immediately frame the current scene and preserve that choice for a later render if needed."""

        self._fit_camera_on_next_render = True
        if self.renderer is None or not self._initialized:
            return
        self.renderer.ResetCamera()
        self._reset_clipping_range()
        self._fit_camera_on_next_render = False
        self.widget.GetRenderWindow().Render()

    def fit_analysis_face(self) -> None:
        """Fit the analyzed mesh, or its control net/boundary when unmeshed."""

        if self.renderer is None or not self._initialized or self._last_snapshot is None:
            return
        face = next(
            (face for face in self._last_snapshot.faces if face.entity_id == self._analysis_face_id),
            None,
        )
        points = list(face.points) if face is not None else []
        if not points:
            points.extend(
                point
                for net in self._last_snapshot.control_nets
                if net.entity_id == self._analysis_surface_id
                for point in net.point_markers
            )
            points.extend(
                point
                for edge in self._last_snapshot.polylines
                if edge.entity_id in self._analysis_boundary_edge_ids
                for point in edge.points
            )
            points.extend(
                point for entity_id, point in self._last_snapshot.vertices.items()
                if entity_id in self._analysis_boundary_vertex_ids
            )
        if not points:
            return
        bounds = tuple(
            bound
            for axis in ("x", "y", "z")
            for bound in (
                min(getattr(point, axis) for point in points),
                max(getattr(point, axis) for point in points),
            )
        )
        self.renderer.ResetCamera(bounds)
        self._reset_clipping_range()
        self._render_snapshot(
            self._last_snapshot,
            self._last_selected_id,
            self._last_labels,
            self._last_label_mode,
            self._last_label_texts,
            self._last_label_orders,
            self._last_label_time,
        )

    def set_issue_edge(self, edge_id: int | None, face_ids=()) -> None:
        """Mark the exact shared STEP edge and its two adjacent preview faces."""
        self._issue_blink_timer.stop()
        self._issue_blink_steps.clear()
        self._clear_issue_overview()
        self._issue_edge_id = edge_id
        if self.renderer is None or not self._initialized:
            return
        if self._issue_edge_actor is not None:
            self.renderer.RemoveActor(self._issue_edge_actor)
            self._issue_edge_actor = None
        for actor, original, backface in self._issue_face_properties:
            actor.GetProperty().DeepCopy(original)
            actor.SetBackfaceProperty(backface)
        self._issue_face_properties.clear()
        if edge_id is None or self._last_snapshot is None:
            self.widget.GetRenderWindow().Render()
            return
        for face_id, color in zip(face_ids, ((1.0, 0.54, 0.16), (0.25, 0.93, 0.66))):
            actor = self._face_actors.get(face_id)
            if actor is None:
                continue
            original = vtk.vtkProperty()
            original.DeepCopy(actor.GetProperty())
            self._issue_face_properties.append((actor, original, actor.GetBackfaceProperty()))
            actor.SetBackfaceProperty(None)
            actor.GetProperty().SetColor(*color)
            actor.GetProperty().SetOpacity(0.72)
            actor.GetProperty().LightingOff()
        edge = next(
            (item for item in self._last_snapshot.polylines if item.entity_id == edge_id), None
        )
        if edge is not None and len(edge.points) >= 2:
            points = vtk.vtkPoints()
            for point in edge.points:
                points.InsertNextPoint(point.x, point.y, point.z)
            line = vtk.vtkPolyLine()
            line.GetPointIds().SetNumberOfIds(len(edge.points))
            for index in range(len(edge.points)):
                line.GetPointIds().SetId(index, index)
            cells = vtk.vtkCellArray()
            cells.InsertNextCell(line)
            data = vtk.vtkPolyData()
            data.SetPoints(points)
            data.SetLines(cells)
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(data)
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(1.0, 0.12, 0.85)
            actor.GetProperty().SetLineWidth(7)
            actor.GetProperty().LightingOff()
            actor.SetPickable(False)
            self.renderer.AddActor(actor)
            self._issue_edge_actor = actor
        self.widget.GetRenderWindow().Render()

    def _clear_issue_overview(self) -> None:
        if self.renderer is None:
            return
        for actor in self._issue_overview_edge_actors:
            self.renderer.RemoveActor(actor)
        self._issue_overview_edge_actors.clear()
        for actor, original, backface in self._issue_overview_properties:
            actor.GetProperty().DeepCopy(original)
            actor.SetBackfaceProperty(backface)
        self._issue_overview_properties.clear()

    def set_issue_overview(self, pairs, selected_edge_id: int | None = None) -> None:
        """Show every inconsistent STEP edge and its adjacent faces together."""
        self.set_issue_edge(None)
        self._issue_edge_id = selected_edge_id
        if self.renderer is None or not self._initialized or self._last_snapshot is None:
            return
        edge_ids = {pair.edge_id for pair in pairs}
        face_ids = sorted({use.face_id for pair in pairs for use in pair.uses})
        if not edge_ids:
            return
        grey = (0.38, 0.41, 0.44)
        face_actor_ids = {id(actor) for actor in self._face_actors.values()}
        for actor in self._actors:
            if not actor.IsA("vtkActor"):
                continue
            original = vtk.vtkProperty()
            original.DeepCopy(actor.GetProperty())
            self._issue_overview_properties.append((actor, original, actor.GetBackfaceProperty()))
            actor.SetBackfaceProperty(None)
            actor.GetProperty().SetColor(*grey)
            actor.GetProperty().SetOpacity(0.4 if id(actor) in face_actor_ids else 0.7)
            actor.GetProperty().LightingOff()
        base_hues = (0.15, 0.36, 0.55)
        for index, face_id in enumerate(face_ids):
            actor = self._face_actors.get(face_id)
            if actor is None:
                continue
            rng = Random(face_id)
            hue = base_hues[index % len(base_hues)] + rng.uniform(-0.025, 0.025)
            color = hsv_to_rgb(hue, rng.uniform(0.72, 0.9), rng.uniform(0.88, 1.0))
            actor.GetProperty().SetColor(*color)
            actor.GetProperty().SetOpacity(0.98)
        for edge in self._last_snapshot.polylines:
            if edge.entity_id not in edge_ids or len(edge.points) < 2:
                continue
            points = vtk.vtkPoints()
            for point in edge.points:
                points.InsertNextPoint(point.x, point.y, point.z)
            line = vtk.vtkPolyLine()
            line.GetPointIds().SetNumberOfIds(len(edge.points))
            for index in range(len(edge.points)):
                line.GetPointIds().SetId(index, index)
            cells = vtk.vtkCellArray()
            cells.InsertNextCell(line)
            data = vtk.vtkPolyData()
            data.SetPoints(points)
            data.SetLines(cells)
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(data)
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(1.0, 0.08, 0.05)
            actor.GetProperty().SetLineWidth(8)
            actor.GetProperty().LightingOff()
            actor.SetPickable(False)
            self.renderer.AddActor(actor)
            self._issue_overview_edge_actors.append(actor)
        self.widget.GetRenderWindow().Render()

    def blink_issue_edge_and_faces(self) -> None:
        """Flash the edge three times, then each adjacent face three times."""
        if self.renderer is None or not self._initialized or self._issue_edge_actor is None:
            return
        self._issue_blink_timer.stop()
        self._issue_blink_steps = [
            (target, visible)
            for target in ("edge", "face0", "face1")
            for _ in range(3)
            for visible in (True, False)
        ]
        self._issue_blink_index = 0
        self._advance_issue_blink()

    def _advance_issue_blink(self) -> None:
        if self.renderer is None or self._issue_edge_actor is None:
            self._issue_blink_steps.clear()
            return
        finished = self._issue_blink_index >= len(self._issue_blink_steps)
        target, visible = (None, False) if finished else self._issue_blink_steps[self._issue_blink_index]
        self._issue_edge_actor.SetVisibility(finished or (target == "edge" and visible))
        colors = ((1.0, 0.54, 0.16), (0.25, 0.93, 0.66))
        for index, (actor, original, backface) in enumerate(self._issue_face_properties):
            actor.GetProperty().DeepCopy(original)
            actor.SetBackfaceProperty(backface)
            if finished or (target == f"face{index}" and visible):
                actor.SetBackfaceProperty(None)
                actor.GetProperty().SetColor(*colors[index])
                actor.GetProperty().SetOpacity(1.0 if not finished else 0.72)
                actor.GetProperty().LightingOff()
        self.widget.GetRenderWindow().Render()
        if finished:
            self._issue_blink_steps.clear()
        else:
            self._issue_blink_index += 1
            self._issue_blink_timer.start(260)

    def fit_issue_edge(self) -> None:
        if self.renderer is None or self._last_snapshot is None or self._issue_edge_id is None:
            return
        edge = next(
            (item for item in self._last_snapshot.polylines if item.entity_id == self._issue_edge_id), None
        )
        if edge is None or not edge.points:
            return
        bounds = []
        for axis in ("x", "y", "z"):
            values = [getattr(point, axis) for point in edge.points]
            bounds.extend((min(values), max(values)))
        self.renderer.ResetCamera(tuple(bounds))
        self._reset_clipping_range()
        self.widget.GetRenderWindow().Render()

    @classmethod
    def _clamp_label_limit(cls, limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int):
            return cls.DEFAULT_MAX_ENTITY_LABELS
        return max(1, min(2000, limit))

    def showEvent(self, event):
        """Initialize VTK only after Qt has created and shown the native window.

        QVTKRenderWindowInteractor obtains a native window ID in its
        constructor. Initializing it before the containing QMainWindow is
        shown can leave that ID stale on X11/XWayland and produces BadWindow
        errors during the first resize.
        """

        super().showEvent(event)
        self._ensure_initialized()

    def closeEvent(self, event):
        if self._initialized:
            self.widget.Finalize()
            self._initialized = False
        super().closeEvent(event)

    def _ensure_initialized(self):
        if self.renderer is None or self._initialized:
            return
        self.widget.Initialize()
        self._initialized = True
        if self._last_snapshot is not None:
            self._render_snapshot(
                self._last_snapshot,
                self._last_selected_id,
                self._last_labels,
                self._last_label_mode,
                self._last_label_texts,
                self._last_label_orders,
                self._last_label_time,
            )

    def show_snapshot(
        self,
        snapshot,
        selected_id: int | None = None,
        labels: bool = False,
        label_mode: str = "index",
        label_texts: dict[int, str] | None = None,
        label_orders: dict[int, int] | None = None,
        label_time: int | None = None,
        control_net_densities: dict[int, int] | None = None,
        incremental: bool = False,
        show_points: bool | None = None,
        fast_preview: bool | None = None,
    ) -> None:
        if self.renderer is None:
            return
        selection_changed = selected_id != self._last_selected_id
        if selection_changed:
            self._selection_flash_timer.stop()
            self._selection_flash_face_id = None
        self._last_snapshot = snapshot
        self._last_selected_id = selected_id
        self._last_labels = labels
        self._last_label_mode = label_mode if label_mode in {"index", "type"} else "index"
        self._last_label_texts = label_texts or {}
        self._last_label_orders = label_orders or {}
        self._last_label_time = label_time if label_time is not None else 0
        self._last_control_net_densities = control_net_densities or {}
        if show_points is not None:
            self._last_show_points = bool(show_points)
        if fast_preview is not None:
            self._last_fast_preview = bool(fast_preview)
        if not self._initialized:
            return
        self._render_snapshot(
            snapshot,
            selected_id,
            labels,
            self._last_label_mode,
            self._last_label_texts,
            self._last_label_orders,
            self._last_label_time,
            incremental=incremental,
        )
        if selection_changed:
            self._flash_selected_face(selected_id)

    @staticmethod
    def _brighten_color(color: tuple[float, float, float], amount: float) -> tuple[float, float, float]:
        return tuple(component + (1.0 - component) * amount for component in color)

    def _face_base_colors(self, entity_id: int) -> tuple[tuple[float, float, float], tuple[float, float, float] | None]:
        if self.face_color_mode == "normals":
            return (0.2, 0.5, 1.0), (1.0, 0.25, 0.2)
        return self._mesh_color(entity_id), None

    def _set_selected_face_brightness(self, actor, entity_id: int, amount: float) -> None:
        front, back = self._face_base_colors(entity_id)
        actor.GetProperty().SetColor(*self._brighten_color(front, amount))
        if back is not None and actor.GetBackfaceProperty() is not None:
            actor.GetBackfaceProperty().SetColor(*self._brighten_color(back, amount))

    def _flash_selected_face(self, entity_id: int | None) -> None:
        actor = self._face_actors.get(entity_id)
        if actor is None:
            return
        self._selection_flash_face_id = entity_id
        self._set_selected_face_brightness(actor, entity_id, 0.72)
        if self._initialized:
            self.widget.GetRenderWindow().Render()
        self._selection_flash_timer.start(180)

    def _finish_selection_flash(self) -> None:
        entity_id = self._selection_flash_face_id
        self._selection_flash_face_id = None
        if entity_id != self._last_selected_id:
            return
        actor = self._face_actors.get(entity_id)
        if actor is not None:
            self._set_selected_face_brightness(actor, entity_id, 0.4)
            if self._initialized:
                self.widget.GetRenderWindow().Render()

    def _render_snapshot(
        self,
        snapshot,
        selected_id: int | None,
        labels: bool,
        label_mode: str,
        label_texts,
        label_orders,
        label_time: int,
        control_net_densities: dict[int, int] | None = None,
        incremental: bool = False,
    ) -> None:
        if incremental and self._can_render_incrementally(
            snapshot, labels, label_mode, control_net_densities,
        ):
            self._render_snapshot_incremental(
                snapshot,
                selected_id,
                label_mode,
                label_texts,
                label_orders,
                label_time,
                control_net_densities,
            )
            return

        snapshot_points = self._snapshot_points(
            snapshot, include_point_markers=self._last_show_points,
        )
        self._rendered_bounds = self._bounds(snapshot_points)
        self._update_xy_grid(snapshot_points)
        self._update_xyz_origin_axes(snapshot_points)
        for actor in self._actors:
            self.renderer.RemoveActor(actor)
        self._actors.clear()
        self._actors_by_entity.clear()
        self._face_actors.clear()
        self._analysis_normal_glyph = None
        self._rendered_items = self._snapshot_render_items(snapshot)
        self._rendered_selected_id = selected_id
        self._mesh_edge_actors.clear()
        self._label_specs: list[_LabelSpec] = []
        self._active_label_orders = label_orders
        self._active_label_time = label_time
        control_net_densities = control_net_densities or self._last_control_net_densities
        self._normal_arrow_quotas = self._allocate_normal_arrow_quotas(
            snapshot.faces, getattr(self, "normal_arrow_limit", self.DEFAULT_NORMAL_ARROW_LIMIT),
        )
        analysis_active = self._analysis_face_id is not None
        show_normals = (
            getattr(self, "normal_display", "Hidden") == "Vectors"
            or (analysis_active and self._analysis_show_normals)
        )
        if show_normals and snapshot.faces:
            normal_faces = [face for face in snapshot.faces if face.entity_id == self._analysis_face_id] if analysis_active else snapshot.faces
            face_points = [point for face in (normal_faces or snapshot.faces) for point in face.points]
            extents = [
                max(getattr(point, axis) for point in face_points)
                - min(getattr(point, axis) for point in face_points)
                for axis in ("x", "y", "z")
            ]
            self._normal_length = max(
                max(extents) * 0.05, 1e-6,
            )
        label_scale = self._label_scale(snapshot) if (labels or analysis_active) else 1.0
        for entity_id, (kind, item) in self._rendered_items.items():
            self._render_entity_item(
                entity_id,
                kind,
                item,
                selected_id,
                labels,
                label_mode,
                label_texts,
                label_scale,
                control_net_densities,
                analysis_active,
                self._last_show_points,
            )
        self._active_entity_id = None
        if (self.autozoom and not analysis_active) or self._fit_camera_on_next_render:
            self.renderer.ResetCamera()
            self._fit_camera_on_next_render = False
        self._reset_clipping_range()
        if labels or analysis_active:
            self._place_labels()
        self._update_warning_balloons(snapshot)
        self.widget.GetRenderWindow().Render()
        self._rendered_style_signature = self._render_style_signature(
            labels, label_mode, control_net_densities,
            self._last_show_points, self._last_fast_preview,
        )

    @staticmethod
    def _snapshot_render_items(snapshot) -> dict[int, tuple[str, object]]:
        items = {entity_id: ("point", point) for entity_id, point in snapshot.points.items()}
        items.update({entity_id: ("vertex", point) for entity_id, point in snapshot.vertices.items()})
        items.update({item.entity_id: ("polyline", item) for item in snapshot.polylines})
        items.update({item.entity_id: ("control_net", item) for item in snapshot.control_nets})
        items.update({item.entity_id: ("face", item) for item in snapshot.faces})
        return items

    @staticmethod
    def _bounds(points) -> tuple[float, float, float, float, float, float] | None:
        if not points:
            return None
        return (
            min(point.x for point in points), max(point.x for point in points),
            min(point.y for point in points), max(point.y for point in points),
            min(point.z for point in points), max(point.z for point in points),
        )

    @staticmethod
    def _bounds_corners(bounds):
        if bounds is None:
            return []
        return [
            Point3(x, y, z)
            for x in (bounds[0], bounds[1])
            for y in (bounds[2], bounds[3])
            for z in (bounds[4], bounds[5])
        ]

    def _render_style_signature(
        self, labels, label_mode, control_net_densities, show_points=True, fast_preview=False,
    ):
        densities = control_net_densities or self._last_control_net_densities
        return (
            labels,
            label_mode,
            self.face_color_mode,
            self.show_mesh_edges,
            self.mesh_edge_opacity,
            self.normal_display,
            tuple(sorted(densities.items())),
            show_points,
            fast_preview,
        )

    def _can_render_incrementally(self, snapshot, labels, label_mode, control_net_densities):
        return (
            snapshot.render_updates is not None
            and not labels
            and self._analysis_face_id is None
            and self.normal_display == "Hidden"
            and not self._fit_camera_on_next_render
            and self._rendered_style_signature == self._render_style_signature(
                labels, label_mode, control_net_densities, self._last_show_points,
                self._last_fast_preview,
            )
        )

    def _render_snapshot_incremental(
        self,
        snapshot,
        selected_id,
        label_mode,
        label_texts,
        label_orders,
        label_time,
        control_net_densities,
    ) -> None:
        updates = snapshot.render_updates or {}
        faces_changed = False
        changed_face_ids = set()
        bounds_may_shrink = False
        for entity_id, item in updates.items():
            previous = self._rendered_items.get(entity_id)
            faces_changed |= (previous is not None and previous[0] == "face")
            if previous is not None and previous[0] == "face":
                changed_face_ids.add(entity_id)
            bounds_may_shrink |= (
                previous is not None
                and (item is None or previous[0] in {"polyline", "control_net", "face"})
            )
            if item is None:
                self._rendered_items.pop(entity_id, None)
            else:
                self._rendered_items[entity_id] = item
                faces_changed |= item[0] == "face"
                if item[0] == "face":
                    changed_face_ids.add(entity_id)

        affected = dict.fromkeys(updates)
        if self._rendered_selected_id is not None:
            affected.setdefault(self._rendered_selected_id, None)
        if selected_id is not None:
            affected.setdefault(selected_id, None)
        self._active_label_orders = label_orders
        self._active_label_time = label_time
        for entity_id in affected:
            self._remove_entity_actors(entity_id)
            record = self._rendered_items.get(entity_id)
            if record is not None:
                self._render_entity_item(
                    entity_id,
                    record[0],
                    record[1],
                    selected_id,
                    False,
                    label_mode,
                    label_texts,
                    1.0,
                    control_net_densities or self._last_control_net_densities,
                    False,
                    self._last_show_points,
                )
        self._active_entity_id = None

        changed_points = []
        for record in updates.values():
            if record is None:
                continue
            kind, item = record
            if kind in {"point", "vertex"}:
                if self._last_show_points:
                    changed_points.append(item)
            elif kind == "polyline":
                changed_points.extend(item.points)
            elif kind == "control_net":
                changed_points.extend(item.point_markers)
            elif kind == "face":
                changed_points.extend(item.points)
        previous_bounds = self._rendered_bounds
        if bounds_may_shrink:
            self._rendered_bounds = self._bounds(self._snapshot_points(
                snapshot, include_point_markers=self._last_show_points,
            ))
        elif changed_points:
            changed_bounds = self._bounds(changed_points)
            if previous_bounds is None:
                self._rendered_bounds = changed_bounds
            elif changed_bounds is not None:
                self._rendered_bounds = (
                    min(previous_bounds[0], changed_bounds[0]), max(previous_bounds[1], changed_bounds[1]),
                    min(previous_bounds[2], changed_bounds[2]), max(previous_bounds[3], changed_bounds[3]),
                    min(previous_bounds[4], changed_bounds[4]), max(previous_bounds[5], changed_bounds[5]),
                )
        bounds_changed = self._rendered_bounds != previous_bounds
        if bounds_changed:
            bound_points = self._bounds_corners(self._rendered_bounds)
            self._update_xy_grid(bound_points)
            self._update_xyz_origin_axes(bound_points)

        if ((self.autozoom and bounds_changed and self._analysis_face_id is None)
                or self._fit_camera_on_next_render):
            self.renderer.ResetCamera()
            self._fit_camera_on_next_render = False
        self._reset_clipping_range()
        if faces_changed:
            self._update_warning_balloons(snapshot, changed_face_ids=changed_face_ids)
        self.widget.GetRenderWindow().Render()
        self._rendered_selected_id = selected_id
        self._rendered_style_signature = self._render_style_signature(
            False, label_mode, control_net_densities,
            self._last_show_points, self._last_fast_preview,
        )

    def _render_entity_item(
        self,
        entity_id,
        kind,
        item,
        selected_id,
        labels,
        label_mode,
        label_texts,
        label_scale,
        control_net_densities,
        analysis_active,
        show_points=True,
    ) -> None:
        selected = selected_id == entity_id
        self._active_entity_id = entity_id
        label_text = self._label_text(entity_id, label_mode, label_texts)
        if kind in {"point", "vertex"}:
            category = "Geometry" if kind == "point" else "Topology"
            boundary_vertex = analysis_active and entity_id in self._analysis_boundary_vertex_ids
            if not show_points and not boundary_vertex:
                return
            self._add_point(
                entity_id,
                item.x,
                item.y,
                item.z,
                selected,
                category,
                labels or boundary_vertex,
                label_scale,
                f"#{entity_id}" if boundary_vertex else label_text,
                dimmed=analysis_active and not boundary_vertex,
                highlighted=boundary_vertex,
                force_label=boundary_vertex,
            )
        elif kind == "polyline":
            boundary_edge = analysis_active and entity_id in self._analysis_boundary_edge_ids
            self._add_polyline(
                entity_id,
                item.points,
                selected,
                "Geometry" if item.category == "edge" else "Structure",
                labels,
                label_scale,
                label_text,
                dimmed=analysis_active and not boundary_edge,
                highlighted=boundary_edge,
            )
        elif kind == "control_net":
            analyzed_net = analysis_active and entity_id == self._analysis_surface_id
            density = (
                100 if analyzed_net else
                control_net_densities.get(entity_id, self.DEFAULT_CONTROL_NET_DENSITY)
            )
            self._add_control_net(
                item.at_density(density),
                selected and not analysis_active,
                labels,
                label_scale,
                label_text,
                dimmed=analysis_active and not analyzed_net,
                highlighted=analyzed_net,
                point_labels=analyzed_net,
            )
        elif kind == "face":
            analyzed_face = analysis_active and entity_id == self._analysis_face_id
            self._add_mesh(
                item,
                selected and not analysis_active,
                labels,
                label_scale,
                label_text,
                dimmed=analysis_active and not analyzed_face,
                highlighted=analyzed_face,
            )

    def _register_actor(self, actor) -> None:
        self._actors.append(actor)
        if self._active_entity_id is not None:
            self._actors_by_entity.setdefault(self._active_entity_id, []).append(actor)

    def _remove_entity_actors(self, entity_id: int) -> None:
        self._face_actors.pop(entity_id, None)
        actors = self._actors_by_entity.pop(entity_id, ())
        if not actors:
            return
        removed = {id(actor) for actor in actors}
        for actor in actors:
            self.renderer.RemoveActor(actor)
        self._actors = [actor for actor in self._actors if id(actor) not in removed]
        self._mesh_edge_actors = [actor for actor in self._mesh_edge_actors if id(actor) not in removed]

    def _update_warning_balloons(self, snapshot, changed_face_ids=None) -> None:
        if changed_face_ids is not None:
            removed_buttons = set()
            for entity_id in changed_face_ids:
                existing = self._warning_balloons_by_id.pop(entity_id, None)
                if existing is not None:
                    button, _ = existing
                    button.hide()
                    button.deleteLater()
                    removed_buttons.add(id(button))
            self._warning_balloons = [
                pair for pair in self._warning_balloons
                if id(pair[0]) not in removed_buttons
            ]
            for entity_id in sorted(changed_face_ids):
                record = self._rendered_items.get(entity_id)
                mesh = record[1] if record is not None and record[0] == "face" else None
                self._add_warning_balloon(mesh)
            self._position_warning_balloons()
            return

        for button, _ in self._warning_balloons:
            button.hide()
            button.deleteLater()
        self._warning_balloons = []
        self._warning_balloons_by_id = {}
        for mesh in snapshot.faces:
            self._add_warning_balloon(mesh)
        self._position_warning_balloons()

    def _add_warning_balloon(self, mesh) -> None:
        if mesh is None:
            return
        warning = getattr(mesh, "orientation_warning", None)
        if not warning or not mesh.points:
            return
        if len(self._warning_balloons) >= 50:
            return
        button = QPushButton(f"#{mesh.entity_id} possible wrong normal  ⓘ", self.widget)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip("Click for orientation details")
        button.setStyleSheet(
            "QPushButton { background: #fff1bd; color: #302713; border: 1px solid #d69a23; "
            "border-radius: 10px; padding: 6px 10px; } "
            "QPushButton:hover { background: #ffdb75; }"
        )
        button.clicked.connect(
            lambda checked=False, entity_id=mesh.entity_id, message=warning:
                self.orientation_warning_clicked.emit(entity_id, message)
        )
        button.adjustSize()
        indices = mesh.triangles[0] if mesh.triangles else (0,)
        anchor = tuple(sum(getattr(mesh.points[i], axis) for i in indices) / len(indices)
                       for axis in ("x", "y", "z"))
        pair = (button, anchor)
        self._warning_balloons.append(pair)
        self._warning_balloons_by_id[mesh.entity_id] = pair

    def _position_warning_balloons(self, *_args) -> None:
        if not getattr(self, "_warning_balloons", None):
            return
        render_width, render_height = self.widget.GetRenderWindow().GetSize()
        if render_width <= 0 or render_height <= 0:
            return
        placed = []
        for button, anchor in self._warning_balloons:
            self.renderer.SetWorldPoint(*anchor, 1.0)
            self.renderer.WorldToDisplay()
            x, y, depth = self.renderer.GetDisplayPoint()
            if not (0 <= depth <= 1 and 0 <= x <= render_width and 0 <= y <= render_height):
                button.hide()
                continue
            x = round(x * self.widget.width() / render_width)
            y = round((render_height - y) * self.widget.height() / render_height)
            x = max(0, min(x + 12, self.widget.width() - button.width()))
            y = max(0, min(y - button.height() - 12, self.widget.height() - button.height()))
            button.move(x, y)
            while any(button.geometry().intersects(rect) for rect in placed):
                y += button.height() + 4
                if y + button.height() > self.widget.height():
                    break
                button.move(x, y)
            placed.append(button.geometry())
            button.show()
            button.raise_()

    @staticmethod
    def _snapshot_points(snapshot, *, include_point_markers: bool = True) -> list:
        points = [point for face in snapshot.faces for point in face.points]
        points.extend(point for polyline in snapshot.polylines for point in polyline.points)
        points.extend(point for control_net in snapshot.control_nets for point in control_net.point_markers)
        if include_point_markers:
            points.extend(snapshot.points.values())
            points.extend(snapshot.vertices.values())
        return points

    def _update_xyz_origin_axes(self, points) -> None:
        """Keep the world-origin marker small relative to the visible model."""

        axes = self._xyz_origin_axes
        if axes is None:
            return
        if not points:
            length = 1.0
        else:
            span = max(
                max(getattr(point, axis) for point in points) - min(getattr(point, axis) for point in points)
                for axis in ("x", "y", "z")
            )
            length = max(span * 0.08, 1e-6)
        axes.SetTotalLength(length, length, length)

    def _reset_clipping_range(self) -> None:
        """Include the grid in clipping without letting it change camera framing."""

        bounds = self.renderer.ComputeVisiblePropBounds()
        grid = getattr(self, "_xy_grid_actor", None)
        if grid is not None and grid.GetVisibility():
            grid_bounds = grid.GetBounds()
            if all(grid_bounds[index] <= grid_bounds[index + 1] for index in (0, 2, 4)):
                if all(bounds[index] <= bounds[index + 1] for index in (0, 2, 4)):
                    bounds = tuple(
                        min(bounds[index], grid_bounds[index]) if index % 2 == 0
                        else max(bounds[index], grid_bounds[index])
                        for index in range(6)
                    )
                else:
                    bounds = grid_bounds
        if all(bounds[index] <= bounds[index + 1] for index in (0, 2, 4)):
            self.renderer.ResetCameraClippingRange(bounds)
        else:
            self.renderer.ResetCameraClippingRange()

    def _update_xy_grid(self, points) -> None:
        """Build a world-origin XY grid with bounds that grow to cover the model."""

        if not points:
            bounds = (-5.0, 5.0, -5.0, 5.0)
            spacing = 1.0
        else:
            min_x = min(point.x for point in points)
            max_x = max(point.x for point in points)
            min_y = min(point.y for point in points)
            max_y = max(point.y for point in points)
            extent = max(
                abs(min_x),
                abs(max_x),
                abs(min_y),
                abs(max_y),
                max_x - min_x,
                max_y - min_y,
                1e-6,
            )
            spacing = self._nice_grid_spacing(extent / 10.0)
            margin = max((max_x - min_x) * 0.08, (max_y - min_y) * 0.08, spacing)
            bounds = (
                floor((min(min_x, 0.0) - margin) / spacing) * spacing,
                ceil((max(max_x, 0.0) + margin) / spacing) * spacing,
                floor((min(min_y, 0.0) - margin) / spacing) * spacing,
                ceil((max(max_y, 0.0) + margin) / spacing) * spacing,
            )

        min_x, max_x, min_y, max_y = bounds
        min_x *= self.xy_grid_scale
        max_x *= self.xy_grid_scale
        min_y *= self.xy_grid_scale
        max_y *= self.xy_grid_scale
        self.xy_grid_world_size = (max_x - min_x, max_y - min_y)
        actor = self._xy_grid_actor
        if actor is None:
            return
        grid_points = vtk.vtkPoints()
        grid_points.SetDataTypeToDouble()
        grid_lines = vtk.vtkCellArray()

        def add_line(start, end):
            first = grid_points.InsertNextPoint(*start)
            second = grid_points.InsertNextPoint(*end)
            grid_lines.InsertNextCell(2)
            grid_lines.InsertCellPoint(first)
            grid_lines.InsertCellPoint(second)

        x_index = floor(min_x / spacing)
        x_end_index = ceil(max_x / spacing)
        for index in range(x_index, x_end_index + 1):
            x = index * spacing
            add_line((x, min_y, 0.0), (x, max_y, 0.0))
        y_index = floor(min_y / spacing)
        y_end_index = ceil(max_y / spacing)
        for index in range(y_index, y_end_index + 1):
            y = index * spacing
            add_line((min_x, y, 0.0), (max_x, y, 0.0))

        grid_data = vtk.vtkPolyData()
        grid_data.SetPoints(grid_points)
        grid_data.SetLines(grid_lines)
        self._xy_grid_mapper.SetInputData(grid_data)
        self._xy_grid_mapper.Update()

    @staticmethod
    def _nice_grid_spacing(target: float) -> float:
        """Choose a readable 1/2/5 × 10ⁿ interval near *target*."""

        magnitude = 10.0 ** floor(log10(max(target, 1e-12)))
        scaled = target / magnitude
        if scaled <= 1.0:
            multiplier = 1.0
        elif scaled <= 2.0:
            multiplier = 2.0
        elif scaled <= 5.0:
            multiplier = 5.0
        else:
            multiplier = 10.0
        return multiplier * magnitude

    def _label_scale(self, snapshot) -> float:
        points = [*snapshot.points.values(), *snapshot.vertices.values()]
        points.extend(point for polyline in snapshot.polylines for point in polyline.points)
        points.extend(point for control_net in snapshot.control_nets for point in control_net.point_markers)
        points.extend(point for face in snapshot.faces for point in face.points)
        if not points:
            return 1.0
        span = max(
            max(point.x for point in points) - min(point.x for point in points),
            max(point.y for point in points) - min(point.y for point in points),
            max(point.z for point in points) - min(point.z for point in points),
        )
        return max(span * 0.03, 1e-9)

    @staticmethod
    def _label_text(entity_id, label_mode, label_texts):
        if label_mode == "type":
            return label_texts.get(entity_id, "Entity")
        return f"#{entity_id}"

    def _add_label(
        self, entity_id, position, color, scale, text, selected=False, priority=0,
        dimmed=False, force_visible=False,
    ):
        arrival_order = self._active_label_orders.get(entity_id, self._active_label_time)
        self._label_specs.append(
            _LabelSpec(
                entity_id, position, color, scale, text, selected, priority,
                arrival_order, dimmed, force_visible,
            )
        )

    def _place_labels(self) -> None:
        """Place labels in display space and add callouts for collisions."""

        width, height = self.widget.GetRenderWindow().GetSize()
        if width <= 0 or height <= 0:
            return

        occupied: list[tuple[float, float, float, float]] = []
        candidates = self._visible_label_candidates(width, height)
        for spec, display_anchor, label_width, label_height, fade in candidates:
            anchor = spec.anchor
            color = (0.48, 0.50, 0.53) if spec.dimmed else self._fade_color(spec.color, fade)
            scale = spec.scale
            text = spec.text
            position, box, displaced = self._label_position(
                display_anchor,
                label_width,
                label_height,
                occupied,
                width,
                height,
            )
            occupied.append(box)
            label_position = self._display_to_world(position[0], position[1], display_anchor[2])
            label = vtk.vtkBillboardTextActor3D()
            label.SetInput(text)
            label.SetPosition(*label_position)
            label.SetScale(scale, scale, scale)
            label.SetPickable(False)
            text_property = label.GetTextProperty()
            text_property.SetColor(*color)
            text_property.SetOpacity(
                self._analysis_context_opacity if spec.dimmed else 1.0 - 0.9 * fade
            )
            text_property.SetBold(True)
            if displaced and not spec.dimmed:
                text_property.SetBackgroundColor(0.06, 0.08, 0.11)
                text_property.SetBackgroundOpacity(
                    self._analysis_context_opacity if spec.dimmed else 0.88 * (1.0 - 0.9 * fade)
                )
                text_property.SetFrame(True)
                text_property.SetFrameColor(*color)
                text_property.SetFrameWidth(1)
            self.renderer.AddActor(label)
            self._register_actor(label)
            if displaced and not spec.dimmed:
                self._add_leader(
                    anchor, label_position, color,
                    self._analysis_context_opacity if spec.dimmed else 1.0 - 0.9 * fade,
                )

    def _visible_label_candidates(self, width, height):
        """Project, prioritize, and thin labels before creating VTK actors.

        The old path attempted collision placement for every rendered entity.
        This selection pass projects each rendered entity once and limits the
        expensive actor/collision path to the configured budget.
        Selected entities win, followed by newer labels and then entity
        priority. A screen-space grid prevents a dense cluster from consuming
        the whole budget.
        """

        projected = []
        for order, spec in enumerate(self._label_specs):
            display_anchor = self._world_to_display(spec.anchor)
            x, y = display_anchor[:2]
            label_width = max(30.0, 8.0 * len(spec.text) + 8.0)
            label_height = 22.0
            if x < -label_width or x > width or y < -label_height or y > height:
                continue
            projected.append((spec, display_anchor, label_width, label_height, order))

        forced = [item for item in projected if item[0].force_visible]
        projected = [item for item in projected if not item[0].force_visible]
        projected.sort(key=lambda item: (not item[0].selected, -item[0].arrival_order, item[0].priority, item[4]))
        accepted = [(*item[:4], 0.0) for item in forced]
        grid: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
        spacing = self.LABEL_SPACING
        cell_size = self.LABEL_GRID_SIZE
        largest_accepted_radius = 0.0
        for spec, display_anchor, label_width, label_height, _order in projected:
            if len(accepted) >= self.max_entity_labels:
                break
            x, y = display_anchor[:2]
            radius = max(spacing, label_width / 2.0, label_height / 2.0)
            cell_x = int(x // cell_size)
            cell_y = int(y // cell_size)
            nearby = False
            radius_in_cells = max(1, int((radius + largest_accepted_radius) // cell_size) + 1)
            for neighbor_x in range(cell_x - radius_in_cells, cell_x + radius_in_cells + 1):
                for neighbor_y in range(cell_y - radius_in_cells, cell_y + radius_in_cells + 1):
                    for other_x, other_y, other_radius in grid.get((neighbor_x, neighbor_y), ()):
                        required = radius + other_radius
                        if (x - other_x) ** 2 + (y - other_y) ** 2 < required**2:
                            nearby = True
                            break
                    if nearby:
                        break
                if nearby:
                    break
            if nearby:
                continue
            grid.setdefault((cell_x, cell_y), []).append((x, y, radius))
            largest_accepted_radius = max(largest_accepted_radius, radius)
            fade = 0.0 if spec.selected else self._label_fade(len(accepted))
            accepted.append((spec, display_anchor, label_width, label_height, fade))
        return accepted

    def _label_fade(self, accepted_index: int) -> float:
        """Fade the oldest labels as the visible label budget fills."""

        fade_start = max(1, int(self.max_entity_labels * 0.72))
        fade_end = max(fade_start + 1, self.max_entity_labels - 1)
        return min(0.88, max(0.0, (accepted_index - fade_start) / (fade_end - fade_start)))

    @staticmethod
    def _fade_color(color, fade):
        background = (0.10, 0.12, 0.15)
        return tuple(
            background_component + (component - background_component) * (1.0 - fade)
            for component, background_component in zip(color, background)
        )

    def _label_position(self, anchor, label_width, label_height, occupied, width, height):
        gap = 7.0
        initial = (min(anchor[0] + gap, width - label_width), min(anchor[1] + gap, height - label_height))
        candidates = [initial]
        for radius in range(1, 8):
            distance = radius * (max(label_width, label_height) + gap)
            candidates.extend(
                (
                    anchor[0] + dx * distance,
                    anchor[1] + dy * distance,
                )
                for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1), (0, 1), (0, -1), (1, 0), (-1, 0))
            )
        for x, y in candidates:
            x = max(2.0, min(x, width - label_width - 2.0))
            y = max(2.0, min(y, height - label_height - 2.0))
            box = (x, y, x + label_width, y + label_height)
            if not any(self._boxes_overlap(box, other) for other in occupied):
                return (x, y), box, (x, y) != initial
        x, y = initial
        return (x, y), (x, y, x + label_width, y + label_height), True

    @staticmethod
    def _boxes_overlap(first, second) -> bool:
        return first[0] < second[2] and first[2] > second[0] and first[1] < second[3] and first[3] > second[1]

    def _world_to_display(self, position):
        self.renderer.SetWorldPoint(*position, 1.0)
        self.renderer.WorldToDisplay()
        return self.renderer.GetDisplayPoint()

    def _display_to_world(self, x, y, z):
        self.renderer.SetDisplayPoint(x, y, z)
        self.renderer.DisplayToWorld()
        world = self.renderer.GetWorldPoint()
        divisor = world[3] or 1.0
        return tuple(component / divisor for component in world[:3])

    def _add_leader(self, anchor, label_position, color, opacity=1.0):
        line = vtk.vtkLineSource()
        line.SetPoint1(*anchor)
        line.SetPoint2(*label_position)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(line.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color)
        actor.GetProperty().SetOpacity(opacity)
        actor.GetProperty().SetLineWidth(1.0)
        actor.SetPickable(False)
        self.renderer.AddActor(actor)
        self._register_actor(actor)

    def _center(self, points):
        return tuple(
            sum(getattr(point, axis) for point in points) / len(points)
            for axis in ("x", "y", "z")
        )

    def _add_point(
        self, entity_id, x, y, z, selected, category, labels, label_scale,
        label_text, dimmed=False, highlighted=False, force_label=False,
    ):
        points = vtk.vtkPoints(); points.InsertNextPoint(x, y, z)
        cells = vtk.vtkCellArray(); cells.InsertNextCell(1); cells.InsertCellPoint(0)
        data = vtk.vtkPolyData(); data.SetPoints(points); data.SetVerts(cells)
        mapper = vtk.vtkPolyDataMapper(); mapper.SetInputData(data)
        actor = vtk.vtkActor(); actor.SetMapper(mapper)
        actor.GetProperty().SetPointSize(
            4 if dimmed else (13 if highlighted else (10 if selected else 6))
        )
        color = (
            (0.48, 0.50, 0.53) if dimmed else
            ((0.2, 1.0, 0.72) if highlighted else
             ((1.0, 0.3, 0.2) if selected else color_for_category(category)))
        )
        actor.GetProperty().SetColor(*color)
        if dimmed:
            actor.GetProperty().SetOpacity(self._analysis_context_opacity)
        self.renderer.AddActor(actor); self._register_actor(actor)
        if labels:
            priority = 2 if category == "Topology" else 3
            self._add_label(
                entity_id, (x, y, z), color, label_scale, label_text,
                selected or highlighted, priority, dimmed=dimmed,
                force_visible=force_label,
            )

    def _add_polyline(
        self, entity_id, points, selected, category, labels, label_scale,
        label_text, dimmed=False, highlighted=False,
    ):
        context_edge = dimmed and category == "Geometry"
        vtk_points = vtk.vtkPoints()
        for point in points: vtk_points.InsertNextPoint(point.x, point.y, point.z)
        line = vtk.vtkPolyLine(); line.GetPointIds().SetNumberOfIds(len(points))
        for index in range(len(points)): line.GetPointIds().SetId(index, index)
        cells = vtk.vtkCellArray(); cells.InsertNextCell(line)
        data = vtk.vtkPolyData(); data.SetPoints(vtk_points); data.SetLines(cells)
        mapper = vtk.vtkPolyDataMapper(); mapper.SetInputData(data)
        actor = vtk.vtkActor(); actor.SetMapper(mapper)
        actor.GetProperty().SetLineWidth(
            2.5 if context_edge else
            (1.5 if dimmed else (4.0 if highlighted else (3 if selected else 1.5)))
        )
        color = (
            ((0.68, 0.76, 0.88) if context_edge else (0.48, 0.50, 0.53)) if dimmed else
            ((0.2, 1.0, 0.72) if highlighted else
             ((1.0, 0.3, 0.2) if selected else color_for_category(category)))
        )
        actor.GetProperty().SetColor(*color)
        if dimmed:
            actor.GetProperty().SetOpacity(self._analysis_context_opacity)
        self.renderer.AddActor(actor); self._register_actor(actor)
        if labels:
            self._add_label(
                entity_id, self._center(points), color, label_scale, label_text,
                selected, 1, dimmed=dimmed,
            )

    def _add_control_net(
        self, control_net, selected, labels, label_scale, label_text,
        dimmed=False, highlighted=False, point_labels=False,
    ):
        """Render one B-spline control net as a compact point-and-line actor."""

        points = vtk.vtkPoints()
        for point in control_net.point_markers:
            points.InsertNextPoint(point.x, point.y, point.z)
        markers = vtk.vtkCellArray()
        for index in range(len(control_net.point_markers)):
            markers.InsertNextCell(1)
            markers.InsertCellPoint(index)
        lines = vtk.vtkCellArray()
        for row in range(control_net.row_count):
            line = vtk.vtkPolyLine()
            line.GetPointIds().SetNumberOfIds(control_net.column_count)
            for column in range(control_net.column_count):
                line.GetPointIds().SetId(column, row * control_net.column_count + column)
            lines.InsertNextCell(line)
        for column in range(control_net.column_count):
            line = vtk.vtkPolyLine()
            line.GetPointIds().SetNumberOfIds(control_net.row_count)
            for row in range(control_net.row_count):
                line.GetPointIds().SetId(row, row * control_net.column_count + column)
            lines.InsertNextCell(line)
        data = vtk.vtkPolyData()
        data.SetPoints(points)
        data.SetVerts(markers)
        data.SetLines(lines)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(data)
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetPointSize(10 if (selected or highlighted) else (4 if dimmed else 7))
        actor.GetProperty().SetLineWidth(3 if (selected or highlighted) else 2)
        color = (
            (0.48, 0.50, 0.53) if dimmed else
            ((0.2, 1.0, 0.72) if highlighted else
             ((1.0, 0.3, 0.2) if selected else color_for_category("Control Net")))
        )
        actor.GetProperty().SetColor(*color)
        if dimmed:
            actor.GetProperty().SetOpacity(self._analysis_context_opacity)
        self.renderer.AddActor(actor)
        self._register_actor(actor)
        if labels and not point_labels:
            self._add_label(
                control_net.entity_id,
                self._center(control_net.point_markers),
                color,
                label_scale,
                label_text,
                selected,
                0,
                dimmed=dimmed,
            )
        if point_labels:
            for row_index, row in enumerate(control_net.rows):
                for column_index, point in enumerate(row):
                    point_id = (
                        control_net.point_ids[row_index][column_index]
                        if control_net.point_ids else None
                    )
                    label_id = point_id if point_id is not None else control_net.entity_id
                    text = f"#{point_id}" if point_id is not None else f"P{row_index + 1},{column_index + 1}"
                    self._add_label(
                        label_id,
                        (point.x, point.y, point.z),
                        color,
                        label_scale * 0.6,
                        text,
                        True,
                        0,
                        force_visible=True,
                    )

    def _add_mesh(
        self, mesh, selected, labels, label_scale, label_text,
        dimmed=False, highlighted=False,
    ):
        points = vtk.vtkPoints()
        for point in mesh.points: points.InsertNextPoint(point.x, point.y, point.z)
        triangles = vtk.vtkCellArray()
        for a, b, c in mesh.triangles:
            triangle = vtk.vtkTriangle()
            triangle.GetPointIds().SetId(0, a); triangle.GetPointIds().SetId(1, b); triangle.GetPointIds().SetId(2, c)
            triangles.InsertNextCell(triangle)
        data = vtk.vtkPolyData(); data.SetPoints(points); data.SetPolys(triangles)
        mapper = vtk.vtkPolyDataMapper(); mapper.SetInputData(data)
        actor = vtk.vtkActor(); actor.SetMapper(mapper)
        actor.GetProperty().SetOpacity(
            self._analysis_context_opacity if dimmed else
            (self._analysis_face_opacity if highlighted else 1.0)
        )
        front_color, back_color = self._face_base_colors(mesh.entity_id)
        actor.GetProperty().SetColor(
            (0.48, 0.50, 0.53) if dimmed else
            ((1.0, 0.72, 0.08) if highlighted else front_color)
        )
        orientation_mode = getattr(self, "face_color_mode", "all-the-same") == "normals"
        selected_color = self._brighten_color(front_color, 0.4)
        if highlighted:
            actor.GetProperty().LightingOff()
            if self._analysis_triangle_edges:
                actor.GetProperty().EdgeVisibilityOn()
                actor.GetProperty().SetEdgeColor(1.0, 0.93, 0.35)
                actor.GetProperty().SetLineWidth(2.5)
            else:
                actor.GetProperty().EdgeVisibilityOff()
        elif orientation_mode and not dimmed:
            actor.GetProperty().SetColor(*front_color)
            actor.GetProperty().SetOpacity(1.0)
            actor.GetProperty().LightingOff()
            back = vtk.vtkProperty()
            back.SetColor(*back_color)
            back.SetOpacity(1.0)
            back.LightingOff()
            actor.SetBackfaceProperty(back)
        elif mesh.curved:
            # Shared vertices plus Phong interpolation make sampled analytic
            # surfaces read as smooth rather than as a row of flat facets.
            normals = vtk.vtkPolyDataNormals()
            normals.SetInputData(data)
            normals.SplittingOff()
            normals.ConsistencyOff()
            mapper.SetInputConnection(normals.GetOutputPort())
            actor.GetProperty().SetInterpolationToPhong()
        else:
            # Planar previews are triangulated with a fan. Lighting can give
            # those triangles different normals and create a diagonal split.
            actor.GetProperty().LightingOff()
        if not highlighted and not dimmed:
            if selected and orientation_mode and not self.show_mesh_edges:
                actor.GetProperty().EdgeVisibilityOn()
                actor.GetProperty().SetEdgeColor(*selected_color)
                actor.GetProperty().SetLineWidth(2)
            elif self.show_mesh_edges and not self._last_fast_preview:
                actor.GetProperty().EdgeVisibilityOn()
                edge_color = selected_color if selected else (0.09, 0.13, 0.19)
                actor.GetProperty().SetEdgeColor(*edge_color)
                actor.GetProperty().SetEdgeOpacity(self.mesh_edge_opacity)
                actor.GetProperty().SetLineWidth(1.0)
                back = actor.GetBackfaceProperty()
                if back is not None:
                    back.EdgeVisibilityOn()
                    back.SetEdgeColor(*edge_color)
                    back.SetEdgeOpacity(self.mesh_edge_opacity)
                    back.SetLineWidth(1.0)
                self._mesh_edge_actors.append(actor)
        if selected:
            self._set_selected_face_brightness(actor, mesh.entity_id, 0.4)
            actor.GetProperty().SetOpacity(1.0)
        self.renderer.AddActor(actor); self._register_actor(actor)
        self._face_actors[mesh.entity_id] = actor
        if (highlighted and self._analysis_show_normals) or (
            self._analysis_face_id is None and getattr(self, "normal_display", "Hidden") == "Vectors"
        ):
            self._add_mesh_normals(
                mesh,
                dimmed=dimmed,
                highlighted=highlighted,
                length_scale=self._analysis_normal_scale if highlighted else 1.0,
                max_count=(
                    self.DEFAULT_NORMAL_ARROW_LIMIT if highlighted else
                    self._normal_arrow_quotas.get(mesh.entity_id, self.DEFAULT_NORMAL_ARROW_LIMIT)
                ),
            )
        if labels:
            color = (
                (0.48, 0.50, 0.53) if dimmed else
                ((1.0, 0.93, 0.35) if highlighted else
                 (selected_color if selected else front_color))
            )
            self._add_label(
                mesh.entity_id, self._center(mesh.points), color, label_scale,
                label_text, selected or highlighted, 0, dimmed=dimmed,
            )

    def _mesh_color(self, entity_id: int) -> tuple[float, float, float]:
        """Return the requested stable face color before selection brightening."""

        if getattr(self, "face_color_mode", "all-the-same") != "random":
            return (0.3, 0.7, 1.0)
        # Golden-ratio stepping gives adjacent STEP ids visibly different hues
        # while retaining stable colors through rebuilds and playback.
        return hsv_to_rgb((entity_id * 0.618033988749895) % 1.0, 0.62, 0.95)

    def _add_mesh_normals(
        self, mesh, dimmed=False, highlighted=False, length_scale=1.0,
        max_count=DEFAULT_NORMAL_ARROW_LIMIT,
    ) -> None:
        """Show a small sample of arrows following the preview triangle winding."""

        if max_count <= 0:
            return
        if mesh.curved:
            candidates = []
            for triangle in mesh.triangles:
                p, q, r = (mesh.points[index] for index in triangle)
                edges = (
                    dist((p.x, p.y, p.z), (q.x, q.y, q.z)),
                    dist((q.x, q.y, q.z), (r.x, r.y, r.z)),
                    dist((r.x, r.y, r.z), (p.x, p.y, p.z)),
                )
                # Sliver triangles produce unstable preview directions.
                if min(edges) <= 1e-12 or max(edges) / min(edges) > 100:
                    continue
                candidates.append(triangle)
            count = min(max_count, len(candidates))
            if count == 1:
                samples = [candidates[len(candidates) // 2]]
            elif count:
                samples = [
                    candidates[index * (len(candidates) - 1) // (count - 1)]
                    for index in range(count)
                ]
            else:
                samples = []
        else:
            samples = mesh.triangles[:1]
        positions = vtk.vtkPoints()
        directions = vtk.vtkFloatArray()
        directions.SetNumberOfComponents(3)
        directions.SetName("Normals")
        for a, b, c in samples:
            p, q, r = (mesh.points[index] for index in (a, b, c))
            cross = (
                (q.y - p.y) * (r.z - p.z) - (q.z - p.z) * (r.y - p.y),
                (q.z - p.z) * (r.x - p.x) - (q.x - p.x) * (r.z - p.z),
                (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x),
            )
            magnitude = sum(component * component for component in cross) ** 0.5
            if magnitude <= 1e-12:
                continue
            positions.InsertNextPoint(*((getattr(p, axis) + getattr(q, axis) + getattr(r, axis)) / 3 for axis in ("x", "y", "z")))
            directions.InsertNextTuple3(*(component / magnitude for component in cross))
        if not positions.GetNumberOfPoints():
            return
        data = vtk.vtkPolyData()
        data.SetPoints(positions)
        data.GetPointData().SetNormals(directions)
        arrow = vtk.vtkArrowSource()
        glyph = vtk.vtkGlyph3D()
        glyph.SetInputData(data)
        glyph.SetSourceConnection(arrow.GetOutputPort())
        glyph.SetVectorModeToUseNormal()
        glyph.SetScaleModeToScaleByVector()
        glyph.SetScaleFactor(getattr(self, "_normal_length", 1.0) * length_scale)
        if highlighted:
            self._analysis_normal_glyph = glyph
        glyph.OrientOn()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(glyph.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(
            (0.48, 0.50, 0.53) if dimmed else
            ((1.0, 0.93, 0.35) if highlighted else (1.0, 0.9, 0.2))
        )
        if dimmed:
            actor.GetProperty().SetOpacity(self._analysis_context_opacity)
        actor.GetProperty().LightingOff()
        actor.SetPickable(False)
        self.renderer.AddActor(actor)
        self._register_actor(actor)
