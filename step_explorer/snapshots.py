"""Headless build-up snapshots: a time-lapse of geometry appearing.

The interactive viewer animates playback with a live Qt/VTK widget, which goes
laggy on pathological STEP files. This module reproduces that build-up with no
window at all: it tessellates the whole file once, works out the file order at
which each visible element first becomes fully resolvable, and then renders that
accumulation as a sequence of still frames from fixed cameras.

Every frame shares one bounding box and one parallel scale, so a whole run reads
as a single zoomed-in observation rather than a per-frame auto-fit. That constant
framing is what makes the sequence usable as input to a vision model: the only
thing that changes from frame to frame is the geometry itself.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .geometry.builder import GeometrySnapshot, Point3
from .headless import CAMERA_DIRECTIONS, _face_label_position, _mean_normal
from .step.parser import StepDocument
from .ui.colors import color_for_category


DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 960

_VIEW_ALIASES = {"over": "iso-above-left", "under": "iso-below-left"}


def normalize_view(view: str) -> str:
    """Expand older short names to the descriptive snapshot view names."""

    return _VIEW_ALIASES.get(view, view)

# A little slack so the tightest part still fills the frame like an auto-fit would.
_FRAME_MARGIN = 1.12
_ANGLED_VIEWS = {"over", "under", "iso-above-left", "iso-below-left"}


@dataclass(frozen=True)
class Element:
    """One renderable primitive with the file order at which it becomes visible."""

    appearance: int
    entity_id: int
    kind: str
    payload: object


@dataclass(frozen=True)
class SnapshotPlan:
    """A resolved run: the source, output directory, cadence, and framing."""

    source: Path
    out_dir: Path
    interval: int
    views: tuple[str, ...]
    orders: tuple[int, ...]
    total_events: int
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT


def _appearance_orders(document: StepDocument) -> dict[int, int]:
    """Map each entity id to the highest file order in its reference closure.

    `build(through_order)` only yields an element once every entity it depends on is
    inside the prefix, so an element's first visible frame is the maximum order over
    its whole closure. Computing that closure once, memoized, turns each frame into a
    cheap filter instead of a fresh tessellation pass.
    """

    order_of = {entity.entity_id: entity.order for entity in document.entities}
    outgoing = document.outgoing
    memo: dict[int, int] = {}
    on_stack: set[int] = set()

    def resolve(entity_id: int) -> int:
        cached = memo.get(entity_id)
        if cached is not None:
            return cached
        if entity_id in on_stack:  # defensive: STEP writers occasionally loop
            return order_of.get(entity_id, entity_id)
        on_stack.add(entity_id)
        highest = order_of.get(entity_id, entity_id)
        for target in outgoing.get(entity_id, ()):
            if target in order_of:
                highest = max(highest, resolve(target))
        on_stack.discard(entity_id)
        memo[entity_id] = highest
        return highest

    return {entity_id: resolve(entity_id) for entity_id in order_of}


def collect_elements(document: StepDocument, snapshot: GeometrySnapshot) -> list[Element]:
    """Flatten a snapshot into renderable elements stamped with their appearance order."""

    appearance = _appearance_orders(document)
    fallback = {entity.entity_id: entity.order for entity in document.entities}

    def at(entity_id: int) -> int:
        return appearance.get(entity_id, fallback.get(entity_id, entity_id))

    elements: list[Element] = []
    for entity_id, point in snapshot.points.items():
        elements.append(Element(at(entity_id), entity_id, "point", point))
    for entity_id, point in snapshot.vertices.items():
        elements.append(Element(at(entity_id), entity_id, "vertex", point))
    for polyline in snapshot.polylines:
        elements.append(Element(at(polyline.entity_id), polyline.entity_id, "polyline", polyline))
    for control_net in snapshot.control_nets:
        elements.append(Element(at(control_net.entity_id), control_net.entity_id, "control_net", control_net))
    for mesh in snapshot.faces:
        elements.append(Element(at(mesh.entity_id), mesh.entity_id, "face", mesh))
    return elements


# Visual-significance tiers, most important first. A build-up only advances a frame
# when an element of the highest non-empty tier appears, so isolated points/edges that
# are invisible until they get connected do not each earn their own (near-blank) shot.
_SIGNIFICANCE_TIERS = (("face", "control_net"), ("polyline",), ("vertex",), ("point",))


def _significant_kinds(elements: list[Element]) -> set[str]:
    present = {element.kind for element in elements}
    for tier in _SIGNIFICANCE_TIERS:
        if any(kind in present for kind in tier):
            return set(tier)
    return set()


def geometry_event_orders(elements: list[Element]) -> tuple[int, ...]:
    """Return the distinct file orders at which significant visible geometry changes."""

    kinds = _significant_kinds(elements)
    return tuple(sorted({element.appearance for element in elements if element.kind in kinds}))


def plan_orders(events: tuple[int, ...], interval: int, max_snapshots: int) -> tuple[int, ...]:
    """Choose the through-orders to render, always keeping the final one.

    `interval` counts geometry events, not raw STEP records, so metadata-only
    entities never advance a frame. `max_snapshots` widens the stride instead of
    silently producing tens of thousands of images on a huge file.
    """

    if max_snapshots < 0:
        raise ValueError("max_snapshots must be zero or positive")
    if not events:
        return ()
    if interval <= 0:
        interval = 1
    start = min(interval - 1, len(events) - 1)
    chosen = list(events[start :: interval])
    if chosen[-1] != events[-1]:
        chosen.append(events[-1])
    if max_snapshots and len(chosen) > max_snapshots:
        if max_snapshots == 1:
            return (events[-1],)
        stride = math.ceil((len(chosen) - 1) / (max_snapshots - 1))
        chosen = chosen[::stride]
        if chosen[-1] != events[-1]:
            chosen.append(events[-1])
    return tuple(chosen)


def _element_points(element: Element) -> list[Point3]:
    payload = element.payload
    if element.kind in ("point", "vertex"):
        return [payload]
    if element.kind == "polyline":
        return list(payload.points)
    if element.kind == "control_net":
        return list(payload.point_markers)
    if element.kind == "face":
        return list(payload.points)
    return []


def bounding_box(elements: list[Element]) -> tuple[float, float, float, float, float, float] | None:
    # Frame on the tessellated solid (edges/cages/faces). Bare CARTESIAN_POINTs are
    # dropped because many are surface/placement origins that sit far off the finite
    # part and would otherwise dwarf it; fall back to them only for a bare point cloud.
    solid = [point for element in elements if element.kind != "point" for point in _element_points(element)]
    points = solid or [element.payload for element in elements if element.kind == "point"]
    if not points:
        return None
    return (
        min(point.x for point in points),
        min(point.y for point in points),
        min(point.z for point in points),
        max(point.x for point in points),
        max(point.y for point in points),
        max(point.z for point in points),
    )


def parallel_scale(box) -> float:
    """Half the longest diagonal (with margin), identical for every view and frame."""

    dx, dy, dz = box[3] - box[0], box[4] - box[1], box[5] - box[2]
    return (0.5 * math.sqrt(dx * dx + dy * dy + dz * dz) * _FRAME_MARGIN) or 1.0


def _scene_extent(points: list[Point3]) -> float:
    if not points:
        return 1.0
    return max(
        max(getattr(point, axis) for point in points) - min(getattr(point, axis) for point in points)
        for axis in ("x", "y", "z")
    )


def camera_params(box, camera: str) -> dict:
    """Pure camera framing from the shared box, so zoom is identical across views/frames.

    Only the direction and view-up differ per camera; the focal point and parallel
    scale come straight from the one bounding box, which is exactly what keeps the
    whole sequence at a single zoom level.
    """

    center = (
        (box[0] + box[3]) / 2.0,
        (box[1] + box[4]) / 2.0,
        (box[2] + box[5]) / 2.0,
    )
    scale = parallel_scale(box)
    direction = CAMERA_DIRECTIONS.get(camera, CAMERA_DIRECTIONS["iso"])
    length = math.sqrt(sum(component * component for component in direction))
    distance = scale * 4.0 + 1.0
    position = tuple(center[i] + distance * direction[i] / length for i in range(3))
    # World +Z reads as "up" for the front-facing and the angled shots;
    # the near-top-down and side views keep +Y as up. "top" cannot use +Z (it is the
    # view axis), so it stays on +Y.
    view_up = (0, 0, 1) if camera == "front" or camera in _ANGLED_VIEWS else (0, 1, 0)
    return {
        "focal_point": center,
        "position": position,
        "view_up": view_up,
        "parallel_scale": scale,
        "clipping_range": (max(distance - scale * 3.0, 1e-6), distance + scale * 3.0),
    }


def _set_camera(renderer, box, camera: str) -> None:
    params = camera_params(box, camera)
    cam = renderer.GetActiveCamera()
    cam.SetFocalPoint(*params["focal_point"])
    cam.SetPosition(*params["position"])
    cam.SetViewUp(*params["view_up"])
    cam.SetParallelProjection(True)
    cam.SetParallelScale(params["parallel_scale"])
    cam.SetClippingRange(*params["clipping_range"])


def _point_actor(vtk, x, y, z, color, size):
    points = vtk.vtkPoints()
    points.InsertNextPoint(x, y, z)
    cells = vtk.vtkCellArray()
    cells.InsertNextCell(1)
    cells.InsertCellPoint(0)
    data = vtk.vtkPolyData()
    data.SetPoints(points)
    data.SetVerts(cells)
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(data)
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(*color)
    actor.GetProperty().SetPointSize(size)
    actor.GetProperty().SetRepresentationToSurface()
    return actor


def _polyline_actor(vtk, polyline):
    points = vtk.vtkPoints()
    for point in polyline.points:
        points.InsertNextPoint(point.x, point.y, point.z)
    line = vtk.vtkPolyLine()
    line.GetPointIds().SetNumberOfIds(len(polyline.points))
    for index in range(len(polyline.points)):
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
    category = "Geometry" if polyline.category == "edge" else "Structure"
    actor.GetProperty().SetColor(*color_for_category(category))
    actor.GetProperty().SetLineWidth(1.5)
    return actor


def _control_net_actor(vtk, control_net):
    markers = control_net.point_markers
    points = vtk.vtkPoints()
    for point in markers:
        points.InsertNextPoint(point.x, point.y, point.z)
    verts = vtk.vtkCellArray()
    for index in range(len(markers)):
        verts.InsertNextCell(1)
        verts.InsertCellPoint(index)
    lines = vtk.vtkCellArray()
    rows, columns = control_net.row_count, control_net.column_count
    for row in range(rows):
        line = vtk.vtkPolyLine()
        line.GetPointIds().SetNumberOfIds(columns)
        for column in range(columns):
            line.GetPointIds().SetId(column, row * columns + column)
        lines.InsertNextCell(line)
    for column in range(columns):
        line = vtk.vtkPolyLine()
        line.GetPointIds().SetNumberOfIds(rows)
        for row in range(rows):
            line.GetPointIds().SetId(row, row * columns + column)
        lines.InsertNextCell(line)
    data = vtk.vtkPolyData()
    data.SetPoints(points)
    data.SetVerts(verts)
    data.SetLines(lines)
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(data)
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(*color_for_category("Control Net"))
    actor.GetProperty().SetPointSize(5)
    actor.GetProperty().SetLineWidth(1.0)
    return actor


def _mesh_actor(vtk, mesh):
    points = vtk.vtkPoints()
    for point in mesh.points:
        points.InsertNextPoint(point.x, point.y, point.z)
    triangles = vtk.vtkCellArray()
    for a, b, c in mesh.triangles:
        triangle = vtk.vtkTriangle()
        triangle.GetPointIds().SetId(0, a)
        triangle.GetPointIds().SetId(1, b)
        triangle.GetPointIds().SetId(2, c)
        triangles.InsertNextCell(triangle)
    data = vtk.vtkPolyData()
    data.SetPoints(points)
    data.SetPolys(triangles)
    mapper = vtk.vtkPolyDataMapper()
    if mesh.curved:
        normals = vtk.vtkPolyDataNormals()
        normals.SetInputData(data)
        normals.SplittingOff()
        normals.ConsistencyOff()
        mapper.SetInputConnection(normals.GetOutputPort())
    else:
        mapper.SetInputData(data)
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(0.72, 0.76, 0.82)
    if mesh.curved:
        actor.GetProperty().SetInterpolationToPhong()
    else:
        actor.GetProperty().LightingOff()
    return actor


def _centroid(mesh):
    count = len(mesh.points) or 1
    return (
        sum(point.x for point in mesh.points) / count,
        sum(point.y for point in mesh.points) / count,
        sum(point.z for point in mesh.points) / count,
    )


def _arrow_actor(vtk, faces, arrow_length):
    positions = vtk.vtkPoints()
    directions = vtk.vtkFloatArray()
    directions.SetNumberOfComponents(3)
    directions.SetName("Normals")
    for mesh in faces:
        if not mesh.triangles:
            continue
        anchor = _face_label_position(mesh, 0.0) or _centroid(mesh)
        normal = _mean_normal(mesh)
        if not any(abs(component) > 1e-9 for component in normal):
            continue
        positions.InsertNextPoint(*anchor)
        directions.InsertNextTuple3(*normal)
    if not positions.GetNumberOfPoints():
        return None
    data = vtk.vtkPolyData()
    data.SetPoints(positions)
    data.GetPointData().SetNormals(directions)
    arrow = vtk.vtkArrowSource()
    glyph = vtk.vtkGlyph3D()
    glyph.SetInputData(data)
    glyph.SetSourceConnection(arrow.GetOutputPort())
    glyph.SetVectorModeToUseNormal()
    glyph.SetScaleModeToScaleByVector()
    glyph.SetScaleFactor(arrow_length)
    glyph.OrientOn()
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(glyph.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(1.0, 0.85, 0.15)
    actor.GetProperty().LightingOff()
    actor.SetPickable(False)
    return actor


def _caption_actor(vtk, text, width, height):
    actor = vtk.vtkTextActor()
    actor.SetInput(text)
    actor.GetPositionCoordinate().SetCoordinateSystemToDisplay()
    actor.SetPosition(10, max(height - 10, 10))
    prop = actor.GetTextProperty()
    prop.SetVerticalJustificationToTop()
    prop.SetColor(1.0, 1.0, 1.0)
    prop.SetFontSize(max(12, round(min(width, height) / 55)))
    prop.SetFontFamilyToArial()
    prop.SetBackgroundColor(0.04, 0.05, 0.07)
    prop.SetBackgroundOpacity(0.6)
    prop.SetFrame(True)
    prop.SetFrameColor(0.2, 0.22, 0.26)
    return actor


def _resize_area(array, target_h: int, target_w: int):
    """Area-average an (H, W, 3) uint8 array to (target_h, target_w, 3) via an integral image."""

    import numpy as np

    height, width, _ = array.shape
    if height == target_h and width == target_w:
        return array
    integral = np.zeros((height + 1, width + 1, 3), dtype="float64")
    integral[1:, 1:] = np.cumsum(np.cumsum(array.astype("float64"), axis=0), axis=1)
    rows = np.linspace(0, height, target_h + 1).round().astype(int)
    cols = np.linspace(0, width, target_w + 1).round().astype(int)
    ry0, ry1 = rows[:-1][:, None], rows[1:][:, None]
    cx0, cx1 = cols[:-1][None, :], cols[1:][None, :]
    block = integral[ry1, cx1] - integral[ry0, cx1] - integral[ry1, cx0] + integral[ry0, cx0]
    area = ((rows[1:] - rows[:-1])[:, None] * (cols[1:] - cols[:-1])[None, :])[:, :, None].astype("float64")
    return (block / np.maximum(area, 1.0)).clip(0, 255).round().astype("uint8")


def _center_crop(array, size: int):
    """Crop to the non-black content and re-center it on a (size, size, 3) black canvas."""

    import numpy as np

    mask = array.max(axis=2) > 24
    if not mask.any():
        return np.zeros((size, size, 3), dtype="uint8")
    ys, xs = np.where(mask)
    crop = array[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    ch, cw = crop.shape[:2]
    scale = min(size / ch, size / cw) * 0.92  # small margin so labels are never edge-clipped
    new_h, new_w = max(1, round(ch * scale)), max(1, round(cw * scale))
    resized = _resize_area(crop, new_h, new_w)
    canvas = np.zeros((size, size, 3), dtype="uint8")
    oy, ox = (size - new_h) // 2, (size - new_w) // 2
    canvas[oy : oy + new_h, ox : ox + new_w] = resized
    return canvas


def _axes_gizmo_array(orientation: dict, size: int):
    """Render a small world-axis triad on black, oriented like `orientation`.

    Returns an (size, size, 3) uint8 array; pure-black pixels are treated as
    transparent by the caller so it can be composited over any frame.
    """

    import numpy as np
    from step_explorer.vtk_lite import vtk
    from vtkmodules.util.numpy_support import vtk_to_numpy

    # Render larger than needed so the crop-and-resize below stays crisp; content is re-centered later.
    internal = int(max(240, min(size * 2, 512)))
    renderer = vtk.vtkRenderer()
    renderer.SetBackground(0.0, 0.0, 0.0)
    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(internal, internal)
    window.AddRenderer(renderer)

    # Default vtkAxesActor already draws red/green/blue X/Y/Z arrows; only tune proportions.
    axes = vtk.vtkAxesActor()
    axes.SetTotalLength(1.0, 1.0, 1.0)
    axes.SetNormalizedShaftLength((0.72, 0.72, 0.72))
    axes.SetNormalizedTipLength((0.28, 0.28, 0.28))
    axes.SetCylinderRadius(0.035)
    axes.SetConeRadius(0.09)
    renderer.AddActor(axes)

    # Mirror the main camera's orientation so the triad reads as world space from this view:
    # stand on the far side of the origin looking back along the same direction and up vector.
    position = np.asarray(orientation["position"], dtype=float)
    focal = np.asarray(orientation["focal_point"], dtype=float)
    view_dir = focal - position
    length = float(np.linalg.norm(view_dir)) or 1.0
    view_dir = view_dir / length
    cam = renderer.GetActiveCamera()
    cam.SetFocalPoint(0.0, 0.0, 0.0)
    cam.SetPosition(*tuple(-view_dir * 3.0))
    cam.SetViewUp(*tuple(orientation["view_up"]))
    cam.SetParallelProjection(True)
    cam.SetParallelScale(1.4)

    window.Render()
    capture = vtk.vtkWindowToImageFilter()
    capture.SetInput(window)
    capture.SetInputBufferTypeToRGB()
    capture.ReadFrontBufferOff()
    capture.Update()
    data = capture.GetOutput()
    width, height, _ = data.GetDimensions()
    array = vtk_to_numpy(data.GetPointData().GetScalars()).reshape(height, width, 3).astype("uint8")
    window.Finalize()
    return _center_crop(array, size)


def _write_png_from_array(array, path: Path) -> None:
    """Write an (H, W, 3) uint8 array to a PNG through VTK (no image library needed)."""

    import numpy as np
    from step_explorer.vtk_lite import vtk
    from vtkmodules.util.numpy_support import numpy_to_vtk

    array = np.ascontiguousarray(np.asarray(array, dtype="uint8"))
    height, width, _ = array.shape
    image = vtk.vtkImageData()
    image.SetDimensions(width, height, 1)
    # Pass a 2-D (N, 3) buffer so VTK stores three components per pixel; a flat buffer is
    # misread as one component and garbles the RGB triplets.
    image.GetPointData().SetScalars(numpy_to_vtk(array.reshape(-1, 3), deep=1, array_type=vtk.VTK_UNSIGNED_CHAR))
    writer = vtk.vtkPNGWriter()
    writer.SetFileName(str(path))
    writer.SetInputData(image)
    writer.Write()


def render_frame(
    snapshot: GeometrySnapshot,
    box,
    camera: str,
    output: Path,
    width: int,
    height: int,
    caption: str = "",
    show_arrows: bool = True,
    show_points: bool = False,
    show_axes: bool = True,
) -> None:
    """Render one build-up frame to a PNG at a fixed camera framing."""

    from step_explorer.vtk_lite import vtk

    renderer = vtk.vtkRenderer()
    renderer.SetBackground(0.10, 0.12, 0.15)
    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(width, height)
    window.SetMultiSamples(4)
    window.AddRenderer(renderer)

    extent = _scene_extent([point for mesh in snapshot.faces for point in mesh.points])
    arrow_length = extent * 0.05

    if show_points:
        for point in snapshot.points.values():
            renderer.AddActor(_point_actor(vtk, point.x, point.y, point.z, color_for_category("Geometry"), 5))
    for point in snapshot.vertices.values():
        renderer.AddActor(_point_actor(vtk, point.x, point.y, point.z, color_for_category("Topology"), 7))
    for polyline in snapshot.polylines:
        renderer.AddActor(_polyline_actor(vtk, polyline))
    for control_net in snapshot.control_nets:
        renderer.AddActor(_control_net_actor(vtk, control_net))
    for mesh in snapshot.faces:
        renderer.AddActor(_mesh_actor(vtk, mesh))
    if show_arrows:
        actor = _arrow_actor(vtk, snapshot.faces, arrow_length)
        if actor is not None:
            renderer.AddActor(actor)

    _set_camera(renderer, box, camera)
    if caption:
        renderer.AddActor(_caption_actor(vtk, caption, width, height))
    active = renderer.GetActiveCamera()
    axes_orientation = {
        "position": tuple(active.GetPosition()),
        "focal_point": tuple(active.GetFocalPoint()),
        "view_up": tuple(active.GetViewUp()),
    }
    window.Render()

    from vtkmodules.util.numpy_support import vtk_to_numpy

    capture = vtk.vtkWindowToImageFilter()
    capture.SetInput(window)
    capture.SetInputBufferTypeToRGB()
    capture.ReadFrontBufferOff()
    capture.Update()
    data = capture.GetOutput()
    frame = vtk_to_numpy(data.GetPointData().GetScalars()).reshape(height, width, 3).astype("uint8")
    if show_axes:
        size = int(min(max(40, round(min(width, height) * 0.26)), width, height))
        gizmo = _axes_gizmo_array(axes_orientation, size)
        cy = (height - size) // 2
        cx = (width - size) // 2
        alpha = (gizmo.max(axis=2) / 255.0)[..., None]
        region = frame[cy : cy + size, cx : cx + size].astype("float32")
        blended = region * (1.0 - alpha) + gizmo.astype("float32") * alpha
        frame[cy : cy + size, cx : cx + size] = blended.astype("uint8")
    _write_png_from_array(frame, output)
    window.Finalize()


def _bucket(kind: str) -> str:
    return {
        "point": "points",
        "vertex": "vertices",
        "polyline": "polylines",
        "control_net": "control_nets",
        "face": "faces",
    }[kind]


def _subset_snapshot(elements: list[Element]) -> GeometrySnapshot:
    snapshot = GeometrySnapshot()
    for element in elements:
        if element.kind == "point":
            snapshot.points[element.entity_id] = element.payload
        elif element.kind == "vertex":
            snapshot.vertices[element.entity_id] = element.payload
        elif element.kind == "polyline":
            snapshot.polylines.append(element.payload)
        elif element.kind == "control_net":
            snapshot.control_nets.append(element.payload)
        elif element.kind == "face":
            snapshot.faces.append(element.payload)
    return snapshot


def _frame_caption(plan: SnapshotPlan, index: int, total: int, order: int, view: str, counts: dict) -> str:
    return (
        f"{plan.source.name}   view={normalize_view(view)}\n"
        f"frame {index}/{total}   entity order #{order}\n"
        f"faces={counts['faces']}  edges={counts['polylines']}  vertices={counts['vertices']}"
    )


def run(
    plan: SnapshotPlan,
    elements: list[Element],
    box,
    progress=None,
    show_arrows: bool = True,
    show_points: bool = False,
    caption: bool = True,
    show_axes: bool = True,
) -> dict:
    """Render every planned frame and view, then write a manifest. Returns the manifest."""

    if box is None or not plan.orders:
        raise ValueError("no renderable geometry to snapshot")

    ordered = sorted(elements, key=lambda element: element.appearance)
    # Precompute each frame's element boundary by count so filtering is a slice, not a scan.
    frames = []
    total = len(plan.orders)
    for index, order in enumerate(plan.orders, start=1):
        subset = [element for element in ordered if element.appearance <= order]
        counts = {name: 0 for name in ("points", "vertices", "polylines", "faces", "control_nets")}
        for element in subset:
            counts[_bucket(element.kind)] += 1
        snapshot = _subset_snapshot(subset)
        view_files = []
        for view in dict.fromkeys(normalize_view(view) for view in plan.views):
            name = f"frame{index:04d}-{view}.png"
            if progress is not None and progress.enabled:
                progress.item(index, total, f"frame order {order}")
            render_frame(
                snapshot,
                box,
                view,
                plan.out_dir / name,
                width=plan.width,
                height=plan.height,
                caption=_frame_caption(plan, index, total, order, view, counts) if caption else "",
                show_arrows=show_arrows,
                show_points=show_points,
                show_axes=show_axes,
            )
            view_files.append(name)
        frames.append({"index": index, "order": order, "counts": counts, "views": view_files})

    manifest = {
        "source": str(plan.source),
        "out_dir": str(plan.out_dir),
        "interval": plan.interval,
        "views": list(dict.fromkeys(normalize_view(view) for view in plan.views)),
        "width": plan.width,
        "height": plan.height,
        "total_geometry_events": plan.total_events,
        "frame_count": len(frames),
        "bounding_box": list(box),
        "arrows": show_arrows,
        "center_axes": show_axes,
        "frames": frames,
    }
    (plan.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if progress is not None and progress.enabled:
        progress.end_item()
    return manifest


def timestamp_dir(out_root: Path, when: datetime | None = None) -> Path:
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return out_root / stamp
