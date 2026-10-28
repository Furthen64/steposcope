"""Kernel-free STEP shell reports and deterministic offscreen rendering."""

from __future__ import annotations

from collections import defaultdict
from colorsys import hsv_to_rgb
import json
import math
import time
from pathlib import Path

from .geometry.builder import GeometryBuilder, Mesh, Point3
from .step.parser import StepDocument
from .step.values import StepAggregate, StepEnumeration, StepReference


CAMERA_DIRECTIONS = {
    "front": (0, -1, 0),
    "top": (0, 0, 1),
    "right": (1, 0, 0),
    "iso": (1, -1, 1),
    # Offset from the scene center toward the camera. The above-left view gives a
    # downward three-quarter shot; the below-left view mirrors it for a worm's-eye
    # look back up at the part. "over" and "under" remain as short aliases.
    "over": (1, -0.5, 1),
    "under": (1, -0.5, -1),
    # Descriptive names used by snapshot outputs; the shorter names above remain
    # accepted for existing commands and configuration files.
    "iso-above-left": (1, -0.5, 1),
    "iso-below-left": (1, -0.5, -1),
}


def _face_color(entity_id: int) -> tuple[float, float, float]:
    """Return a vivid, stable color derived only from a STEP entity id."""

    # SplitMix64 avoids patterns when a writer allocates face ids at a regular
    # stride, while integer arithmetic stays predictable for very large ids.
    mask = (1 << 64) - 1
    hue_bits = (entity_id + 0x9E3779B97F4A7C15) & mask
    hue_bits = ((hue_bits ^ (hue_bits >> 30)) * 0xBF58476D1CE4E5B9) & mask
    hue_bits = ((hue_bits ^ (hue_bits >> 27)) * 0x94D049BB133111EB) & mask
    hue_bits ^= hue_bits >> 31
    return hsv_to_rgb(hue_bits / (1 << 64), 0.78, 0.95)


def _scene_extent(meshes: list[Mesh]) -> float:
    points = [point for mesh in meshes for point in mesh.points]
    if not points:
        return 1.0
    return max(
        max(getattr(point, axis) for point in points) - min(getattr(point, axis) for point in points)
        for axis in ("x", "y", "z")
    )


def _mesh_area(mesh: Mesh) -> float:
    """Total tessellated area, used to decide which faces are worth labelling."""
    total = 0.0
    points = mesh.points
    for triangle in mesh.triangles:
        try:
            p, q, r = (points[index] for index in triangle)
        except IndexError:
            continue
        ux, uy, uz = q.x - p.x, q.y - p.y, q.z - p.z
        vx, vy, vz = r.x - p.x, r.y - p.y, r.z - p.z
        cx = uy * vz - uz * vy
        cy = uz * vx - ux * vz
        cz = ux * vy - uy * vx
        total += 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)
    return total


def _face_label_position(mesh: Mesh, offset: float) -> tuple[float, float, float] | None:
    """Find an area-weighted face centroid just above its tessellated surface."""

    weighted_centroid = [0.0, 0.0, 0.0]
    summed_normal = [0.0, 0.0, 0.0]
    fallback_normal = (0.0, 0.0, 0.0)
    fallback_area = 0.0
    total_area = 0.0
    samples = []
    for triangle in mesh.triangles:
        try:
            p, q, r = (mesh.points[index] for index in triangle)
        except IndexError:
            continue
        normal = (
            (q.y - p.y) * (r.z - p.z) - (q.z - p.z) * (r.y - p.y),
            (q.z - p.z) * (r.x - p.x) - (q.x - p.x) * (r.z - p.z),
            (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x),
        )
        area = math.sqrt(sum(value * value for value in normal))
        if not math.isfinite(area) or area == 0.0:
            continue
        centroid = ((p.x + q.x + r.x) / 3, (p.y + q.y + r.y) / 3, (p.z + q.z + r.z) / 3)
        samples.append((centroid, normal))
        for axis in range(3):
            weighted_centroid[axis] += centroid[axis] * area
            summed_normal[axis] += normal[axis]
        total_area += area
        if area > fallback_area:
            fallback_area = area
            fallback_normal = normal

    if total_area == 0.0:
        return None
    centroid = tuple(value / total_area for value in weighted_centroid)
    if mesh.curved:
        # A curved face's mathematical centroid may lie inside the solid (the
        # center of a cylinder, for example). Anchor to the tessellated point
        # nearest that centroid so the label starts on the actual surface.
        centroid, local_normal = min(
            samples,
            key=lambda sample: sum((sample[0][axis] - centroid[axis]) ** 2 for axis in range(3)),
        )
        summed_normal = list(local_normal)
    normal_length = math.sqrt(sum(value * value for value in summed_normal))
    if normal_length <= total_area * 1e-12:
        summed_normal = list(fallback_normal)
        normal_length = fallback_area
    return tuple(
        centroid[axis] + offset * summed_normal[axis] / normal_length
        for axis in range(3)
    )


def _boxes_overlap(first, second) -> bool:
    return first[0] < second[2] and first[2] > second[0] and first[1] < second[3] and first[3] > second[1]


class _LabelPlacer:
    """Tracks placed label boxes and answers 'does this one hit any of them?'.

    The original answer was a linear scan of every box placed so far, asked once
    per candidate position per label: O(labels^2) with up to 57 candidates each.
    On a 3537-face loft that is a few hundred million box tests, and it dominated
    the whole `faces` render -- 170s of a 178s run, against 0.4s for `normals`.

    Bucketing by cell keeps the answer identical: with the cell at least as large
    as the biggest label, two boxes can only overlap if they share a cell, so the
    lookup only has to consider neighbours. Same placement, same order, no
    different output -- just not quadratic.
    """

    def __init__(self, cell: float = 64.0) -> None:
        self.cell = max(1.0, float(cell))
        self._buckets: dict[tuple[int, int], list[tuple]] = {}

    def _cells(self, box: tuple[float, float, float, float]):
        x0 = int(box[0] // self.cell)
        y0 = int(box[1] // self.cell)
        x1 = int(box[2] // self.cell)
        y1 = int(box[3] // self.cell)
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                yield cx, cy

    def overlaps(self, box: tuple[float, float, float, float]) -> bool:
        for key in self._cells(box):
            for other in self._buckets.get(key, ()):
                if _boxes_overlap(box, other):
                    return True
        return False

    def add(self, box: tuple[float, float, float, float]) -> None:
        for key in self._cells(box):
            self._buckets.setdefault(key, []).append(box)


def _label_position(anchor, label_width, label_height, occupied, width, height):
    """Place a label near its projected anchor without covering another id."""

    centered = (anchor[0] - label_width / 2, anchor[1] - label_height / 2)
    candidates = [centered]
    gap = 4.0
    for radius in range(1, 8):
        distance = radius * (max(label_width, label_height) + gap)
        candidates.extend(
            (centered[0] + dx * distance, centered[1] + dy * distance)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1))
        )
    for x, y in candidates:
        x = max(2.0, min(x, width - label_width - 2.0))
        y = max(2.0, min(y, height - label_height - 2.0))
        box = (x, y, x + label_width, y + label_height)
        if not occupied.overlaps(box):
            return (x, y), box, (x, y) != centered
    x, y = centered
    return (x, y), (x, y, x + label_width, y + label_height), True


def _add_face_labels(vtk, renderer, specs, width: int, height: int, font_size: int) -> None:
    """Add stable, collision-aware face labels after the camera is framed."""

    occupied = _LabelPlacer(cell=max(48.0, font_size * 12.0))
    for entity_id, anchor, color in sorted(specs):
        renderer.SetWorldPoint(*anchor, 1.0)
        renderer.WorldToDisplay()
        display_anchor = renderer.GetDisplayPoint()
        label_width = max(24.0, font_size * (0.65 * len(f"#{entity_id}") + 0.5))
        label_height = font_size * 1.35
        position, box, displaced = _label_position(
            display_anchor, label_width, label_height, occupied, width, height
        )
        occupied.add(box)
        renderer.SetDisplayPoint(position[0], position[1], display_anchor[2])
        renderer.DisplayToWorld()
        world = renderer.GetWorldPoint()
        divisor = world[3] or 1.0
        label_position = tuple(component / divisor for component in world[:3])

        label = vtk.vtkBillboardTextActor3D()
        label.SetInput(f"#{entity_id}")
        label.SetPosition(*label_position)
        label.SetPickable(False)
        label.SetUseBounds(False)
        text = label.GetTextProperty()
        text.SetColor(1.0, 1.0, 1.0)
        text.SetFontSize(font_size)
        text.SetBold(True)
        text.SetBackgroundColor(0.04, 0.05, 0.07)
        text.SetBackgroundOpacity(0.72)
        text.SetFrame(True)
        text.SetFrameColor(*color)
        text.SetFrameWidth(1)
        renderer.AddActor(label)

        if displaced:
            line = vtk.vtkLineSource()
            line.SetPoint1(*anchor)
            line.SetPoint2(*label_position)
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(line.GetOutputPort())
            leader = vtk.vtkActor()
            leader.SetMapper(mapper)
            leader.GetProperty().SetColor(*color)
            leader.GetProperty().SetLineWidth(1.0)
            leader.SetPickable(False)
            leader.SetUseBounds(False)
            renderer.AddActor(leader)


def _face_ids(document: StepDocument, shell) -> list[int]:
    return [
        value.entity_id
        for argument in shell.arguments
        if isinstance(argument, StepAggregate)
        for value in argument.values
        if isinstance(value, StepReference)
    ]


def _contribution(mesh: Mesh) -> float:
    total = 0.0
    for indices in mesh.triangles:
        p, q, r = (mesh.points[index] for index in indices)
        total += (
            p.x * (q.y * r.z - q.z * r.y)
            - p.y * (q.x * r.z - q.z * r.x)
            + p.z * (q.x * r.y - q.y * r.x)
        ) / 6.0
    return total


def _mean_normal(mesh: Mesh) -> list[float]:
    vector = [0.0, 0.0, 0.0]
    for a, b, c in mesh.triangles:
        p, q, r = (mesh.points[index] for index in (a, b, c))
        vector[0] += (q.y - p.y) * (r.z - p.z) - (q.z - p.z) * (r.y - p.y)
        vector[1] += (q.z - p.z) * (r.x - p.x) - (q.x - p.x) * (r.z - p.z)
        vector[2] += (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length > 1e-15 else [0.0, 0.0, 0.0]


def _edge_judgement(meshes: list[Mesh]) -> tuple[int, list[list[int]], set[int]]:
    """Return open-edge count, equally wound triangle pairs, and their faces.

    Meshes own separate point arrays, so vertices are welded by deterministic
    coordinate keys solely for adjacency. Geometry itself is never changed.
    """

    coordinates = [point for mesh in meshes for point in mesh.points]
    if not coordinates:
        return 0, [], set()
    extent = max(
        (max(getattr(point, axis) for point in coordinates) - min(getattr(point, axis) for point in coordinates)
         for axis in ("x", "y", "z")),
        default=1.0,
    )
    tolerance = max(extent * 1e-9, 1e-10)

    def vertex_key(point: Point3) -> tuple[int, int, int]:
        return tuple(round(value / tolerance) for value in (point.x, point.y, point.z))

    # undirected edge -> (directed start/end, global triangle id, face id)
    adjacency: dict[tuple[tuple[int, int, int], tuple[int, int, int]], list[tuple[object, object, int, int]]] = defaultdict(list)
    triangle_id = 0
    for mesh in meshes:
        keys = [vertex_key(point) for point in mesh.points]
        for triangle in mesh.triangles:
            triangle_id += 1
            for start_index, end_index in zip(triangle, (triangle[1], triangle[2], triangle[0])):
                start, end = keys[start_index], keys[end_index]
                edge = (start, end) if start <= end else (end, start)
                adjacency[edge].append((start, end, triangle_id, mesh.entity_id))

    n_boundary_edges = sum(len(incidents) != 2 for incidents in adjacency.values())
    bad_edges: list[list[int]] = []
    self_conflicting_faces: set[int] = set()
    face_constraints: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for incidents in adjacency.values():
        if len(incidents) != 2:
            continue
        first, second = incidents
        same_direction = first[0] == second[0] and first[1] == second[1]
        if same_direction:
            bad_edges.append([first[2], second[2]])
            if first[3] == second[3]:
                self_conflicting_faces.add(first[3])
        if first[3] != second[3]:
            parity = 1 if same_direction else 0
            face_constraints[first[3]].append((second[3], parity))
            face_constraints[second[3]].append((first[3], parity))

    # Solve the relative winding constraints per connected component. Each
    # component may be globally inverted, so choose the assignment requiring
    # the fewest face flips; global signed volume handles an all-face flip.
    bad_faces: set[int] = set(self_conflicting_faces)
    unseen = {mesh.entity_id for mesh in meshes}
    while unseen:
        root = min(unseen)
        assignment = {root: 0}
        stack = [root]
        while stack:
            face_id = stack.pop()
            for neighbor, parity in face_constraints.get(face_id, ()):
                expected = assignment[face_id] ^ parity
                if neighbor not in assignment:
                    assignment[neighbor] = expected
                    stack.append(neighbor)
        unseen.difference_update(assignment)
        ones = {face_id for face_id, value in assignment.items() if value}
        zeros = set(assignment) - ones
        bad_faces.update(ones if len(ones) <= len(zeros) else zeros)
    bad_edges.sort()
    return n_boundary_edges, bad_edges, bad_faces


def build_report(source: Path, document: StepDocument, meshes: list[Mesh]) -> dict:
    mesh_by_id = {mesh.entity_id: mesh for mesh in meshes}
    shells = [entity for entity in document.entities if entity.type_name.upper() in {"CLOSED_SHELL", "OPEN_SHELL"}]
    if not shells:
        shells = [None]
    solids = []
    for shell in shells:
        ids = _face_ids(document, shell) if shell else [entity.entity_id for entity in document.entities if entity.type_name.upper() == "ADVANCED_FACE"]
        shell_meshes = [mesh_by_id[entity_id] for entity_id in ids if entity_id in mesh_by_id]
        contributions = {mesh.entity_id: _contribution(mesh) for mesh in shell_meshes}
        total = sum(contributions.values())
        scale = max((abs(value) for value in contributions.values()), default=1.0)
        eps = max(1e-12, scale * 1e-10)
        n_boundary_edges, bad_edges, bad_faces = _edge_judgement(shell_meshes)
        global_sign_ok = total > eps
        faces = []
        for entity_id in ids:
            mesh = mesh_by_id.get(entity_id)
            entity = document.entity(entity_id)
            contribution = contributions.get(entity_id, 0.0)
            if mesh:
                type_name, sense, applied = mesh.type_name, mesh.sense, mesh.sense_applied
                mean_normal = _mean_normal(mesh)
            else:
                surface_id = next((arg.entity_id for arg in (entity.arguments if entity else ()) if isinstance(arg, StepReference)), None)
                surface = document.entity(surface_id or -1)
                sense_arg = next((arg for arg in reversed(entity.arguments if entity else ()) if isinstance(arg, StepEnumeration)), None)
                type_name = surface.type_name.upper() if surface else "UNKNOWN"
                sense = f".{sense_arg.value.upper()}." if sense_arg else ".T."
                applied, mean_normal = False, [0.0, 0.0, 0.0]
            faces.append({
                "entity_id": entity_id, "type_name": type_name, "sense": sense,
                "sense_applied": applied, "mean_normal": mean_normal,
                "signed_volume_contribution": contribution,
                "ok": entity_id not in bad_faces,
            })
        coverage_ok = all(face["sense_applied"] is True for face in faces)
        if n_boundary_edges:
            verdict = "open"
        elif not coverage_ok:
            verdict = "unreliable"
        elif bad_edges or not global_sign_ok:
            verdict = "inconsistent"
        else:
            verdict = "consistent"
        if verdict != "consistent":
            for face in faces:
                face["ok"] = False if verdict in {"open", "unreliable"} or not global_sign_ok else face["ok"]
        solids.append({
            "shell_entity_id": shell.entity_id if shell else None,
            "faces": faces, "total_signed_volume": total,
            "n_inconsistent": sum(face["entity_id"] in bad_faces for face in faces),
            "n_boundary_edges": n_boundary_edges,
            "bad_edges": bad_edges,
            "verdict": verdict,
        })
    return {"source": str(source), "solids": solids}


def render_png(
    meshes: list[Mesh],
    report: dict,
    output: Path,
    width: int,
    height: int,
camera: str,
    mode: str,
    max_labels: int = 0,
    fit_box: tuple[float, float, float, float, float, float] | None = None,
) -> None:
    """Render one view to PNG.

    `max_labels` caps the `faces`-mode labels, keeping the largest-area faces. Drawing a
    VTK text actor is expensive -- about 10ms each at that glyph size -- so a dense loft
    spends ~35s per view drawing labels that are ~10px tall, mutually overlapping and
    unreadable anyway. 0 (the default) means no cap, i.e. current behaviour.

    `fit_box` overrides the camera framing. By default each render calls
    `ResetCamera()` on its own actors, so every file is scaled to fill the frame --
    which makes a uniform size difference between two files completely invisible.
    Pass the same box for every file in a comparison and the camera becomes a ruler.
    """
    from step_explorer.vtk_lite import vtk

    verdicts = {face["entity_id"]: face["ok"] for solid in report["solids"] for face in solid["faces"]}
    renderer = vtk.vtkRenderer()
    renderer.SetBackground(0.10, 0.12, 0.15)
    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(width, height)
    window.SetMultiSamples(0)
    window.AddRenderer(renderer)
    if fit_box is not None:
        # A visible actor is required: ResetCamera ignores invisible geometry, so
        # hiding this box would silently restore the per-file auto-fit.
        outline = vtk.vtkOutlineSource()
        outline.SetBounds(*fit_box)
        outline_mapper = vtk.vtkPolyDataMapper()
        outline_mapper.SetInputConnection(outline.GetOutputPort())
        outline_actor = vtk.vtkActor()
        outline_actor.SetMapper(outline_mapper)
        outline_actor.GetProperty().SetColor(0.45, 0.50, 0.58)
        outline_actor.GetProperty().SetLineWidth(1.0)
        outline_actor.GetProperty().SetOpacity(0.55)
        renderer.AddActor(outline_actor)
    label_font_size = max(12, min(28, round(min(width, height) / 45)))
    # A billboard lies in the camera plane, not the face plane. It therefore
    # needs more than a numerical epsilon of clearance or an oblique surface
    # will slice through its glyphs even though the anchor itself is visible.
    # Small images devote a larger share of the viewport to the minimum-size
    # font, so their labels need proportionally more clearance.
    offset_factor = max(0.05, 2 * label_font_size / max(min(width, height), 1))
    label_offset = max(_scene_extent(meshes) * offset_factor, 1e-9)
    label_specs = []
    label_areas = []
    for mesh in meshes:
        points = vtk.vtkPoints()
        for point in mesh.points:
            points.InsertNextPoint(point.x, point.y, point.z)
        cells = vtk.vtkCellArray()
        for triangle in mesh.triangles:
            cell = vtk.vtkTriangle()
            for index, point_id in enumerate(triangle):
                cell.GetPointIds().SetId(index, point_id)
            cells.InsertNextCell(cell)
        data = vtk.vtkPolyData()
        data.SetPoints(points)
        data.SetPolys(cells)
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
        if mesh.curved:
            actor.GetProperty().SetInterpolationToPhong()
        if mode == "normals":
            actor.GetProperty().SetColor((0.2, 0.45, 1.0) if verdicts.get(mesh.entity_id, True) else (1.0, 0.15, 0.1))
        elif mode == "faces":
            color = _face_color(mesh.entity_id)
            actor.GetProperty().SetColor(color)
            label_position = _face_label_position(mesh, label_offset)
            if label_position is not None:
                label_specs.append((mesh.entity_id, label_position, color))
                label_areas.append(_mesh_area(mesh))
        else:
            actor.GetProperty().SetColor(0.72, 0.76, 0.82)
        renderer.AddActor(actor)

        # The source meshes are triangulated, so actor edge visibility draws
        # every tessellation diagonal. Keep only boundaries and sharp creases
        # to show the model's construction without obscuring its broad faces.
        feature_edges = vtk.vtkFeatureEdges()
        feature_edges.SetInputData(data)
        feature_edges.BoundaryEdgesOn()
        feature_edges.FeatureEdgesOn()
        feature_edges.NonManifoldEdgesOn()
        feature_edges.ManifoldEdgesOff()
        feature_edges.SetFeatureAngle(35.0)
        edge_mapper = vtk.vtkPolyDataMapper()
        edge_mapper.SetInputConnection(feature_edges.GetOutputPort())
        edge_mapper.ScalarVisibilityOff()
        edge_actor = vtk.vtkActor()
        edge_actor.SetMapper(edge_mapper)
        edge_actor.GetProperty().SetColor(1.0, 1.0, 1.0)
        edge_actor.GetProperty().SetOpacity(0.2)
        edge_actor.GetProperty().SetLineWidth(1.25)
        edge_actor.GetProperty().LightingOff()
        renderer.AddActor(edge_actor)
    renderer.ResetCamera()
    cam = renderer.GetActiveCamera()
    focal = cam.GetFocalPoint()
    distance = cam.GetDistance()
    direction = CAMERA_DIRECTIONS[camera]
    length = math.sqrt(sum(value * value for value in direction))
    cam.SetPosition(*(focal[i] + distance * direction[i] / length for i in range(3)))
    cam.SetViewUp(0, 1, 0 if camera != "front" else 1)
    if camera == "front":
        cam.SetViewUp(0, 0, 1)
    cam.ParallelProjectionOn()
    renderer.ResetCamera()
    renderer.ResetCameraClippingRange()

    # A small orientation triad provides a stable XYZ reference without
    # changing the model's camera framing.
    window.SetNumberOfLayers(2)
    axes_renderer = vtk.vtkRenderer()
    axes_renderer.SetLayer(1)
    axes_renderer.SetViewport(0.025, 0.025, 0.16, 0.16)
    axes_renderer.SetBackgroundAlpha(0.0)
    axes_renderer.SetInteractive(0)
    axes = vtk.vtkAxesActor()
    axes.SetTotalLength(1.0, 1.0, 1.0)
    axes.SetNormalizedShaftLength((0.72, 0.72, 0.72))
    axes.SetNormalizedTipLength((0.28, 0.28, 0.28))
    axes.SetCylinderRadius(0.035)
    axes.SetConeRadius(0.09)
    axes_renderer.AddActor(axes)
    axes_camera = axes_renderer.GetActiveCamera()
    view_direction = tuple(cam.GetFocalPoint()[i] - cam.GetPosition()[i] for i in range(3))
    direction_length = math.sqrt(sum(value * value for value in view_direction)) or 1.0
    axes_camera.SetFocalPoint(0.0, 0.0, 0.0)
    axes_camera.SetPosition(*(value * -3.0 / direction_length for value in view_direction))
    axes_camera.SetViewUp(cam.GetViewUp())
    axes_camera.ParallelProjectionOn()
    axes_camera.SetParallelScale(1.4)
    window.AddRenderer(axes_renderer)

    if max_labels and len(label_specs) > max_labels:
        # Keep the biggest faces: a truncated label set is only useful if the labels
        # that survive are the ones you would actually look for.
        keep = sorted(range(len(label_specs)), key=lambda i: label_areas[i], reverse=True)[:max_labels]
        label_specs = [label_specs[i] for i in sorted(keep)]
    _add_face_labels(vtk, renderer, label_specs, width, height, label_font_size)
    window.Render()
    capture = vtk.vtkWindowToImageFilter()
    capture.SetInput(window)
    capture.SetInputBufferTypeToRGB()
    capture.ReadFrontBufferOff()
    capture.Update()
    writer = vtk.vtkPNGWriter()
    writer.SetFileName(str(output))
    writer.SetInputConnection(capture.GetOutputPort())
    writer.Write()
    window.Finalize()


def process_file(
    source: Path,
    output_base: Path,
    width: int,
    height: int,
    camera: str,
    mode: str,
    views: tuple[str, ...] | None = None,
    progress=None,
    max_labels: int = 0,
    fit_box: tuple[float, float, float, float, float, float] | None = None,
) -> dict:
    # Each of these stages can take minutes on a heavy file, and a silent run is
    # indistinguishable from a hung one -- so say what is happening and how long
    # it took. `progress` is None (silent) unless the caller asks for reporting.
    rep = progress
    live = rep is not None and rep.enabled

    if live:
        rep.stage(f"reading {source.name}")
    t0 = time.time()
    document = StepDocument.from_file(source)
    t1 = time.time()
    if live:
        rep.done(f"read {source.name} ({len(document.entities)} entities)", t1 - t0)

        rep.stage("tessellating")
    t2 = time.time()
    snapshot = GeometryBuilder(document).build(progress=rep)
    t3 = time.time()
    if live:
        rep.done(f"tessellated {len(snapshot.faces)} faces", t3 - t2)

        rep.stage("judging shell")
    report = build_report(source, document, snapshot.faces)
    t4 = time.time()
    if live:
        rep.done("judged", t4 - t3)
        rep.end_item()

    output_base.parent.mkdir(parents=True, exist_ok=True)
    output_base.with_suffix(".json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if views is None:
        views_to_render = [(camera, output_base.with_suffix(".png"))]
    else:
        views_to_render = [
            (view, output_base.with_name(f"{output_base.name}-{view}").with_suffix(".png"))
            for view in views
        ]
    if live:
        cap = f", max {max_labels} labels" if max_labels and mode == "faces" else ""
        rep.stage(f"rendering {len(views_to_render)} view(s) [{mode}{cap}]")
    t5 = time.time()
    for index, (view, png) in enumerate(views_to_render, start=1):
        render_png(snapshot.faces, report, png, width, height, view, mode, max_labels=max_labels, fit_box=fit_box)
        if live:
            rep.item(index, len(views_to_render), f"view {view}")
    if live:
        rep.end_item()
        rep.done(f"rendered [{mode}]", time.time() - t5)
    return report
