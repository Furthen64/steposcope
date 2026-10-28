"""Understand a useful, deliberately small subset of STEP geometry/topology."""

from dataclasses import dataclass, field, replace
from itertools import permutations
import math
from time import perf_counter
from typing import Literal

from step_explorer.vtk_lite import vtk

from ..step.parser import StepDocument, StepEntity
from ..step.values import StepAggregate, StepComplexValue, StepEnumeration, StepNumber, StepReference, StepValue


@dataclass(frozen=True)
class Point3:
    x: float
    y: float
    z: float


class _RationalSplineSurface:
    """Small validated tensor-product NURBS evaluator for trimmed STEP faces."""

    def __init__(self, controls, weights, degrees, knots, *, self_intersect=False):
        self.controls = controls
        self.weights = weights
        self.degrees = degrees
        self.knots = knots
        self.self_intersect = self_intersect
        self.used_trim_interpolation = False
        self.u_range = (knots[0][degrees[0]], knots[0][len(controls)])
        self.v_range = (knots[1][degrees[1]], knots[1][len(controls[0])])
        self._projection_grid = None
        self.periodic = [False, False]
        scale = max(
            math.dist((point.x, point.y, point.z),
                      (controls[0][0].x, controls[0][0].y, controls[0][0].z))
            for row in controls for point in row
        )
        for axis, domain in enumerate((self.u_range, self.v_range)):
            other = self.v_range if axis == 0 else self.u_range
            middle = (other[0] + other[1]) / 2
            first = self.evaluate(domain[0], middle) if axis == 0 else self.evaluate(middle, domain[0])
            last = self.evaluate(domain[1], middle) if axis == 0 else self.evaluate(middle, domain[1])
            if first is not None and last is not None:
                self.periodic[axis] = math.dist(
                    (first.x, first.y, first.z), (last.x, last.y, last.z)
                ) <= max(scale * 1e-7, 1e-8)

    @staticmethod
    def _basis(parameter, degree, knots, count):
        if parameter >= knots[count]:
            return [0.0] * (count - 1) + [1.0]
        values = [1.0 if knots[i] <= parameter < knots[i + 1] else 0.0
                  for i in range(count + degree)]
        for level in range(1, degree + 1):
            values = [
                ((parameter - knots[i]) * values[i] / (knots[i + level] - knots[i])
                 if knots[i + level] > knots[i] else 0.0)
                + ((knots[i + level + 1] - parameter) * values[i + 1]
                   / (knots[i + level + 1] - knots[i + 1])
                   if knots[i + level + 1] > knots[i + 1] else 0.0)
                for i in range(count + degree - level)
            ]
        return values[:count]

    def evaluate(self, u, v):
        if self.periodic[0] and not self.u_range[0] <= u <= self.u_range[1]:
            u = self.u_range[0] + (u - self.u_range[0]) % (self.u_range[1] - self.u_range[0])
        if self.periodic[1] and not self.v_range[0] <= v <= self.v_range[1]:
            v = self.v_range[0] + (v - self.v_range[0]) % (self.v_range[1] - self.v_range[0])
        u = max(self.u_range[0], min(self.u_range[1], u))
        v = max(self.v_range[0], min(self.v_range[1], v))
        ub = self._basis(u, self.degrees[0], self.knots[0], len(self.controls))
        vb = self._basis(v, self.degrees[1], self.knots[1], len(self.controls[0]))
        xyz = [0.0, 0.0, 0.0]
        denominator = 0.0
        for i, u_value in enumerate(ub):
            if u_value == 0:
                continue
            for j, v_value in enumerate(vb):
                if v_value == 0:
                    continue
                weight = u_value * v_value * self.weights[i][j]
                point = self.controls[i][j]
                denominator += weight
                xyz[0] += weight * point.x
                xyz[1] += weight * point.y
                xyz[2] += weight * point.z
        if denominator <= 0:
            return None
        return Point3(*(value / denominator for value in xyz))

    def project(self, point, seed=None):
        """Find a boundary point's UV coordinates by coarse search and Newton steps."""
        u0, u1 = self.u_range
        v0, v1 = self.v_range
        u_span, v_span = u1 - u0, v1 - v0
        target = (point.x, point.y, point.z)

        def error(u, v):
            candidate = self.evaluate(u, v)
            if candidate is None:
                return math.inf
            return sum((getattr(candidate, axis) - target[i]) ** 2
                       for i, axis in enumerate(("x", "y", "z")))

        if self._projection_grid is None:
            grid = []
            n = max(25, len(self.controls[0]) * 2)
            for i in range(9):
                u = u0 + u_span * i / 8
                for j in range(n):
                    v = v0 + v_span * j / (n - 1)
                    grid.append((u, v, self.evaluate(u, v)))
            self._projection_grid = grid
        candidates = []
        for u, v, candidate in self._projection_grid:
            if candidate is None:
                continue
            distance = sum((getattr(candidate, axis) - target[i]) ** 2
                           for i, axis in enumerate(("x", "y", "z")))
            candidates.append((distance, u, v))
        candidates.sort(key=lambda item: item[0])
        starts = [(u, v) for _, u, v in candidates[:3]]
        if seed is not None:
            starts.insert(0, seed)
        best = (math.inf, u0, v0)
        for u, v in starts:
            for _ in range(18):
                base = self.evaluate(u, v)
                if base is None:
                    break
                hu = max(u_span * 1e-5, 1e-8)
                hv = max(v_span * 1e-5, 1e-10)
                low_u, high_u = max(u0, u - hu), min(u1, u + hu)
                low_v, high_v = max(v0, v - hv), min(v1, v + hv)
                pu0, pu1 = self.evaluate(low_u, v), self.evaluate(high_u, v)
                pv0, pv1 = self.evaluate(u, low_v), self.evaluate(u, high_v)
                if None in (pu0, pu1, pv0, pv1):
                    break
                du = tuple((getattr(pu1, axis) - getattr(pu0, axis)) / (high_u - low_u)
                           for axis in ("x", "y", "z"))
                dv = tuple((getattr(pv1, axis) - getattr(pv0, axis)) / (high_v - low_v)
                           for axis in ("x", "y", "z"))
                residual = tuple(target[i] - getattr(base, axis)
                                 for i, axis in enumerate(("x", "y", "z")))
                a = sum(value * value for value in du)
                b = sum(x * y for x, y in zip(du, dv))
                c = sum(value * value for value in dv)
                det = a * c - b * b
                if det <= 1e-22:
                    break
                ru = sum(x * y for x, y in zip(du, residual))
                rv = sum(x * y for x, y in zip(dv, residual))
                step_u = max(-u_span / 4, min(u_span / 4, (ru * c - rv * b) / det))
                step_v = max(-v_span / 4, min(v_span / 4, (rv * a - ru * b) / det))
                previous = error(u, v)
                moved = False
                for divisor in (1, 2, 4, 8, 16):
                    next_u = max(u0, min(u1, u + step_u / divisor))
                    next_v = max(v0, min(v1, v + step_v / divisor))
                    if error(next_u, next_v) < previous - 1e-16:
                        u, v = next_u, next_v
                        moved = True
                        break
                if not moved:
                    break
            candidate = (error(u, v), u, v)
            if candidate[0] < best[0]:
                best = candidate
        return best


@dataclass(frozen=True)
class Polyline:
    entity_id: int
    points: tuple[Point3, ...]
    category: str = "edge"


@dataclass(frozen=True)
class Mesh:
    entity_id: int
    points: tuple[Point3, ...]
    triangles: tuple[tuple[int, int, int], ...]
    curved: bool = False
    type_name: str = "UNKNOWN"
    sense: str = ".T."
    sense_applied: bool | str = False
    orientation_warning: str | None = None
    preview_note: str | None = None


@dataclass(frozen=True)
class ControlNet:
    """A rectangular B-spline control-point grid, independent of rendering.

    Rows preserve the first aggregate dimension in the STEP surface entity;
    columns preserve its second aggregate dimension.  The points describe a
    control cage, rather than samples guaranteed to lie on the surface.
    """

    entity_id: int
    rows: tuple[tuple[Point3, ...], ...]
    point_ids: tuple[tuple[int, ...], ...] = ()

    def __post_init__(self) -> None:
        if len(self.rows) < 2:
            raise ValueError("a control net needs at least two rows")
        column_count = len(self.rows[0])
        if column_count < 2:
            raise ValueError("a control net needs at least two columns")
        if any(len(row) != column_count for row in self.rows):
            raise ValueError("a control net must be rectangular")
        if self.point_ids and (
            len(self.point_ids) != len(self.rows)
            or any(len(row) != column_count for row in self.point_ids)
        ):
            raise ValueError("control-point ids must match the control grid")

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return len(self.rows[0])

    @property
    def point_markers(self) -> tuple[Point3, ...]:
        """Return every control point in STEP aggregate order."""

        return tuple(point for row in self.rows for point in row)

    def at_density(self, density: int) -> "ControlNet":
        """Return an evenly distributed coarse-to-full view of this control net.

        A density of zero retains the four corner controls; 100 retains every
        point. Intermediate levels select rows and columns across the full
        control cage, rather than taking a local prefix of the grid.
        """

        if isinstance(density, bool) or not isinstance(density, int):
            raise ValueError("control-net density must be an integer")
        density = max(0, min(100, density))
        row_indices = self._evenly_spaced_indices(self.row_count, density)
        column_indices = self._evenly_spaced_indices(self.column_count, density)
        return ControlNet(
            self.entity_id,
            tuple(tuple(self.rows[row][column] for column in column_indices) for row in row_indices),
            tuple(
                tuple(self.point_ids[row][column] for column in column_indices)
                for row in row_indices
            ) if self.point_ids else (),
        )

    @staticmethod
    def _evenly_spaced_indices(count: int, density: int) -> tuple[int, ...]:
        selected_count = 2 + round((count - 2) * density / 100)
        if selected_count == count:
            return tuple(range(count))
        return tuple(round(index * (count - 1) / (selected_count - 1)) for index in range(selected_count))


@dataclass(frozen=True)
class ControlNetStatus:
    """Playback readiness for a B-spline surface's control net."""

    entity_id: int
    state: Literal["waiting", "ready", "invalid"]
    missing_point_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in {"waiting", "ready", "invalid"}:
            raise ValueError(f"unsupported control-net state: {self.state}")
        if self.state == "waiting" and not self.missing_point_ids:
            raise ValueError("a waiting control net must identify missing points")
        if self.state != "waiting" and self.missing_point_ids:
            raise ValueError("only a waiting control net can have missing points")

    @property
    def description(self) -> str:
        if self.state == "ready":
            return "Control net ready"
        if self.state == "invalid":
            return "Invalid control-point grid"
        count = len(self.missing_point_ids)
        return f"Waiting for {count} control point{'s' if count != 1 else ''}"


@dataclass(frozen=True)
class FaceAssessment:
    """Conservative, explainable triage of the evidence available for one face."""

    category: Literal["OK", "BAD", "JUDGEMENT CALL"]
    reasons: tuple[str, ...]
    surface_type: str


@dataclass
class GeometrySnapshot:
    points: dict[int, Point3] = field(default_factory=dict)
    vertices: dict[int, Point3] = field(default_factory=dict)
    polylines: list[Polyline] = field(default_factory=list)
    faces: list[Mesh] = field(default_factory=list)
    control_nets: list[ControlNet] = field(default_factory=list)
    control_net_statuses: list[ControlNetStatus] = field(default_factory=list)
    # Per-entity geometry-handler time, separate from the complete prefix build.
    entity_build_seconds: dict[int, float] = field(default_factory=dict)
    # Wall time for rebuilding the full document prefix included in this snapshot.
    build_elapsed_seconds: float | None = None
    # Visual changes made by one incremental prefix advance. ``None`` means the
    # snapshot came from a full build; an empty mapping means no visible geometry
    # changed. Values are (kind, item) pairs, or None when an item was removed.
    render_updates: dict[int, tuple[str, object] | None] | None = None

    def point_for(self, entity_id: int) -> Point3 | None:
        return self.points.get(entity_id) or self.vertices.get(entity_id)


def _ref(value: StepValue) -> int | None:
    return value.entity_id if isinstance(value, StepReference) else None


def _aggregate(value: StepValue) -> tuple[StepValue, ...]:
    return value.values if isinstance(value, StepAggregate) else ()


def _number(value: StepValue) -> float | None:
    return float(value.value) if isinstance(value, StepNumber) else None


def _point_coordinates(entity: StepEntity) -> tuple[StepValue, ...]:
    if not entity.arguments:
        return ()
    return _aggregate(entity.arguments[1] if len(entity.arguments) > 1 else entity.arguments[0])


class GeometryBuilder:
    """Build simple primitives from all entities up to a physical order."""

    MAX_CURVED_TRIM_TRIANGLES = 2048

    def __init__(self, document: StepDocument, *, heal_orientations: bool = True):
        self.document = document
        self.heal_orientations = heal_orientations
        # Playback rebuilds progressively larger prefixes of the same STEP
        # document. Key successful tessellations by the visible part of their
        # dependency graph, so unchanged faces do not repeat curved-surface
        # work while later topology can still update an incomplete preview.
        self._face_mesh_cache: dict[tuple[int, bool, tuple[int, ...]], Mesh] = {}
        self._prefix_order: int | None = None
        self._prefix_heal = heal_orientations
        self._prefix_visible: dict[int, StepEntity] = {}
        self._prefix_snapshot: GeometrySnapshot | None = None

    def build(self, through_order: int | None = None, progress=None, measure_times: bool = False) -> GeometrySnapshot:
        if through_order is not None:
            through_order = max(-1, min(through_order, len(self.document.entities) - 1))
        if through_order is not None and progress is None and self._prefix_heal == self.heal_orientations:
            if through_order == self._prefix_order and self._prefix_snapshot is not None and not measure_times:
                return self._prefix_snapshot
            if self._prefix_order is not None and through_order == self._prefix_order + 1:
                return self._advance_prefix(through_order, measure_times)
        build_started = perf_counter() if measure_times else None
        entities = self.document.entities if through_order is None else self.document.entities[: through_order + 1]
        visible = {entity.entity_id: entity for entity in entities}
        snapshot = GeometrySnapshot()
        for entity in entities:
            if entity.type_name.upper() != "CARTESIAN_POINT":
                continue
            started = perf_counter() if measure_times else None
            coords = _point_coordinates(entity)
            if len(coords) >= 3 and all(_number(item) is not None for item in coords[:3]):
                snapshot.points[entity.entity_id] = Point3(*(_number(item) for item in coords[:3]))
            if started is not None:
                snapshot.entity_build_seconds[entity.entity_id] = perf_counter() - started
        for entity in entities:
            if entity.type_name.upper() != "B_SPLINE_SURFACE_WITH_KNOTS":
                continue
            started = perf_counter() if measure_times else None
            control_net, status = self._control_net(entity, snapshot)
            snapshot.control_net_statuses.append(status)
            if control_net:
                snapshot.control_nets.append(control_net)
            if started is not None:
                snapshot.entity_build_seconds[entity.entity_id] = perf_counter() - started
        for entity in entities:
            if entity.type_name.upper() != "VERTEX_POINT":
                continue
            started = perf_counter() if measure_times else None
            target = next((_ref(item) for item in entity.arguments if _ref(item) is not None), None)
            if target in snapshot.points:
                snapshot.vertices[entity.entity_id] = snapshot.points[target]
            if started is not None:
                snapshot.entity_build_seconds[entity.entity_id] = perf_counter() - started
        # Face tessellation is usually the slowest handler. The UI can opt into
        # timing each supported geometry handler; progress remains independent.
        reporter = progress
        face_total = sum(1 for e in entities if e.type_name.upper() == "ADVANCED_FACE")
        face_done = 0
        for entity in entities:
            name = entity.type_name.upper()
            if name == "EDGE_CURVE":
                started = perf_counter() if measure_times else None
                points = self._edge_points(entity, snapshot)
                if len(points) >= 2:
                    snapshot.polylines.append(Polyline(entity.entity_id, points))
                if started is not None:
                    snapshot.entity_build_seconds[entity.entity_id] = perf_counter() - started
            elif name == "ORIENTED_EDGE":
                started = perf_counter() if measure_times else None
                edge_refs = [item for item in entity.arguments if _ref(item) is not None]
                edge = self.document.entity(_ref(edge_refs[-1]) if edge_refs else -1)
                if edge and edge.type_name.upper() == "EDGE_CURVE":
                    points = self._edge_points(edge, snapshot)
                    if len(points) >= 2:
                        same_sense = next((item for item in entity.arguments if isinstance(item, StepEnumeration)), None)
                        if same_sense and same_sense.value.upper() == "F":
                            points = tuple(reversed(points))
                        snapshot.polylines.append(Polyline(entity.entity_id, points, "oriented-edge"))
                if started is not None:
                    snapshot.entity_build_seconds[entity.entity_id] = perf_counter() - started
            elif name == "ADVANCED_FACE":
                started = perf_counter() if measure_times else None
                mesh = self._face_mesh(entity, snapshot, visible)
                if mesh:
                    snapshot.faces.append(mesh)
                if started is not None:
                    snapshot.entity_build_seconds[entity.entity_id] = perf_counter() - started
                face_done += 1
                if reporter is not None and reporter.enabled:
                    reporter.item(face_done, face_total, "faces")
        if reporter is not None and reporter.enabled:
            reporter.end_item()
        if build_started is not None:
            snapshot.build_elapsed_seconds = perf_counter() - build_started
        if through_order is not None and progress is None:
            self._prefix_order = through_order
            self._prefix_heal = self.heal_orientations
            self._prefix_visible = visible
            self._prefix_snapshot = snapshot
        return snapshot

    def _advance_prefix(self, through_order: int, measure_times: bool) -> GeometrySnapshot:
        """Recompute only geometry whose dependency graph reaches the new entity."""

        started = perf_counter() if measure_times else None
        previous = self._prefix_snapshot
        assert previous is not None
        entity = self.document.entities[through_order]
        self._prefix_visible[entity.entity_id] = entity
        affected: set[int] = set()
        pending = [entity.entity_id]
        while pending:
            current = pending.pop()
            if current in affected:
                continue
            affected.add(current)
            pending.extend(
                parent for parent in self.document.incoming.get(current, ())
                if self.document.entity(parent) is not None
                and self.document.entity(parent).order <= through_order
            )

        geometry_types = {
            "CARTESIAN_POINT", "B_SPLINE_SURFACE_WITH_KNOTS", "VERTEX_POINT",
            "EDGE_CURVE", "ORIENTED_EDGE", "ADVANCED_FACE",
        }
        changed = [
            self.document.entity(entity_id) for entity_id in affected
            if self.document.entity(entity_id) is not None
            and self.document.entity(entity_id).type_name.upper() in geometry_types
        ]
        if not changed:
            self._prefix_order = through_order
            snapshot = GeometrySnapshot(
                points=previous.points,
                vertices=previous.vertices,
                polylines=previous.polylines,
                faces=previous.faces,
                control_nets=previous.control_nets,
                control_net_statuses=previous.control_net_statuses,
                build_elapsed_seconds=perf_counter() - started if started is not None else None,
                render_updates={},
            )
            self._prefix_snapshot = snapshot
            return snapshot

        snapshot = GeometrySnapshot(
            points=previous.points.copy(),
            vertices=previous.vertices.copy(),
            polylines=list(previous.polylines),
            faces=list(previous.faces),
            control_nets=list(previous.control_nets),
            control_net_statuses=list(previous.control_net_statuses),
        )
        polylines = {item.entity_id: item for item in snapshot.polylines}
        faces = {item.entity_id: item for item in snapshot.faces}
        nets = {item.entity_id: item for item in snapshot.control_nets}
        statuses = {item.entity_id: item for item in snapshot.control_net_statuses}
        render_updates: dict[int, tuple[str, object] | None] = {}
        priority = {
            "CARTESIAN_POINT": 0, "B_SPLINE_SURFACE_WITH_KNOTS": 1,
            "VERTEX_POINT": 2, "EDGE_CURVE": 3, "ORIENTED_EDGE": 4,
            "ADVANCED_FACE": 5,
        }
        for changed_entity in sorted(changed, key=lambda item: (priority[item.type_name.upper()], item.order)):
            entity_started = perf_counter() if measure_times else None
            name = changed_entity.type_name.upper()
            entity_id = changed_entity.entity_id
            if name == "CARTESIAN_POINT":
                coords = _point_coordinates(changed_entity)
                if len(coords) >= 3 and all(_number(item) is not None for item in coords[:3]):
                    point = Point3(*(_number(item) for item in coords[:3]))
                    snapshot.points[entity_id] = point
                    render_updates[entity_id] = ("point", point)
                else:
                    snapshot.points.pop(entity_id, None)
                    render_updates[entity_id] = None
            elif name == "B_SPLINE_SURFACE_WITH_KNOTS":
                net, status = self._control_net(changed_entity, snapshot)
                statuses[entity_id] = status
                if net is None:
                    nets.pop(entity_id, None)
                    render_updates[entity_id] = None
                else:
                    nets[entity_id] = net
                    render_updates[entity_id] = ("control_net", net)
            elif name == "VERTEX_POINT":
                target = next((_ref(item) for item in changed_entity.arguments if _ref(item) is not None), None)
                if target in snapshot.points:
                    point = snapshot.points[target]
                    snapshot.vertices[entity_id] = point
                    render_updates[entity_id] = ("vertex", point)
                else:
                    snapshot.vertices.pop(entity_id, None)
                    render_updates[entity_id] = None
            elif name in {"EDGE_CURVE", "ORIENTED_EDGE"}:
                edge = changed_entity
                if name == "ORIENTED_EDGE":
                    edge_refs = [item for item in changed_entity.arguments if _ref(item) is not None]
                    edge = self.document.entity(_ref(edge_refs[-1]) if edge_refs else -1)
                points = self._edge_points(edge, snapshot) if edge and edge.type_name.upper() == "EDGE_CURVE" else ()
                if name == "ORIENTED_EDGE" and points:
                    sense = next((item for item in changed_entity.arguments if isinstance(item, StepEnumeration)), None)
                    if sense and sense.value.upper() == "F":
                        points = tuple(reversed(points))
                if len(points) >= 2:
                    polyline = Polyline(entity_id, points, "oriented-edge" if name == "ORIENTED_EDGE" else "edge")
                    polylines[entity_id] = polyline
                    render_updates[entity_id] = ("polyline", polyline)
                else:
                    polylines.pop(entity_id, None)
                    render_updates[entity_id] = None
            elif name == "ADVANCED_FACE":
                mesh = self._face_mesh(changed_entity, snapshot, self._prefix_visible)
                if mesh is None:
                    faces.pop(entity_id, None)
                    render_updates[entity_id] = None
                else:
                    faces[entity_id] = mesh
                    render_updates[entity_id] = ("face", mesh)
            if entity_started is not None:
                snapshot.entity_build_seconds[entity_id] = perf_counter() - entity_started
        order = lambda entity_id: self.document.entity(entity_id).order
        snapshot.polylines = [polylines[item] for item in sorted(polylines, key=order)]
        snapshot.faces = [faces[item] for item in sorted(faces, key=order)]
        snapshot.control_nets = [nets[item] for item in sorted(nets, key=order)]
        snapshot.control_net_statuses = [statuses[item] for item in sorted(statuses, key=order)]
        snapshot.render_updates = render_updates
        if started is not None:
            snapshot.build_elapsed_seconds = perf_counter() - started
        self._prefix_order = through_order
        self._prefix_snapshot = snapshot
        return snapshot

    @staticmethod
    def _control_net(
        entity: StepEntity, snapshot: GeometrySnapshot
    ) -> tuple[ControlNet | None, ControlNetStatus]:
        """Resolve a B-spline control grid and report its playback readiness.

        The fourth B_SPLINE_SURFACE_WITH_KNOTS argument is its two-dimensional
        `control_points_list`.  During playback it is common for the surface
        to precede one or more referenced points, so an incomplete grid is
        deliberately withheld instead of being partially fabricated.
        """

        if len(entity.arguments) < 4:
            return None, ControlNetStatus(entity.entity_id, "invalid")
        source_rows = _aggregate(entity.arguments[3])
        if not source_rows:
            return None, ControlNetStatus(entity.entity_id, "invalid")
        rows: list[tuple[Point3, ...]] = []
        point_id_rows: list[tuple[int, ...]] = []
        missing_point_ids: list[int] = []
        for source_row in source_rows:
            point_refs = _aggregate(source_row)
            if not point_refs:
                return None, ControlNetStatus(entity.entity_id, "invalid")
            points: list[Point3] = []
            row_point_ids: list[int] = []
            for value in point_refs:
                point_id = _ref(value)
                if point_id is None:
                    return None, ControlNetStatus(entity.entity_id, "invalid")
                row_point_ids.append(point_id)
                point = snapshot.points.get(point_id)
                if point is None:
                    missing_point_ids.append(point_id)
                    continue
                points.append(point)
            rows.append(tuple(points))
            point_id_rows.append(tuple(row_point_ids))
        if missing_point_ids:
            return None, ControlNetStatus(
                entity.entity_id,
                "waiting",
                tuple(dict.fromkeys(missing_point_ids)),
            )
        try:
            return (
                ControlNet(entity.entity_id, tuple(rows), tuple(point_id_rows)),
                ControlNetStatus(entity.entity_id, "ready"),
            )
        except ValueError:
            return None, ControlNetStatus(entity.entity_id, "invalid")

    def _edge_points(self, entity: StepEntity, snapshot: GeometrySnapshot) -> tuple[Point3, ...]:
        refs = [_ref(item) for item in entity.arguments if _ref(item) is not None][:2]
        points = tuple(snapshot.point_for(item) for item in refs if item is not None and snapshot.point_for(item) is not None)
        curve_id = next((_ref(item) for item in entity.arguments[3:4]), None)
        curve = self.document.entity(curve_id or -1)
        # Open CASCADE/FreeCAD commonly wraps the actual 3-D curve in a
        # SURFACE_CURVE (or SEAM_CURVE), followed by its p-curves.  The first
        # reference is the curve in model space; sampling the 2-D p-curves (or
        # ignoring the wrapper) reduces every circular edge to a chord.
        if curve and curve.type_name.upper() in {"SURFACE_CURVE", "SEAM_CURVE"}:
            model_curve_id = next((_ref(item) for item in curve.arguments if _ref(item) is not None), None)
            curve = self.document.entity(model_curve_id or -1)
        if len(points) == 2 and curve and curve.type_name.upper() == "CIRCLE":
            same_sense = next(
                (item for item in reversed(entity.arguments) if isinstance(item, StepEnumeration)), None
            )
            sampled = self._circle_arc(
                curve,
                points[0],
                points[1],
                same_sense is None or same_sense.value.upper() == "T",
            )
            if sampled:
                return sampled
        if len(points) == 2 and curve and curve.type_name.upper() == "ELLIPSE":
            same_sense = next(
                (item for item in reversed(entity.arguments) if isinstance(item, StepEnumeration)), None
            )
            sampled = self._ellipse_arc(
                curve,
                points[0],
                points[1],
                same_sense is None or same_sense.value.upper() == "T",
            )
            if sampled:
                return sampled
        if len(points) == 2 and curve and curve.type_name.upper() == "B_SPLINE_CURVE_WITH_KNOTS":
            sampled = self._bspline_curve_points(curve)
            if sampled:
                same_sense = next(
                    (item for item in reversed(entity.arguments) if isinstance(item, StepEnumeration)), None
                )
                if same_sense and same_sense.value.upper() == "F":
                    sampled = tuple(reversed(sampled))
                sampled = tuple(sampled)
                sampled = (points[0], *sampled[1:-1], points[1])
                return sampled
        if len(points) == 2 and curve and curve.type_name.upper() == "COMPLEX":
            sampled = self._complex_bspline_curve_points(curve)
            if sampled:
                same_sense = next(
                    (item for item in reversed(entity.arguments) if isinstance(item, StepEnumeration)), None
                )
                if same_sense and same_sense.value.upper() == "F":
                    sampled = tuple(reversed(sampled))
                return (points[0], *sampled[1:-1], points[1])
        return points

    def _adaptive_curve_points(
        self,
        evaluate,
        start: float,
        end: float,
        tolerance: float,
        *,
        base_steps: int = 4,
        max_depth: int = 1,
    ) -> tuple[Point3, ...]:
        """Sample a spline until quarter-point chord error is within tolerance."""
        cache: dict[float, Point3 | None] = {}

        def sample(parameter: float) -> Point3 | None:
            if parameter not in cache:
                cache[parameter] = evaluate(parameter)
            return cache[parameter]

        first, last = sample(start), sample(end)
        if first is None or last is None:
            return ()

        def chord_error(point: Point3, a: Point3, b: Point3) -> float:
            direction = (b.x - a.x, b.y - a.y, b.z - a.z)
            offset = (point.x - a.x, point.y - a.y, point.z - a.z)
            length_squared = sum(value * value for value in direction)
            portion = 0.0 if length_squared <= 1e-24 else max(
                0.0, min(1.0, sum(x * y for x, y in zip(offset, direction)) / length_squared),
            )
            return math.sqrt(sum(
                (offset[axis] - portion * direction[axis]) ** 2 for axis in range(3)
            ))

        def subdivide(a, p_a, b, p_b, depth):
            parameters = (a + (b - a) * 0.25, (a + b) * 0.5, a + (b - a) * 0.75)
            probes = tuple(sample(parameter) for parameter in parameters)
            if any(point is None for point in probes):
                return None
            if max(chord_error(point, p_a, p_b) for point in probes) <= tolerance:
                return (p_a, p_b)
            if depth >= max_depth:
                return (p_a, *probes, p_b)
            middle, p_middle = parameters[1], probes[1]
            left = subdivide(a, p_a, middle, p_middle, depth + 1)
            right = subdivide(middle, p_middle, b, p_b, depth + 1)
            if left is None or right is None:
                return None
            return (*left[:-1], *right)

        result = []
        for index in range(base_steps):
            left = start + (end - start) * index / base_steps
            right = start + (end - start) * (index + 1) / base_steps
            span = subdivide(left, sample(left), right, sample(right), 0)
            if span is None:
                return ()
            result.extend(span if not result else span[1:])
        return tuple(result)

    def _complex_bspline_curve_points(self, curve: StepEntity) -> tuple[Point3, ...]:
        """Sample a rational B-spline stored as a STEP complex curve entity."""

        if len(curve.arguments) != 1 or not isinstance(curve.arguments[0], StepComplexValue):
            return ()
        components = {item.name.upper(): item for item in curve.arguments[0].components}
        spline = components.get("B_SPLINE_CURVE")
        knot_data = components.get("B_SPLINE_CURVE_WITH_KNOTS")
        rational = components.get("RATIONAL_B_SPLINE_CURVE")
        if spline is None or knot_data is None or rational is None:
            return ()
        if len(spline.arguments) < 2 or len(knot_data.arguments) < 2 or not rational.arguments:
            return ()
        degree_value = _number(spline.arguments[0])
        control_ids = [_ref(value) for value in _aggregate(spline.arguments[1])]
        weights = [_number(value) for value in _aggregate(rational.arguments[0])]
        multiplicities = [_number(value) for value in _aggregate(knot_data.arguments[0])]
        knot_values = [_number(value) for value in _aggregate(knot_data.arguments[1])]
        if (degree_value is None or degree_value < 1 or not degree_value.is_integer()
                or len(control_ids) != len(weights) or any(value is None for value in control_ids)
                or any(value is None or not math.isfinite(value) or value <= 0 for value in weights)
                or len(multiplicities) != len(knot_values)
                or any(value is None or value < 1 or not value.is_integer() for value in multiplicities)
                or any(value is None or not math.isfinite(value) for value in knot_values)):
            return ()
        degree = int(degree_value)
        controls = [self._cartesian(entity_id) for entity_id in control_ids]
        if len(controls) <= degree or any(point is None for point in controls):
            return ()
        knots = [float(knot) for multiplicity, knot in zip(multiplicities, knot_values)
                 for _ in range(int(multiplicity))]
        if len(knots) != len(controls) + degree + 1 or any(left > right for left, right in zip(knots, knots[1:])):
            return ()
        first, last = knots[degree], knots[len(controls)]
        if last <= first:
            return ()

        homogeneous = [
            (point.x * weight, point.y * weight, point.z * weight, weight)
            for point, weight in zip(controls, weights)
        ]

        def evaluate(parameter: float) -> Point3 | None:
            if parameter >= last:
                return controls[-1]
            span = next((index for index in range(degree, len(controls))
                         if knots[index] <= parameter < knots[index + 1]), None)
            if span is None:
                return None
            values = [list(homogeneous[index]) for index in range(span - degree, span + 1)]
            for level in range(1, degree + 1):
                for index in range(degree, level - 1, -1):
                    knot_index = span - degree + index
                    denominator = knots[knot_index + degree - level + 1] - knots[knot_index]
                    fraction = (parameter - knots[knot_index]) / denominator if denominator else 0.0
                    values[index] = [
                        (1.0 - fraction) * values[index - 1][axis] + fraction * values[index][axis]
                        for axis in range(4)
                    ]
            x, y, z, weight = values[degree]
            return Point3(x / weight, y / weight, z / weight) if weight > 1e-12 else None

        breaks = sorted({value for value in knots if first <= value <= last})
        if len(breaks) < 2:
            return ()
        scale = max(
            max(getattr(point, axis) for point in controls)
            - min(getattr(point, axis) for point in controls)
            for axis in ("x", "y", "z")
        )
        tolerance = max(scale * 5e-4, 1e-8)
        result = []
        for left, right in zip(breaks, breaks[1:]):
            steps = 18 if len(breaks) == 2 else 4
            span = self._adaptive_curve_points(evaluate, left, right, tolerance, base_steps=steps)
            if not span:
                return ()
            result.extend(span if not result else span[1:])
        return tuple(result)

    def _bspline_curve_points(self, curve: StepEntity) -> tuple[Point3, ...]:
        """Sample a non-rational B-spline curve over each non-empty knot span."""

        if len(curve.arguments) < 8:
            return ()
        degree_value = _number(curve.arguments[1])
        control_ids = [
            _ref(value)
            for value in _aggregate(curve.arguments[2])
            if _ref(value) is not None
        ]
        multiplicities = [_number(value) for value in _aggregate(curve.arguments[6])]
        knot_values = [_number(value) for value in _aggregate(curve.arguments[7])]
        if (
            degree_value is None
            or degree_value < 1
            or not control_ids
            or len(multiplicities) != len(knot_values)
            or any(value is None or value <= 0 or not value.is_integer() for value in multiplicities)
            or any(value is None for value in knot_values)
        ):
            return ()
        degree = int(degree_value)
        controls = [self._cartesian(entity_id) for entity_id in control_ids]
        if any(point is None for point in controls) or len(controls) <= degree:
            return ()
        knots = [
            float(knot)
            for multiplicity, knot in zip(multiplicities, knot_values)
            for _ in range(int(multiplicity))
        ]
        if len(knots) != len(controls) + degree + 1:
            return ()

        first_parameter = knots[degree]
        last_parameter = knots[len(controls)]
        if last_parameter <= first_parameter:
            return ()
        knot_breaks = sorted(
            {value for value in knots if first_parameter <= value <= last_parameter}
        )
        if len(knot_breaks) < 2:
            return ()

        def evaluate(parameter: float) -> Point3:
            if parameter >= last_parameter:
                return controls[-1]
            span = next(
                (
                    index
                    for index in range(degree, len(controls))
                    if knots[index] <= parameter < knots[index + 1]
                ),
                None,
            )
            if span is None:
                return controls[0]
            values = [
                [controls[index].x, controls[index].y, controls[index].z]
                for index in range(span - degree, span + 1)
            ]
            for level in range(1, degree + 1):
                for index in range(degree, level - 1, -1):
                    knot_index = span - degree + index
                    denominator = knots[knot_index + degree - level + 1] - knots[knot_index]
                    fraction = (parameter - knots[knot_index]) / denominator if denominator else 0.0
                    values[index] = [
                        (1.0 - fraction) * values[index - 1][axis]
                        + fraction * values[index][axis]
                        for axis in range(3)
                    ]
            return Point3(*values[degree])

        scale = max(
            max(getattr(point, axis) for point in controls)
            - min(getattr(point, axis) for point in controls)
            for axis in ("x", "y", "z")
        )
        tolerance = max(scale * 5e-4, 1e-8)
        result = []
        for start, end in zip(knot_breaks, knot_breaks[1:]):
            span = self._adaptive_curve_points(evaluate, start, end, tolerance)
            if not span:
                return ()
            result.extend(span if not result else span[1:])
        return tuple(result)

    def _circle_arc(
        self, circle: StepEntity, start: Point3, end: Point3, same_sense: bool
    ) -> tuple[Point3, ...]:
        placement_id = next((_ref(item) for item in circle.arguments if _ref(item) is not None), None)
        radius = next((_number(item) for item in circle.arguments if _number(item) is not None), None)
        placement = self.document.entity(placement_id or -1)
        if not placement or placement.type_name.upper() != "AXIS2_PLACEMENT_3D" or radius is None:
            return ()
        refs = [_ref(item) for item in placement.arguments if _ref(item) is not None]
        if not refs:
            return ()
        center = self._cartesian(refs[0])
        normal = self._direction(refs[1]) if len(refs) > 1 else (0.0, 0.0, 1.0)
        x_axis = self._direction(refs[2]) if len(refs) > 2 else None
        if center is None or normal is None:
            return ()
        normal = self._normalized(normal)
        if x_axis is None:
            seed = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
            x_axis = self._normalized(self._cross(seed, normal))
        else:
            x_axis = self._normalized(x_axis)
        y_axis = self._normalized(self._cross(normal, x_axis))
        if not normal or not x_axis or not y_axis:
            return ()

        def angle(point: Point3) -> float:
            delta = (point.x - center.x, point.y - center.y, point.z - center.z)
            return math.atan2(self._dot(delta, y_axis), self._dot(delta, x_axis))

        start_angle = angle(start)
        sweep = (angle(end) - start_angle) % (2.0 * math.pi)
        # Coincident endpoints denote a complete circle in STEP.
        if sweep < 1e-9:
            sweep = 2.0 * math.pi
        if not same_sense:
            sweep -= 2.0 * math.pi
        segments = max(4, int(math.ceil(abs(sweep) / (math.pi / 18.0))))
        result = []
        for index in range(segments + 1):
            parameter = start_angle + sweep * index / segments
            result.append(
                Point3(
                    center.x + radius * (math.cos(parameter) * x_axis[0] + math.sin(parameter) * y_axis[0]),
                    center.y + radius * (math.cos(parameter) * x_axis[1] + math.sin(parameter) * y_axis[1]),
                    center.z + radius * (math.cos(parameter) * x_axis[2] + math.sin(parameter) * y_axis[2]),
                )
            )
        # Preserve exact topology vertices, avoiding tiny seams from trigonometry.
        result[0], result[-1] = start, end
        return tuple(result)

    def _ellipse_arc(
        self, ellipse: StepEntity, start: Point3, end: Point3, same_sense: bool
    ) -> tuple[Point3, ...]:
        """Sample a trimmed STEP ellipse in its placement coordinate system."""

        placement_id = next((_ref(item) for item in ellipse.arguments if _ref(item) is not None), None)
        radii = [_number(item) for item in ellipse.arguments if _number(item) is not None]
        placement = self.document.entity(placement_id or -1)
        if (
            not placement
            or placement.type_name.upper() != "AXIS2_PLACEMENT_3D"
            or len(radii) < 2
            or radii[0] <= 0.0
            or radii[1] <= 0.0
        ):
            return ()
        refs = [_ref(item) for item in placement.arguments if _ref(item) is not None]
        if not refs:
            return ()
        center = self._cartesian(refs[0])
        normal = self._direction(refs[1]) if len(refs) > 1 else (0.0, 0.0, 1.0)
        x_axis = self._direction(refs[2]) if len(refs) > 2 else None
        if center is None or normal is None:
            return ()
        normal = self._normalized(normal)
        if x_axis is None:
            seed = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
            x_axis = self._normalized(self._cross(seed, normal))
        else:
            x_axis = self._normalized(x_axis)
        y_axis = self._normalized(self._cross(normal, x_axis)) if normal and x_axis else None
        if not normal or not x_axis or not y_axis:
            return ()

        major_radius, minor_radius = radii[:2]

        def angle(point: Point3) -> float:
            delta = (point.x - center.x, point.y - center.y, point.z - center.z)
            return math.atan2(
                self._dot(delta, y_axis) / minor_radius,
                self._dot(delta, x_axis) / major_radius,
            )

        start_angle = angle(start)
        sweep = (angle(end) - start_angle) % (2.0 * math.pi)
        # As with a circle, coincident trim vertices denote one full ellipse.
        if sweep < 1e-9:
            sweep = 2.0 * math.pi
        if not same_sense:
            sweep -= 2.0 * math.pi
        segments = max(4, int(math.ceil(abs(sweep) / (math.pi / 18.0))))
        result = []
        for index in range(segments + 1):
            parameter = start_angle + sweep * index / segments
            result.append(
                Point3(
                    center.x + major_radius * math.cos(parameter) * x_axis[0]
                    + minor_radius * math.sin(parameter) * y_axis[0],
                    center.y + major_radius * math.cos(parameter) * x_axis[1]
                    + minor_radius * math.sin(parameter) * y_axis[1],
                    center.z + major_radius * math.cos(parameter) * x_axis[2]
                    + minor_radius * math.sin(parameter) * y_axis[2],
                )
            )
        result[0], result[-1] = start, end
        return tuple(result)

    def _cartesian(self, entity_id: int) -> Point3 | None:
        entity = self.document.entity(entity_id)
        if not entity or entity.type_name.upper() != "CARTESIAN_POINT":
            return None
        values = next((_aggregate(item) for item in entity.arguments if isinstance(item, StepAggregate)), ())
        coords = [_number(item) for item in values[:3]]
        return Point3(*coords) if len(coords) == 3 and all(value is not None for value in coords) else None

    def _direction(self, entity_id: int) -> tuple[float, float, float] | None:
        entity = self.document.entity(entity_id)
        if not entity or entity.type_name.upper() != "DIRECTION":
            return None
        values = next((_aggregate(item) for item in entity.arguments if isinstance(item, StepAggregate)), ())
        coords = [_number(item) for item in values[:3]]
        return tuple(coords) if len(coords) == 3 and all(value is not None for value in coords) else None

    @staticmethod
    def _dot(first, second) -> float:
        return sum(a * b for a, b in zip(first, second))

    @staticmethod
    def _cross(first, second):
        return (
            first[1] * second[2] - first[2] * second[1],
            first[2] * second[0] - first[0] * second[2],
            first[0] * second[1] - first[1] * second[0],
        )

    @staticmethod
    def _normalized(vector):
        length = math.sqrt(sum(component * component for component in vector))
        return tuple(component / length for component in vector) if length > 1e-12 else None

    def face_boundary_entity_ids(self, face_id: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """Return oriented-edge and vertex entity ids around one ADVANCED_FACE."""

        face = self.document.entity(face_id)
        if face is None or face.type_name.upper() != "ADVANCED_FACE":
            return (), ()

        bound_ids = [
            item.entity_id
            for argument in face.arguments
            for item in _aggregate(argument)
            if isinstance(item, StepReference)
        ]
        edge_ids: list[int] = []
        vertex_ids: list[int] = []
        for bound_id in bound_ids:
            bound = self.document.entity(bound_id)
            if bound is None or bound.type_name.upper() not in {"FACE_OUTER_BOUND", "FACE_BOUND"}:
                continue
            loop_id = next((_ref(item) for item in bound.arguments if _ref(item) is not None), None)
            loop = self.document.entity(loop_id or -1)
            if loop is None:
                continue
            loop_type = loop.type_name.upper()
            if loop_type == "VERTEX_LOOP":
                vertex_id = next((_ref(item) for item in loop.arguments if _ref(item) is not None), None)
                if vertex_id is not None:
                    vertex_ids.append(vertex_id)
                continue
            if loop_type != "EDGE_LOOP":
                continue
            loop_items = next(
                (_aggregate(argument) for argument in loop.arguments if isinstance(argument, StepAggregate)),
                (),
            )
            for loop_item in loop_items:
                oriented_id = _ref(loop_item)
                oriented = self.document.entity(oriented_id or -1)
                if oriented is None or oriented.type_name.upper() != "ORIENTED_EDGE":
                    continue
                edge_refs = [_ref(item) for item in oriented.arguments if _ref(item) is not None]
                edge = self.document.entity(edge_refs[-1] if edge_refs else -1)
                if edge is None or edge.type_name.upper() != "EDGE_CURVE":
                    continue
                if oriented_id is not None:
                    edge_ids.append(oriented_id)
                vertex_ids.extend(
                    reference
                    for item in edge.arguments[1:3]
                    if (reference := _ref(item)) is not None
                )

        return tuple(dict.fromkeys(edge_ids)), tuple(dict.fromkeys(vertex_ids))

    def assess_face(self, face_id: int, mesh: Mesh | None = None) -> FaceAssessment:
        """Classify a face using local STEP topology and optional preview evidence.

        This is deliberately conservative: a broken reference or disconnected
        edge loop is ``BAD``; a supported surface with closed loops and a clean
        preview is ``OK``; anything the lightweight preview cannot check is a
        ``JUDGEMENT CALL``.
        """

        face = self.document.entity(face_id)
        if face is None or face.type_name.upper() != "ADVANCED_FACE":
            return FaceAssessment(
                "JUDGEMENT CALL",
                ("The selected entity is not an ADVANCED_FACE.",),
                "UNKNOWN",
            )

        surface_id = next((_ref(argument) for argument in face.arguments if _ref(argument) is not None), None)
        surface = self.document.entity(surface_id or -1)
        surface_type = surface.type_name.upper() if surface else "UNKNOWN"
        errors: list[str] = []
        uncertain: list[str] = []
        supported_surfaces = {
            "PLANE", "CYLINDRICAL_SURFACE", "CONICAL_SURFACE",
            "SPHERICAL_SURFACE", "TOROIDAL_SURFACE",
        }

        if surface_id is None:
            uncertain.append("No surface reference is available to check.")
        elif surface is None:
            errors.append(f"Surface reference #{surface_id} does not resolve.")
        elif surface_type not in supported_surfaces:
            uncertain.append(f"Surface type {surface_type} is not covered by the preview checks.")

        bound_ids = [
            item.entity_id
            for argument in face.arguments
            for item in _aggregate(argument)
            if isinstance(item, StepReference)
        ]
        if not bound_ids:
            uncertain.append("The face has no boundary loops to validate.")

        loop_count = 0
        edge_count = 0
        for bound_id in bound_ids:
            bound = self.document.entity(bound_id)
            if bound is None:
                errors.append(f"Boundary reference #{bound_id} does not resolve.")
                continue
            if bound.type_name.upper() not in {"FACE_OUTER_BOUND", "FACE_BOUND"}:
                errors.append(f"Boundary #{bound_id} has unexpected type {bound.type_name}.")
                continue
            loop_id = next((_ref(argument) for argument in bound.arguments if _ref(argument) is not None), None)
            loop = self.document.entity(loop_id or -1)
            if loop_id is None or loop is None:
                errors.append(f"Boundary #{bound_id} has no resolvable loop reference.")
                continue
            loop_type = loop.type_name.upper()
            if loop_type == "VERTEX_LOOP":
                uncertain.append(f"Loop #{loop_id} is a vertex loop; its degenerate geometry needs review.")
                continue
            if loop_type != "EDGE_LOOP":
                uncertain.append(f"Loop #{loop_id} has unsupported type {loop.type_name}.")
                continue

            loop_count += 1
            oriented_ids = [
                item.entity_id
                for argument in loop.arguments
                for item in _aggregate(argument)
                if isinstance(item, StepReference)
            ]
            if not oriented_ids:
                errors.append(f"Edge loop #{loop_id} contains no oriented edges.")
                continue

            directed_edges: list[tuple[int, int]] = []
            loop_valid = True
            for oriented_id in oriented_ids:
                oriented = self.document.entity(oriented_id)
                if oriented is None or oriented.type_name.upper() != "ORIENTED_EDGE":
                    errors.append(f"Loop #{loop_id} references invalid oriented edge #{oriented_id}.")
                    loop_valid = False
                    continue
                edge_refs = [
                    _ref(argument)
                    for argument in oriented.arguments
                    if _ref(argument) is not None
                ]
                edge_id = edge_refs[-1] if edge_refs else None
                edge = self.document.entity(edge_id or -1)
                if edge_id is None or edge is None or edge.type_name.upper() != "EDGE_CURVE":
                    errors.append(f"Oriented edge #{oriented_id} has no resolvable EDGE_CURVE.")
                    loop_valid = False
                    continue
                if len(edge.arguments) < 3:
                    errors.append(f"Edge curve #{edge_id} has no endpoint attributes.")
                    loop_valid = False
                    continue
                start_id = _ref(edge.arguments[1])
                end_id = _ref(edge.arguments[2])
                start_vertex = self.document.entity(start_id or -1)
                end_vertex = self.document.entity(end_id or -1)
                if (
                    start_id is None or end_id is None
                    or start_vertex is None or end_vertex is None
                    or start_vertex.type_name.upper() != "VERTEX_POINT"
                    or end_vertex.type_name.upper() != "VERTEX_POINT"
                ):
                    errors.append(f"Edge curve #{edge_id} has invalid endpoint references.")
                    loop_valid = False
                    continue
                orientation = next(
                    (argument.value.upper() for argument in oriented.arguments if isinstance(argument, StepEnumeration)),
                    None,
                )
                if orientation not in {"T", "F"}:
                    errors.append(f"Oriented edge #{oriented_id} has no valid direction flag.")
                    loop_valid = False
                    continue
                if orientation == "F":
                    start_id, end_id = end_id, start_id
                directed_edges.append((start_id, end_id))
                edge_count += 1

            if loop_valid and len(directed_edges) == len(oriented_ids):
                open_joins = sum(
                    end_id != directed_edges[(index + 1) % len(directed_edges)][0]
                    for index, (_, end_id) in enumerate(directed_edges)
                )
                if open_joins:
                    errors.append(
                        f"Edge loop #{loop_id} has {open_joins} disconnected join(s) between its edges."
                    )

        if mesh is None:
            uncertain.append("No preview mesh is available, so its shape and triangles cannot be checked.")
        elif not mesh.triangles:
            uncertain.append("The preview mesh contains no triangles.")
        else:
            span = max(
                max(getattr(point, axis) for point in mesh.points)
                - min(getattr(point, axis) for point in mesh.points)
                for axis in ("x", "y", "z")
            ) if mesh.points else 0.0
            area_tolerance = max(span ** 4 * 1e-24, 1e-48)
            degenerate_count = 0
            invalid_index_count = 0
            sliver_count = 0
            sliver_angle = math.radians(2.0)
            for triangle in mesh.triangles:
                if len(triangle) != 3 or any(index < 0 or index >= len(mesh.points) for index in triangle):
                    invalid_index_count += 1
                    continue
                first, second, third = (mesh.points[index] for index in triangle)
                ab = (second.x - first.x, second.y - first.y, second.z - first.z)
                ac = (third.x - first.x, third.y - first.y, third.z - first.z)
                cross = (
                    ab[1] * ac[2] - ab[2] * ac[1],
                    ab[2] * ac[0] - ab[0] * ac[2],
                    ab[0] * ac[1] - ab[1] * ac[0],
                )
                if sum(component * component for component in cross) <= area_tolerance:
                    degenerate_count += 1
                    continue
                edge_lengths = (
                    self._distance(first, second),
                    self._distance(second, third),
                    self._distance(third, first),
                )
                angle_sides = (
                    (edge_lengths[0], edge_lengths[2], edge_lengths[1]),
                    (edge_lengths[0], edge_lengths[1], edge_lengths[2]),
                    (edge_lengths[1], edge_lengths[2], edge_lengths[0]),
                )
                angles = []
                for first_side, second_side, opposite_side in angle_sides:
                    cosine = (
                        first_side ** 2 + second_side ** 2 - opposite_side ** 2
                    ) / (2.0 * first_side * second_side)
                    angles.append(math.acos(max(-1.0, min(1.0, cosine))))
                minimum_angle = min(angles)
                if minimum_angle < sliver_angle:
                    sliver_count += 1
            if invalid_index_count:
                uncertain.append(f"The preview contains {invalid_index_count} malformed triangle(s).")
            if degenerate_count:
                uncertain.append(f"The preview contains {degenerate_count} zero-area triangle(s).")
            valid_triangle_count = len(mesh.triangles) - invalid_index_count - degenerate_count
            if (
                valid_triangle_count
                and sliver_count >= min(3, valid_triangle_count)
                and sliver_count / valid_triangle_count >= 0.2
            ):
                errors.append(
                    f"Preview triangulation has {sliver_count}/{valid_triangle_count} sliver triangles "
                    "with an angle below 2 degrees."
                )

        if errors:
            category: Literal["OK", "BAD", "JUDGEMENT CALL"] = "BAD"
            reasons = errors + uncertain
        elif uncertain:
            category = "JUDGEMENT CALL"
            reasons = uncertain
        else:
            category = "OK"
            reasons = [
                f"{loop_count} edge loop(s) and {edge_count} oriented edge(s) connect by vertex reference.",
                f"Preview mesh has {len(mesh.triangles)} triangles without severe slivers.",
            ]
        return FaceAssessment(category, tuple(reasons), surface_type)

    def _face_mesh(self, entity: StepEntity, snapshot: GeometrySnapshot, visible: dict[int, StepEntity]) -> Mesh | None:
        if not self._face_dependencies_complete(entity, snapshot, visible):
            return None
        dependency_signature = self._visible_face_dependency_signature(entity, visible)
        cache_key = (entity.entity_id, self.heal_orientations, dependency_signature)
        cached = self._face_mesh_cache.get(cache_key)
        if cached is not None:
            return cached
        mesh = self._face_mesh_impl(entity, snapshot, visible)
        if mesh is None or self.heal_orientations:
            if mesh is not None:
                self._face_mesh_cache[cache_key] = mesh
            return mesh
        # Tessellators choose their own winding. Recover the STEP boundary
        # traversal rather than treating that implementation detail as input.
        boundary_edges = {}
        for a, b, c in mesh.triangles:
            for start, end in ((a, b), (b, c), (c, a)):
                edge_key = tuple(sorted((start, end)))
                boundary_edges.setdefault(edge_key, []).append((start, end))
        def key(point):
            return (round(point.x, 6), round(point.y, 6), round(point.z, 6))
        for bound_ref in _aggregate(entity.arguments[1]):
            bound = visible.get(_ref(bound_ref))
            if bound is None:
                continue
            loop = visible.get(_ref(bound.arguments[1]))
            if loop is None or loop.type_name.upper() != "EDGE_LOOP":
                continue
            contour = []
            for edge_ref in _aggregate(loop.arguments[1]):
                oriented = visible.get(_ref(edge_ref))
                if oriented is None:
                    continue
                edge = visible.get(_ref(oriented.arguments[3]))
                if edge is None:
                    continue
                points = self._edge_points(edge, snapshot)
                if oriented.arguments[-1].value == "F":
                    points = tuple(reversed(points))
                contour.extend(points[:-1])
            if bound.arguments[-1].value == "F":
                contour.reverse()
            indices = {key(point): index for index, point in enumerate(contour)}
            if len(indices) < 3:
                continue
            score = 0
            for uses in boundary_edges.values():
                if len(uses) != 1:
                    continue
                a, b = uses[0]
                start, end = indices.get(key(mesh.points[a])), indices.get(key(mesh.points[b]))
                if start is None or end is None or start == end:
                    continue
                delta = (end - start) % len(contour)
                score += 1 if delta < len(contour) / 2 else -1
            if score:
                if score < 0:
                    edge_ids = ", ".join(f"#{ref.entity_id}" for ref in _aggregate(loop.arguments[1]) if isinstance(ref, StepReference))
                    mesh = replace(
                        mesh,
                        triangles=tuple((a, c, b) for a, b, c in mesh.triangles),
                        orientation_warning=(
                            f"Face #{entity.entity_id}: possible wrong normal.\n\n"
                            f"Boundary loop #{loop.entity_id} traverses the preview boundary opposite "
                            "to the orientation implied by the analytic surface and ADVANCED_FACE sense.\n\n"
                            f"Oriented edges in this loop: {edge_ids}.\n\n"
                            "This is a suspected boundary/surface orientation mismatch, not proof that "
                            "a particular edge or face flag should be reversed. A red back face alone "
                            "does not indicate an error.\n\n"
                            "Enable Heal beside Play to compare with the surface-aligned preview. "
                            "The STEP document is unchanged."
                        ),
                    )
                    self._face_mesh_cache[cache_key] = mesh
                    return mesh
                self._face_mesh_cache[cache_key] = mesh
                return mesh
        self._face_mesh_cache[cache_key] = mesh
        return mesh

    def _face_dependencies_complete(
        self, entity: StepEntity, snapshot: GeometrySnapshot, visible: dict[int, StepEntity]
    ) -> bool:
        """Wait for the face surface, every trim edge, and their sampled geometry."""

        def available(entity_id: int | None) -> StepEntity | None:
            return visible.get(entity_id) if entity_id is not None else None

        surface_id = next((_ref(argument) for argument in entity.arguments if _ref(argument) is not None), None)
        surface = available(surface_id)
        if surface_id is not None and surface is None:
            return False
        if surface is not None:
            # Analytic surfaces need their placement axes and origin before the
            # mesh can be aligned with the declared surface normal.
            for reference in surface.references:
                dependency = available(reference)
                if dependency is None:
                    return False
                if dependency.type_name.upper() == "AXIS2_PLACEMENT_3D":
                    if any(available(nested) is None for nested in dependency.references):
                        return False

        bound_ids = [
            value.entity_id
            for argument in entity.arguments
            for value in _aggregate(argument)
            if isinstance(value, StepReference)
        ]
        if not bound_ids:
            return surface is not None and surface.type_name.upper() in {
                "SPHERICAL_SURFACE", "TOROIDAL_SURFACE",
            }

        for bound_id in bound_ids:
            bound = available(bound_id)
            if bound is None or bound.type_name.upper() not in {"FACE_OUTER_BOUND", "FACE_BOUND"}:
                return False
            loop_id = next((_ref(argument) for argument in bound.arguments if _ref(argument) is not None), None)
            loop = available(loop_id)
            if loop is None or loop.type_name.upper() != "EDGE_LOOP":
                return False
            oriented_ids = [
                reference
                for argument in loop.arguments
                for item in _aggregate(argument)
                if (reference := _ref(item)) is not None
            ]
            if not oriented_ids:
                return False
            for oriented_id in oriented_ids:
                oriented = available(oriented_id)
                if oriented is None or oriented.type_name.upper() != "ORIENTED_EDGE":
                    return False
                edge_id = next((_ref(argument) for argument in reversed(oriented.arguments) if _ref(argument) is not None), None)
                edge = available(edge_id)
                if edge is None or edge.type_name.upper() != "EDGE_CURVE":
                    return False

                endpoint_ids = [_ref(argument) for argument in edge.arguments[1:3]]
                if len(endpoint_ids) != 2:
                    return False
                for vertex_id in endpoint_ids:
                    vertex = available(vertex_id)
                    if vertex is None or vertex.type_name.upper() != "VERTEX_POINT":
                        return False
                    point_id = next((_ref(argument) for argument in vertex.arguments if _ref(argument) is not None), None)
                    if available(point_id) is None or point_id not in snapshot.points:
                        return False

                curve_id = next((_ref(argument) for argument in edge.arguments[3:4] if _ref(argument) is not None), None)
                curve = available(curve_id)
                if curve_id is not None and curve is None:
                    return False
                if curve is not None:
                    if curve.type_name.upper() in {"SURFACE_CURVE", "SEAM_CURVE"}:
                        model_curve_id = next((_ref(argument) for argument in curve.arguments if _ref(argument) is not None), None)
                        curve = available(model_curve_id)
                        if curve is None:
                            return False
                    if any(available(reference) is None for reference in curve.references):
                        return False
                    for reference in curve.references:
                        dependency = available(reference)
                        if dependency is not None and dependency.type_name.upper() == "AXIS2_PLACEMENT_3D":
                            if any(available(nested) is None for nested in dependency.references):
                                return False
        return True

    @staticmethod
    def _visible_face_dependency_signature(
        entity: StepEntity, visible: dict[int, StepEntity]
    ) -> tuple[int, ...]:
        """Track which reachable STEP dependencies are present in this prefix."""
        pending = [entity]
        visited = set()
        while pending:
            current = pending.pop()
            if current.entity_id in visited:
                continue
            visited.add(current.entity_id)
            for reference in current.references:
                dependency = visible.get(reference)
                if dependency is not None:
                    pending.append(dependency)
        return tuple(sorted(visited))

    def _face_mesh_impl(self, entity: StepEntity, snapshot: GeometrySnapshot, visible: dict[int, StepEntity]) -> Mesh | None:
        # AP203's ADVANCED_FACE has a bounds aggregate and a surface reference.
        surface_id = next((_ref(item) for item in entity.arguments if _ref(item) is not None), None)
        surface = visible.get(surface_id or -1)
        bound_ids: list[int] = []
        for argument in entity.arguments:
            bound_ids.extend(item.entity_id for item in _aggregate(argument) if isinstance(item, StepReference))
        loops: list[list[Point3]] = []
        loop_segments: list[list[tuple[Point3, ...]]] = []
        for bound_id in bound_ids:
            bound = visible.get(bound_id)
            if not bound or bound.type_name.upper() not in {"FACE_OUTER_BOUND", "FACE_BOUND"}:
                continue
            loop_id = next((_ref(item) for item in bound.arguments if _ref(item) is not None), None)
            loop = visible.get(loop_id or -1)
            if not loop or loop.type_name.upper() != "EDGE_LOOP":
                continue
            loop_items = next((_aggregate(argument) for argument in loop.arguments if isinstance(argument, StepAggregate)), ())
            polygon: list[Point3] = []
            segments: list[tuple[Point3, ...]] = []
            for oriented_id in (_ref(item) for item in loop_items):
                oriented = visible.get(oriented_id or -1)
                if not oriented:
                    continue
                edge_refs = [_ref(item) for item in oriented.arguments if _ref(item) is not None]
                edge = visible.get(edge_refs[-1] if edge_refs else -1)
                if edge:
                    points = self._edge_points(edge, snapshot)
                    same_sense = next((item for item in oriented.arguments if isinstance(item, StepEnumeration)), None)
                    if same_sense and same_sense.value.upper() == "F":
                        points = tuple(reversed(points))
                    if len(points) < 2:
                        continue
                    segments.append(points)
                    if not polygon:
                        polygon.extend(points)
                    elif polygon[-1] == points[0]:
                        polygon.extend(points[1:])
                    elif polygon[-1] == points[1]:
                        # Some exporters store the edge direction opposite to
                        # the loop order. Reverse it instead of introducing a
                        # disconnected/self-crossing fan polygon.
                        reversed_points = tuple(reversed(points))
                        segments[-1] = reversed_points
                        polygon.extend(reversed_points[1:])
            if len(polygon) >= 3 and polygon[0] == polygon[-1]:
                polygon.pop()
            if len(polygon) >= 3:
                loops.append(polygon)
                loop_segments.append(segments)
        if not loops:
            return self._closed_analytic_surface_mesh(entity, surface)
        if surface and surface.type_name.upper() == "COMPLEX":
            rational = self._rational_spline_surface(surface)
            if rational is not None:
                protected_points = tuple(
                    point for polyline in snapshot.polylines for point in polyline.points
                )
                patch = self._rational_trim_mesh(
                    rational, loops, loop_segments, protected_points,
                )
                if patch is None:
                    return None
                points, triangles = patch
                sense = next((arg for arg in reversed(entity.arguments) if isinstance(arg, StepEnumeration)), None)
                if sense and sense.value.upper() == "F":
                    triangles = tuple((a, c, b) for a, b, c in triangles)
                return Mesh(
                    entity.entity_id, points, triangles, curved=True,
                    type_name="RATIONAL_B_SPLINE_SURFACE",
                    sense=f".{sense.value.upper()}." if sense else ".T.",
                    sense_applied=True,
                    preview_note=(
                        "Self-intersecting rational B-spline preview uses trim-rail interpolation (approximate); "
                        "STEP source unchanged."
                        if rational.used_trim_interpolation else None
                    ),
                )
        if surface and surface.type_name.upper() == "B_SPLINE_SURFACE_WITH_KNOTS":
            loft = self._loft_spline_mesh(surface, loop_segments)
            if loft:
                points, triangles = loft
                sense = next((arg for arg in reversed(entity.arguments) if isinstance(arg, StepEnumeration)), None)
                if sense and sense.value.upper() == "F":
                    triangles = tuple((a, c, b) for a, b, c in triangles)
                return Mesh(
                    entity.entity_id, points, triangles, curved=True,
                    **self._face_metadata(entity, surface),
                )
            # A B-spline boundary is not a planar polygon. An unsupported
            # trim remains a control net instead of a fabricated planar cap.
            return None
        if surface and surface.type_name.upper() == "CONICAL_SURFACE" and len(loops) == 1:
            apex_fan = self._conical_apex_fan(surface, loops[0])
            if apex_fan:
                points, triangles = apex_fan
                return Mesh(
                    entity.entity_id, points,
                    self._oriented_face_triangles(entity, surface, points, triangles),
                    curved=True,
                    **self._face_metadata(entity, surface),
                )
        if surface and surface.type_name.upper() == "CYLINDRICAL_SURFACE" and len(loops) == 2:
            stitched = self._stitch_closed_loops(loops[0], loops[1])
            if stitched:
                points, triangles = stitched
                return Mesh(
                    entity.entity_id, points,
                    self._oriented_face_triangles(entity, surface, points, triangles),
                    curved=True,
                    **self._face_metadata(entity, surface),
                )
        if surface and surface.type_name.upper() == "CYLINDRICAL_SURFACE" and len(loops) == 1:
            full_strip = self._full_cylindrical_strip_mesh(surface, loop_segments[0])
            if full_strip:
                points, triangles = full_strip
                return Mesh(
                    entity.entity_id, points,
                    self._oriented_face_triangles(entity, surface, points, triangles, consistent=True),
                    curved=True,
                    **self._face_metadata(entity, surface),
                )
        if surface and surface.type_name.upper() == "TOROIDAL_SURFACE" and len(loops) == 1:
            toroidal_patch = self._toroidal_patch_mesh(surface, loops[0])
            if toroidal_patch:
                points, triangles = toroidal_patch
                return Mesh(
                    entity.entity_id, points,
                    self._oriented_face_triangles(entity, surface, points, triangles),
                    curved=True,
                    **self._face_metadata(entity, surface),
                )
        curved = [segment for segment in loop_segments[0] if len(segment) > 2]
        ruled_surface_types = {
            "CYLINDRICAL_SURFACE", "CONICAL_SURFACE",
            "SPHERICAL_SURFACE", "TOROIDAL_SURFACE",
        }
        is_ruled_strip = (
            len(loops) == 1
            and surface is not None
            and surface.type_name.upper() in ruled_surface_types
            and len(curved) == 2
            and (
                surface.type_name.upper() != "CONICAL_SURFACE"
                or (len(loop_segments[0]) == 4 and sum(len(segment) == 2 for segment in loop_segments[0]) == 2)
            )
        )
        if is_ruled_strip:
            first, second = curved
            if len(first) == len(second):
                # A bounded cylinder or cone strip joins matching samples on
                # its two arcs. A contour fan spans the opening with chords.
                if self._distance(first[0], second[0]) > self._distance(first[0], second[-1]):
                    second = tuple(reversed(second))
                points = first + second
                offset = len(first)
                triangles = []
                for index in range(len(first) - 1):
                    triangles.extend(((index, index + 1, offset + index + 1), (index, offset + index + 1, offset + index)))
                return Mesh(
                    entity.entity_id, points,
                    self._oriented_face_triangles(entity, surface, points, tuple(triangles)),
                    curved=True,
                    **self._face_metadata(entity, surface),
                )
            if surface.type_name.upper() == "CYLINDRICAL_SURFACE" and len(loop_segments[0]) == 4:
                unequal_strip = self._unequal_cylindrical_strip_mesh(surface, first, second)
                if unequal_strip:
                    points, triangles = unequal_strip
                    return Mesh(
                        entity.entity_id, points,
                        self._oriented_face_triangles(entity, surface, points, triangles, consistent=True),
                        curved=True,
                        **self._face_metadata(entity, surface),
                    )
        if surface and surface.type_name.upper() == "CONICAL_SURFACE":
            conical_trim = self._conical_trim_mesh(surface, loops)
            if conical_trim:
                points, triangles = conical_trim
                return Mesh(
                    entity.entity_id, points,
                    self._oriented_face_triangles(entity, surface, points, triangles, consistent=True),
                    curved=True,
                    **self._face_metadata(entity, surface),
                )
        if surface and surface.type_name.upper() == "CYLINDRICAL_SURFACE":
            cylindrical_trim = self._cylindrical_trim_mesh(surface, loops)
            if cylindrical_trim:
                points, triangles = cylindrical_trim
                return Mesh(
                    entity.entity_id, points,
                    self._oriented_face_triangles(entity, surface, points, triangles, consistent=True),
                    curved=True,
                    **self._face_metadata(entity, surface),
                )
        # The generic contour triangulator fills a planar polygon.  Do not
        # use it for an unsupported analytic or B-spline surface, even when
        # its boundary happens to look planar in 3D: that would fabricate a
        # cap away from the declared surface.  In particular, STEP complex
        # instances can wrap B_SPLINE_SURFACE_WITH_KNOTS while appearing here
        # simply as type_name == "COMPLEX".
        if surface and surface.type_name.upper() != "PLANE":
            return None
        if not self._loops_form_planar_face(loops, surface):
            return None
        triangulated = self._triangulate_loops(loops)
        if triangulated:
            points, triangles = triangulated
            return Mesh(
                entity.entity_id, points,
                self._oriented_face_triangles(entity, surface, points, triangles),
                **self._face_metadata(entity, surface),
            )
        return None

    def _rational_spline_surface(self, surface: StepEntity) -> _RationalSplineSurface | None:
        if len(surface.arguments) != 1 or not isinstance(surface.arguments[0], StepComplexValue):
            return None
        components = {component.name.upper(): component.arguments
                      for component in surface.arguments[0].components}
        bs = components.get("B_SPLINE_SURFACE")
        ks = components.get("B_SPLINE_SURFACE_WITH_KNOTS")
        rational = components.get("RATIONAL_B_SPLINE_SURFACE")
        if bs is None or ks is None or rational is None or len(bs) < 3 or len(ks) < 4 or len(rational) < 1:
            return None
        degrees = [_number(value) for value in bs[:2]]
        if any(value is None or value < 1 or not value.is_integer() for value in degrees):
            return None
        degrees = tuple(int(value) for value in degrees)
        rows = [_aggregate(value) for value in _aggregate(bs[2])]
        weight_rows = [_aggregate(value) for value in _aggregate(rational[0])]
        if (len(rows) <= degrees[0] or not rows or len(rows[0]) <= degrees[1]
                or len(rows) != len(weight_rows)
                or any(len(row) != len(rows[0]) for row in rows)
                or any(len(row) != len(rows[0]) for row in weight_rows)):
            return None
        controls = tuple(tuple(self._cartesian(_ref(value) or -1) for value in row) for row in rows)
        weights = tuple(tuple(_number(value) for value in row) for row in weight_rows)
        if (any(point is None for row in controls for point in row)
                or any(weight is None or not math.isfinite(weight) or weight <= 0
                       for row in weights for weight in row)):
            return None
        knots = []
        for axis, count in enumerate((len(rows), len(rows[0]))):
            multiplicities = [_number(value) for value in _aggregate(ks[axis])]
            values = [_number(value) for value in _aggregate(ks[axis + 2])]
            if (not values or len(values) != len(multiplicities)
                    or any(value is None or not math.isfinite(value) for value in values)
                    or any(value is None or value < 1 or not value.is_integer() for value in multiplicities)):
                return None
            expanded = tuple(value for mult, value in zip(multiplicities, values)
                             for _ in range(int(mult)))
            if (len(expanded) != count + degrees[axis] + 1
                    or any(a > b for a, b in zip(expanded, expanded[1:]))
                    or expanded[degrees[axis]] >= expanded[count]):
                return None
            knots.append(expanded)
        self_intersect = (
            len(bs) > 6
            and isinstance(bs[6], StepEnumeration)
            and bs[6].value.upper() == "T"
        )
        return _RationalSplineSurface(
            controls, weights, degrees, tuple(knots), self_intersect=self_intersect,
        )

    def _rational_dense_rail_mesh(
        self,
        surface: _RationalSplineSurface,
        boundary: tuple[Point3, ...],
        parameters: list[list[float]],
        segments: list[tuple[Point3, ...]],
    ) -> tuple[tuple[Point3, ...], tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Tessellate beside a very densely sampled rational trim rail.

        A trim may carry many STEP samples along a rail whose parameter values
        barely move. Ear clipping then connects that cluster to distant corners
        and leaves long spokes. This inserts a coarser interior rail and fills
        the narrow strip with a zipper triangulation, keeping every original
        boundary segment intact.
        """
        if len(segments) != 4 or not boundary:
            return None
        domain = (surface.u_range, surface.v_range)
        ranges = [item[1] - item[0] for item in domain]
        offsets: list[int] = []
        cursor = 0
        edge_ids: list[list[int]] = []
        for segment in segments:
            ids = [(cursor + index) % len(boundary) for index in range(len(segment))]
            offsets.append(cursor)
            edge_ids.append(ids)
            cursor += len(segment) - 1
        if cursor != len(boundary):
            return None

        candidate = None
        for edge_index, ids in enumerate(edge_ids):
            # The contour currently starts immediately after this rail, so
            # replacing it with an interior rail keeps the remaining loop in
            # its original cyclic order.
            if edge_index != 3:
                continue
            spans = [max(parameters[index][axis] for index in ids)
                     - min(parameters[index][axis] for index in ids) for axis in (0, 1)]
            relative = [spans[axis] / ranges[axis] for axis in (0, 1)]
            varying = 0 if relative[0] >= relative[1] else 1
            if relative[varying] < 0.5 or len(ids) < 64:
                continue
            opposite = edge_ids[(edge_index + 2) % 4]
            if len(ids) < 4 * len(opposite):
                continue
            candidate = edge_index, ids, varying
            break
        if candidate is None:
            return None

        edge_index, outer_ids, varying = candidate
        fixed = 1 - varying
        first, last = parameters[outer_ids[0]], parameters[outer_ids[-1]]
        fixed_value = sum(parameters[index][fixed] for index in outer_ids) / len(outer_ids)
        fixed_domain = domain[fixed]
        inward = 1.0 if fixed_value - fixed_domain[0] <= fixed_domain[1] - fixed_value else -1.0
        scale = max((self._distance(boundary[0], point) for point in boundary), default=1.0)
        separation = max(scale * 1e-7, 1e-8)
        inner_fixed = None
        for fraction in (0.005, 0.01, 0.02, 0.04, 0.08, 0.12):
            value = fixed_value + inward * ranges[fixed] * fraction
            if not fixed_domain[0] < value < fixed_domain[1]:
                continue
            samples = []
            for index in range(17):
                t = index / 16
                varying_value = first[varying] + (last[varying] - first[varying]) * t
                uv = [0.0, 0.0]
                uv[varying], uv[fixed] = varying_value, value
                point = surface.evaluate(uv[0], uv[1])
                if point is None:
                    samples = []
                    break
                samples.append((uv, point))
            if len(samples) != 17:
                continue
            boundary_samples = [
                surface.evaluate(
                    first[0] + (last[0] - first[0]) * index / 16,
                    first[1] + (last[1] - first[1]) * index / 16,
                )
                for index in range(17)
            ]
            if all(point is not None for point in boundary_samples) and all(
                self._distance(inside[1], edge) > separation
                for inside, edge in zip(samples, boundary_samples)
            ):
                inner_fixed = value
                break
        if inner_fixed is None:
            return None

        inner_count = max(8, min(24, max(len(ids) for index, ids in enumerate(edge_ids)
                                        if index != edge_index)))
        inner_uv: list[Point3] = []
        inner_points: list[Point3] = []
        for index in range(inner_count):
            t = index / (inner_count - 1)
            uv_values = [0.0, 0.0]
            uv_values[varying] = first[varying] + (last[varying] - first[varying]) * t
            uv_values[fixed] = inner_fixed
            point = surface.evaluate(*uv_values)
            if point is None:
                return None
            inner_uv.append(Point3(uv_values[0], uv_values[1], 0.0))
            inner_points.append(point)

        outer_t = [
            (parameters[index][varying] - first[varying]) / (last[varying] - first[varying])
            for index in outer_ids
        ]
        if any(right < left - 1e-9 for left, right in zip(outer_t, outer_t[1:])):
            if any(right > left + 1e-9 for left, right in zip(outer_t, outer_t[1:])):
                return None
            outer_t = [1.0 - value for value in outer_t]
        inner_t = [index / (inner_count - 1) for index in range(inner_count)]
        points = list(boundary) + inner_points
        uv_points = [Point3(u, v, 0.0) for u, v in parameters] + inner_uv
        inner_ids = list(range(len(boundary), len(points)))
        strip: list[tuple[int, int, int]] = []
        outer_position = inner_position = 0
        while outer_position < len(outer_ids) - 1 or inner_position < inner_count - 1:
            next_outer = outer_t[outer_position + 1] if outer_position + 1 < len(outer_ids) else math.inf
            next_inner = inner_t[inner_position + 1] if inner_position + 1 < inner_count else math.inf
            outer = outer_ids[outer_position]
            inner = inner_ids[inner_position]
            if abs(next_outer - next_inner) <= 1e-10:
                strip.extend(((outer, outer_ids[outer_position + 1], inner_ids[inner_position + 1]),
                              (outer, inner_ids[inner_position + 1], inner)))
                outer_position += 1
                inner_position += 1
            elif next_outer < next_inner:
                strip.append((outer, outer_ids[outer_position + 1], inner))
                outer_position += 1
            else:
                strip.append((outer, inner_ids[inner_position + 1], inner))
                inner_position += 1

        start = offsets[edge_index]
        if start < 2 or len(boundary) <= start + 1:
            return None
        start_corner, end_corner = 0, start
        inner_start, inner_end = inner_ids[0], inner_ids[-1]
        corner_triangles = [
            (start_corner, 1, inner_end),
            (start - 1, end_corner, inner_start),
        ]
        core_ids = [inner_end] + list(range(1, start)) + inner_ids[:-1]
        core_loop = [uv_points[index] for index in core_ids]
        core_result = self._ear_clip_loop(core_loop)
        if core_result is None:
            return None
        _, core_local_triangles = core_result
        core = [tuple(core_ids[index] for index in triangle) for triangle in core_local_triangles]
        triangles = self._consistent_uv_winding(
            tuple(uv_points), tuple(strip + corner_triangles + core),
        )
        return tuple(points), tuple(uv_points), triangles

    def _rational_trim_mesh(
        self, surface: _RationalSplineSurface, loops: list[list[Point3]],
        loop_segments: list[list[tuple[Point3, ...]]],
        protected_points: tuple[Point3, ...] = (),
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        if len(loops) != 1 or len(loop_segments[0]) != 4:
            return None
        boundary = loops[0]
        scale = max(self._distance(boundary[0], point) for point in boundary)
        tolerance = max(scale * 3e-4, 1e-5)
        parameters = []
        previous = None
        for point in boundary:
            error, u, v = surface.project(point, previous)
            if math.sqrt(error) > tolerance:
                return None
            parameters.append([u, v])
            previous = (u, v)
        # A closed parameter direction repeats the same 3-D seam at both
        # domain ends. Keep consecutive boundary samples on one UV sheet.
        for axis, domain in ((0, surface.u_range), (1, surface.v_range)):
            period = domain[1] - domain[0]
            if period <= 0:
                return None
            other = surface.v_range if axis == 0 else surface.u_range
            middle = (other[0] + other[1]) / 2
            end0 = surface.evaluate(domain[0], middle) if axis == 0 else surface.evaluate(middle, domain[0])
            end1 = surface.evaluate(domain[1], middle) if axis == 0 else surface.evaluate(middle, domain[1])
            if end0 is None or end1 is None or self._distance(end0, end1) > tolerance:
                continue
            first = parameters[0][axis]
            for pair in parameters[1:]:
                pair[axis] += period * round((first - pair[axis]) / period)
                first = pair[axis]
            midpoint = (min(pair[axis] for pair in parameters) + max(pair[axis] for pair in parameters)) / 2
            shift = period * round((midpoint - sum(domain) / 2) / period)
            for pair in parameters:
                pair[axis] -= shift
        # The STEP curves are approximations of isoparametric rails. Their
        # independent fit can jitter by a few microns, creating tiny UV folds
        # even though the original 3-D boundary is continuous. Snap each rail
        # to its constant parameter and retain monotone sample order.
        offset = 0
        count = len(parameters)
        for segment in loop_segments[0]:
            indices = [(offset + i) % count for i in range(len(segment))]
            offset += len(segment) - 1
            spans = [max(parameters[i][axis] for i in indices)
                     - min(parameters[i][axis] for i in indices) for axis in (0, 1)]
            domains = (surface.u_range, surface.v_range)
            relative = [spans[axis] / (domains[axis][1] - domains[axis][0]) for axis in (0, 1)]
            constant = 0 if relative[0] < relative[1] else 1
            if relative[constant] > 2e-3:
                return None
            fixed = (parameters[indices[0]][constant] + parameters[indices[-1]][constant]) / 2
            for i in indices:
                parameters[i][constant] = fixed
            varying = 1 - constant
            start, end = parameters[indices[0]][varying], parameters[indices[-1]][varying]
            sign = 1 if end >= start else -1
            small = (domains[varying][1] - domains[varying][0]) * 1e-10
            previous_value = sign * start
            for position, i in enumerate(indices[1:-1], start=1):
                remaining = len(indices) - 1 - position
                value = max(previous_value + small, sign * parameters[i][varying])
                value = min(value, sign * end - remaining * small)
                parameters[i][varying] = sign * value
                previous_value = value
        if offset != count:
            return None
        if any(
            (candidate := surface.evaluate(u, v)) is None
            or self._distance(candidate, point) > tolerance
            for point, (u, v) in zip(boundary, parameters)
        ):
            return None
        uv_loop = [Point3(u, v, 0.0) for u, v in parameters]
        dense_rail_mesh = self._rational_dense_rail_mesh(
            surface, tuple(boundary), parameters, loop_segments[0],
        )
        original_boundary = tuple(boundary)
        if dense_rail_mesh is None:
            triangulated = self._ear_clip_loop(uv_loop)
            if triangulated is None:
                return None
            uv_points, triangles = triangulated
            mesh_points = original_boundary
            fallback_triangles = triangles
        else:
            mesh_points, uv_points, triangles = dense_rail_mesh
            fallback = self._ear_clip_loop(uv_loop)
            fallback_triangles = fallback[1] if fallback is not None else ()
        triangles = self._consistent_uv_winding(uv_points, triangles)
        if not triangles:
            return None
        a, b, c = triangles[0]
        signed_area = ((uv_points[b].x - uv_points[a].x) * (uv_points[c].y - uv_points[a].y)
                       - (uv_points[b].y - uv_points[a].y) * (uv_points[c].x - uv_points[a].x))
        if signed_area < 0:
            triangles = tuple((a, c, b) for a, b, c in triangles)
        expected_boundary_edges = len(boundary)
        trim_interpolation_used = False
        if surface.self_intersect and dense_rail_mesh is None:
            coons = self._coons_rational_boundary_mesh(
                loop_segments[0], original_boundary, max(scale * 0.08, 1e-6),
            )
            if coons is None:
                return original_boundary, fallback_triangles
            refined_points, refined_triangles, expected_boundary_edges = coons
            trim_interpolation_used = True
        elif dense_rail_mesh is None:
            refined_points, refined_triangles = self._refine_curved_trim_mesh(
                mesh_points, uv_points, triangles, [len(boundary)],
                lambda uv: surface.evaluate(uv.x, uv.y), max(scale * 0.12, 1e-6),
            )
        else:
            refined_points, refined_triangles = self._safe_refine_rational_edges(
                surface, mesh_points, uv_points, triangles, len(original_boundary),
                max(scale * 0.08, 1e-6), max(scale * 1e-9, 1e-10), protected_points,
            )
        # A rational patch can fold or stall near an aggressive fillet. Its
        # mapped refinement points may then coincide in 3-D despite distinct
        # UV coordinates. Keep the valid boundary-only mesh in that case.
        tolerance = max(scale * 1e-9, 1e-10)
        def valid(points, faces, boundary_edge_count):
            keys = [tuple(round(getattr(point, axis) / tolerance) for axis in ("x", "y", "z"))
                    for point in points]
            edge_incidence = {}
            for triangle in faces:
                for a, b in zip(triangle, (triangle[1], triangle[2], triangle[0])):
                    first, second = keys[a], keys[b]
                    edge = (first, second) if first <= second else (second, first)
                    edge_incidence.setdefault(edge, []).append((first, second))
            return (
                sum(len(incidents) == 1 for incidents in edge_incidence.values()) == boundary_edge_count
                and all(len(incidents) <= 2 and
                        (len(incidents) != 2 or incidents[0] != incidents[1])
                        for incidents in edge_incidence.values())
            )

        if not valid(refined_points, refined_triangles, expected_boundary_edges):
            if valid(mesh_points, triangles, len(boundary)):
                refined_points, refined_triangles = mesh_points, triangles
                trim_interpolation_used = False
            else:
                return (original_boundary, fallback_triangles) if fallback_triangles else None
        surface.used_trim_interpolation = trim_interpolation_used
        return refined_points, refined_triangles

    def _coons_rational_boundary_mesh(
        self,
        segments: list[tuple[Point3, ...]],
        boundary: tuple[Point3, ...],
        target_edge_length: float,
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...], int] | None:
        """Interpolate a folded rational preview without changing shared trim edges."""
        if len(segments) != 4 or any(len(segment) < 2 for segment in segments):
            return None

        bottom, right = segments[0], segments[1]
        top, left = tuple(reversed(segments[2])), tuple(reversed(segments[3]))
        def fractions(points):
            lengths = [0.0]
            for first, second in zip(points, points[1:]):
                lengths.append(lengths[-1] + self._distance(first, second))
            if lengths[-1] <= 1e-12:
                return (0.0, 1.0)
            return tuple(value / lengths[-1] for value in lengths)

        bottom_fractions = fractions(bottom)
        right_fractions = fractions(right)
        top_fractions = fractions(top)
        left_fractions = fractions(left)
        scale = max(self._distance(bottom[0], point) for point in (*bottom, *right, *top, *left))
        tolerance = max(scale * 1e-5, 1e-8)
        p00, p10, p11, p01 = bottom[0], bottom[-1], right[-1], top[0]
        corners = ((p00, left[0]), (p10, right[0]), (p11, top[-1]), (p01, left[-1]))
        if any(self._distance(first, second) > tolerance for first, second in corners):
            return None

        def at(points, samples, t):
            if t <= 0:
                return points[0]
            if t >= 1:
                return points[-1]
            for index, (first, second) in enumerate(zip(samples, samples[1:])):
                if first <= t <= second:
                    portion = (t - first) / max(second - first, 1e-12)
                    a, b = points[index], points[index + 1]
                    return Point3(
                        a.x + (b.x - a.x) * portion,
                        a.y + (b.y - a.y) * portion,
                        a.z + (b.z - a.z) * portion,
                    )
            return points[-1]

        def coons(u, v):
            b, t = at(bottom, bottom_fractions, u), at(top, top_fractions, u)
            l, r = at(left, left_fractions, v), at(right, right_fractions, v)
            bilinear = tuple(
                (1 - u) * (1 - v) * getattr(p00, axis)
                + u * (1 - v) * getattr(p10, axis)
                + u * v * getattr(p11, axis)
                + (1 - u) * v * getattr(p01, axis)
                for axis in ("x", "y", "z")
            )
            return Point3(*(
                (1 - v) * getattr(b, axis) + v * getattr(t, axis)
                + (1 - u) * getattr(l, axis) + u * getattr(r, axis)
                - bilinear[index]
                for index, axis in enumerate(("x", "y", "z"))
            ))

        perimeter_uv = [
            *((u, 0.0) for u in bottom_fractions),
            *((1.0, v) for v in right_fractions[1:]),
            *((u, 1.0) for u in reversed(top_fractions[:-1])),
            *((0.0, v) for v in reversed(left_fractions[1:-1])),
        ]
        if len(perimeter_uv) != len(boundary):
            return None

        # Each ring follows every original trim sample, so adjoining faces keep
        # identical boundary segments. More rings resolve the tight fillet
        # without the long diagonals that folded the simple polygon fill.
        ring_count = max(16, min(40, math.ceil(scale / max(target_edge_length, 1e-9) * 2)))
        points = list(boundary)
        rings = [list(range(len(boundary)))]
        for ring_index in range(1, ring_count + 1):
            inset = 1.0 - ring_index / (ring_count + 1)
            ring = []
            for u, v in perimeter_uv:
                ring.append(len(points))
                points.append(coons(
                    0.5 + (u - 0.5) * inset,
                    0.5 + (v - 0.5) * inset,
                ))
            rings.append(ring)

        triangles = []
        count = len(boundary)
        for outer, inner in zip(rings, rings[1:]):
            for index in range(count):
                following = (index + 1) % count
                triangles.extend((
                    (outer[index], outer[following], inner[following]),
                    (outer[index], inner[following], inner[index]),
                ))
        center_id = len(points)
        points.append(coons(0.5, 0.5))
        for index in range(count):
            triangles.append((center_id, rings[-1][index], rings[-1][(index + 1) % count]))
        return tuple(points), tuple(triangles), len(boundary)

    def _safe_refine_rational_edges(
        self,
        surface: _RationalSplineSurface,
        points: tuple[Point3, ...],
        uv_points: tuple[Point3, ...],
        triangles: tuple[tuple[int, int, int], ...],
        boundary_count: int,
        target_length: float,
        tolerance: float,
        protected_points: tuple[Point3, ...],
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]]:
        """Split long interior chords when their surface points are unoccupied."""
        if len(points) != len(uv_points) or not triangles:
            return points, triangles
        boundary_edges = {
            tuple(sorted((index, (index + 1) % boundary_count)))
            for index in range(boundary_count)
        }
        refined_points = list(points)
        refined_uv = list(uv_points)
        refined_triangles = list(triangles)

        def point_key(point: Point3):
            return tuple(round(getattr(point, axis) / tolerance) for axis in ("x", "y", "z"))

        occupied = {point_key(point) for point in (*refined_points, *protected_points)}
        for _ in range(300):
            if len(refined_triangles) + 2 > self.MAX_CURVED_TRIM_TRIANGLES:
                break
            incidence: dict[tuple[int, int], list[int]] = {}
            for triangle_index, triangle in enumerate(refined_triangles):
                for first, second in zip(triangle, (triangle[1], triangle[2], triangle[0])):
                    edge = tuple(sorted((first, second)))
                    incidence.setdefault(edge, []).append(triangle_index)
            candidates = []
            for edge, incident in incidence.items():
                if edge in boundary_edges or len(incident) != 2:
                    continue
                length = self._distance(refined_points[edge[0]], refined_points[edge[1]])
                if length > target_length:
                    candidates.append((length, edge, incident))
            if not candidates:
                break
            candidates.sort(reverse=True, key=lambda item: item[0])

            selected = None
            for _, edge, incident in candidates:
                first, second = edge
                first_uv, second_uv = refined_uv[first], refined_uv[second]
                for fraction in (0.5, 0.35, 0.65, 0.25, 0.75, 0.15, 0.85):
                    uv = Point3(
                        first_uv.x + (second_uv.x - first_uv.x) * fraction,
                        first_uv.y + (second_uv.y - first_uv.y) * fraction,
                        0.0,
                    )
                    point = surface.evaluate(uv.x, uv.y)
                    if (point is None or point_key(point) in occupied
                            or min(self._distance(point, refined_points[first]),
                                   self._distance(point, refined_points[second])) <= tolerance * 4):
                        continue
                    selected = edge, incident, uv, point
                    break
                if selected is not None:
                    break
            if selected is None:
                break

            edge, incident, uv, point = selected
            middle = len(refined_points)
            refined_points.append(point)
            refined_uv.append(uv)
            occupied.add(point_key(point))
            replacements: dict[int, tuple[tuple[int, int, int], tuple[int, int, int]]] = {}
            for triangle_index in incident:
                triangle = refined_triangles[triangle_index]
                for position in range(3):
                    first, second = triangle[position], triangle[(position + 1) % 3]
                    if tuple(sorted((first, second))) == edge:
                        third = triangle[(position + 2) % 3]
                        replacements[triangle_index] = ((first, middle, third), (middle, second, third))
                        break
            if len(replacements) != 2:
                refined_points.pop()
                refined_uv.pop()
                break
            next_triangles = []
            for triangle_index, triangle in enumerate(refined_triangles):
                replacement = replacements.get(triangle_index)
                next_triangles.extend(replacement if replacement else (triangle,))
            refined_triangles = next_triangles
        return tuple(refined_points), tuple(refined_triangles)

    def _unequal_cylindrical_strip_mesh(
        self, surface: StepEntity, first: tuple[Point3, ...], second: tuple[Point3, ...]
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Join unequal samples of the same cylindrical arc without a UV fan."""

        if self._distance(first[0], second[0]) > self._distance(first[0], second[-1]):
            second = tuple(reversed(second))
        placement_id = next((_ref(argument) for argument in surface.arguments if _ref(argument) is not None), None)
        placement = self.document.entity(placement_id or -1)
        if not placement or placement.type_name.upper() != "AXIS2_PLACEMENT_3D":
            return None
        refs = [_ref(argument) for argument in placement.arguments if _ref(argument) is not None]
        origin = self._cartesian(refs[0]) if refs else None
        axis = self._normalized(self._direction(refs[1])) if len(refs) > 1 and self._direction(refs[1]) else None
        x_axis = self._normalized(self._direction(refs[2])) if len(refs) > 2 and self._direction(refs[2]) else None
        if origin is None or axis is None or x_axis is None:
            return None
        x_axis = self._normalized(tuple(x_axis[i] - self._dot(x_axis, axis) * axis[i] for i in range(3)))
        y_axis = self._normalized(self._cross(axis, x_axis)) if x_axis else None
        radius = next((_number(argument) for argument in surface.arguments if _number(argument) is not None), None)
        if x_axis is None or y_axis is None or radius is None or radius <= 0:
            return None

        def angles(rail: tuple[Point3, ...]) -> list[float] | None:
            raw = []
            for point in rail:
                delta = (point.x - origin.x, point.y - origin.y, point.z - origin.z)
                axial = self._dot(delta, axis)
                radial = tuple(delta[i] - axial * axis[i] for i in range(3))
                if abs(math.sqrt(self._dot(radial, radial)) - radius) > max(radius * 2e-4, 1e-6):
                    return None
                raw.append(math.atan2(self._dot(radial, y_axis), self._dot(radial, x_axis)))
            result = [raw[0]]
            for previous, current in zip(raw, raw[1:]):
                result.append(result[-1] + (current - previous + math.pi) % (2 * math.pi) - math.pi)
            return result

        first_angles, second_angles = angles(first), angles(second)
        if first_angles is None or second_angles is None:
            return None
        if first_angles[-1] < first_angles[0]:
            first, second = tuple(reversed(first)), tuple(reversed(second))
            first_angles, second_angles = angles(first), angles(second)
        shift = 2 * math.pi * round((first_angles[0] - second_angles[0]) / (2 * math.pi))
        second_angles = [value + shift for value in second_angles]
        if (abs(first_angles[0] - second_angles[0]) > 1e-5
                or abs(first_angles[-1] - second_angles[-1]) > 1e-5
                or first_angles[-1] - first_angles[0] >= 2 * math.pi - 1e-6
                or any(rail[i + 1] <= rail[i] + 1e-9 for rail in (first_angles, second_angles)
                       for i in range(len(rail) - 1))):
            return None

        points = first + second
        offset = len(first)
        triangles = []
        i = j = 0
        while i + 1 < len(first) or j + 1 < len(second):
            next_first = first_angles[i + 1] if i + 1 < len(first) else math.inf
            next_second = second_angles[j + 1] if j + 1 < len(second) else math.inf
            if next_first <= next_second + 1e-8:
                triangles.append((i, i + 1, offset + j))
                i += 1
            if next_second <= next_first + 1e-8:
                triangles.append((i, offset + j + 1, offset + j))
                j += 1
        return points, tuple(triangles)

    def _loft_spline_mesh(
        self, surface: StepEntity, loop_segments: list[list[tuple[Point3, ...]]]
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Evaluate supported rectangular trims of tensor-product spline faces.

        The four STEP edge curves supply the exact boundary samples. Interior
        points come from the tensor-product surface, never its control cage.
        Closed lofts use knot-aligned half trims. Open ruled patches are
        accepted only when their trim rails match the complete surface domain
        and the surface is linear in its across-rail parameter.
        """
        if len(loop_segments) != 1 or len(loop_segments[0]) != 4 or len(surface.arguments) < 13:
            return None
        u_closed, v_closed = surface.arguments[5:7]
        if not isinstance(u_closed, StepEnumeration) or not isinstance(v_closed, StepEnumeration):
            return None
        closed_loft = u_closed.value.upper() == "T" and v_closed.value.upper() == "F"
        open_ruled = u_closed.value.upper() == "F" and v_closed.value.upper() != "T"
        if open_ruled:
            ruled = self._open_ruled_spline_mesh(surface, loop_segments)
            if ruled is not None:
                return ruled
            return self._trimmed_spline_surface_mesh(surface, loop_segments)
        if not closed_loft:
            return None
        degrees = [_number(surface.arguments[index]) for index in (1, 2)]
        if any(value is None or value < 1 or not value.is_integer() for value in degrees):
            return None
        u_degree, v_degree = (int(value) for value in degrees)
        source_rows = [_aggregate(row) for row in _aggregate(surface.arguments[3])]
        if len(source_rows) <= u_degree or not source_rows or any(len(row) != len(source_rows[0]) for row in source_rows):
            return None
        if len(source_rows[0]) <= v_degree:
            return None
        control_rows = [[self._cartesian(_ref(value) or -1) for value in row] for row in source_rows]
        if any(point is None for row in control_rows for point in row):
            return None

        def expanded_knots(multiplicity_index: int, value_index: int, count: int, degree: int) -> list[float] | None:
            multiplicities = [_number(value) for value in _aggregate(surface.arguments[multiplicity_index])]
            values = [_number(value) for value in _aggregate(surface.arguments[value_index])]
            if (len(multiplicities) != len(values) or not values
                    or any(value is None or not math.isfinite(value) for value in values)
                    or any(value is None or value < 1 or not value.is_integer() for value in multiplicities)):
                return None
            knots = [float(value) for mult, value in zip(multiplicities, values) for _ in range(int(mult))]
            if len(knots) != count + degree + 1 or any(a > b for a, b in zip(knots, knots[1:])):
                return None
            if knots[degree] >= knots[count]:
                return None
            return knots

        u_knots = expanded_knots(8, 10, len(control_rows), u_degree)
        v_knots = expanded_knots(9, 11, len(control_rows[0]), v_degree)
        if u_knots is None or v_knots is None:
            return None

        def basis(parameter: float, degree: int, knots: list[float], count: int) -> list[float]:
            if parameter >= knots[count]:
                return [0.0] * (count - 1) + [1.0]
            values = [1.0 if knots[index] <= parameter < knots[index + 1] else 0.0
                      for index in range(count + degree)]
            for level in range(1, degree + 1):
                values = [
                    ((parameter - knots[index]) * values[index] / (knots[index + level] - knots[index])
                     if knots[index + level] > knots[index] else 0.0)
                    + ((knots[index + level + 1] - parameter) * values[index + 1]
                       / (knots[index + level + 1] - knots[index + 1])
                       if knots[index + level + 1] > knots[index + 1] else 0.0)
                    for index in range(count + degree - level)
                ]
            return values[:count]

        def evaluate(u: float, v: float) -> Point3:
            u_basis = basis(u, u_degree, u_knots, len(control_rows))
            v_basis = basis(v, v_degree, v_knots, len(control_rows[0]))
            return Point3(*(
                sum(u_basis[i] * v_basis[j] * getattr(control_rows[i][j], coord)
                    for i in range(len(control_rows)) for j in range(len(control_rows[0])))
                for coord in ("x", "y", "z")
            ))

        def samples(knots: list[float], start: float, end: float) -> list[float]:
            breaks = sorted({value for value in knots if start - 1e-10 <= value <= end + 1e-10})
            if not breaks or abs(breaks[0] - start) > 1e-8 or abs(breaks[-1] - end) > 1e-8:
                return []
            result = [start]
            for left, right in zip(breaks, breaks[1:]):
                if right - left <= 1e-12:
                    continue
                result.extend(left + (right - left) * step / 4 for step in range(1, 5))
            return result

        u_start, u_end = u_knots[u_degree], u_knots[len(control_rows)]
        v_start, v_end = v_knots[v_degree], v_knots[len(control_rows[0])]
        segments = loop_segments[0]
        scale = max(self._distance(point, segments[0][0]) for segment in segments for point in segment)
        tolerance = max(scale * 1e-5, 1e-6)
        u_middle = (u_start + u_end) / 2
        if not any(abs(value - u_middle) < 1e-8 for value in u_knots):
            return None
        v_values = samples(v_knots, v_start, v_end)
        best = None
        for low, high in ((u_start, u_middle), (u_middle, u_end)):
            current_u_values = samples(u_knots, low, high)
            if not current_u_values:
                continue
            targets = [
                tuple(evaluate(u, v_start) for u in current_u_values),
                tuple(evaluate(u, v_end) for u in current_u_values),
                tuple(evaluate(low, v) for v in v_values),
                tuple(evaluate(high, v) for v in v_values),
            ]
            arcs = [index for index, segment in enumerate(segments) if len(segment) == len(current_u_values)]
            seams = [index for index, segment in enumerate(segments) if len(segment) == len(v_values)]
            if len(arcs) != 2 or len(seams) != 2 or len(set(arcs + seams)) != 4:
                continue
            for arc_order in permutations(arcs):
                for seam_order in permutations(seams):
                    rails = []
                    errors = []
                    for index, target in zip((*arc_order, *seam_order), targets):
                        source = segments[index]
                        choices = (source, tuple(reversed(source)))
                        rail = min(choices, key=lambda choice: sum(self._distance(a, b) ** 2 for a, b in zip(choice, target)))
                        rails.append(rail)
                        errors.extend(self._distance(a, b) for a, b in zip(rail, target))
                    if max(errors) > tolerance:
                        continue
                    score = sum(error * error for error in errors)
                    if best is None or score < best[0]:
                        best = score, current_u_values, v_values, rails
        if best is None:
            return None
        _, u_values, v_values, rails = best
        bottom, top, left, right = rails
        width = len(u_values)
        points = []
        for row, v in enumerate(v_values):
            for column, u in enumerate(u_values):
                if row == 0:
                    point = bottom[column]
                elif row == len(v_values) - 1:
                    point = top[column]
                elif column == 0:
                    point = left[row]
                elif column == width - 1:
                    point = right[row]
                else:
                    point = evaluate(u, v)
                points.append(point)
        triangles = []
        for row in range(len(v_values) - 1):
            for column in range(width - 1):
                a = row * width + column
                b, c, d = a + 1, a + width, a + width + 1
                triangles.extend(((a, b, d), (a, d, c)))
        perimeter = set()
        for row in range(len(v_values) - 1):
            for column in (0, width - 1):
                a = row * width + column
                perimeter.add((a, a + width))
        for row in (0, len(v_values) - 1):
            for column in range(width - 1):
                a = row * width + column
                perimeter.add((a, a + 1))
        uv_points = tuple(Point3(u, v, 0.0) for v in v_values for u in u_values)
        return self._refine_curved_trim_mesh(
            tuple(points), uv_points, tuple(triangles), [],
            lambda parameter: evaluate(parameter.x, parameter.y),
            max(scale * 0.07, 1e-6), boundary_edges=perimeter,
        )

    def _trimmed_spline_surface_mesh(
        self, surface: StepEntity, loop_segments: list[list[tuple[Point3, ...]]]
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Triangulate a four-sided, iso-parametric trim on an open B-spline.

        Some STEP exporters trim an otherwise ordinary tensor-product surface
        to a parameter rectangle that covers only part of its domain. Project
        the original STEP rails to UV, retain their 3-D samples as the mesh
        boundary, and evaluate only the interior. Reject trims that do not map
        to two pairs of monotone iso-parametric rails.
        """
        if (len(loop_segments) != 1 or len(loop_segments[0]) != 4
                or len(surface.arguments) < 13):
            return None
        degrees = [_number(surface.arguments[index]) for index in (1, 2)]
        if any(value is None or value < 1 or not value.is_integer() for value in degrees):
            return None
        degrees = tuple(int(value) for value in degrees)
        rows = [_aggregate(row) for row in _aggregate(surface.arguments[3])]
        if (not rows or len(rows) <= degrees[0]
                or any(len(row) != len(rows[0]) for row in rows)
                or len(rows[0]) <= degrees[1]):
            return None
        controls = tuple(
            tuple(self._cartesian(_ref(value) or -1) for value in row)
            for row in rows
        )
        if any(point is None for row in controls for point in row):
            return None

        knots = []
        for axis, count in enumerate((len(rows), len(rows[0]))):
            multiplicities = [_number(value) for value in _aggregate(surface.arguments[8 + axis])]
            values = [_number(value) for value in _aggregate(surface.arguments[10 + axis])]
            if (not values or len(values) != len(multiplicities)
                    or any(value is None or not value.is_integer() or value < 1 for value in multiplicities)
                    or any(value is None or not math.isfinite(value) for value in values)):
                return None
            expanded = tuple(
                float(value) for multiplicity, value in zip(multiplicities, values)
                for _ in range(int(multiplicity))
            )
            if (len(expanded) != count + degrees[axis] + 1
                    or any(first > second for first, second in zip(expanded, expanded[1:]))
                    or expanded[degrees[axis]] >= expanded[count]):
                return None
            knots.append(expanded)

        evaluator = _RationalSplineSurface(
            controls,
            tuple(tuple(1.0 for _ in row) for row in controls),
            degrees,
            tuple(knots),
        )
        segments = loop_segments[0]
        boundary = []
        segment_ranges = []
        scale = max(
            self._distance(first, second)
            for segment in segments for first in segment for second in segment
        )
        tolerance = max(scale * 3e-4, 1e-5)
        domain_spans = (
            evaluator.u_range[1] - evaluator.u_range[0],
            evaluator.v_range[1] - evaluator.v_range[0],
        )
        if min(domain_spans) <= 0:
            return None

        previous_uv = None
        for segment in segments:
            if len(segment) < 2:
                return None
            projected = []
            for point in segment:
                error, u, v = evaluator.project(point, previous_uv)
                if math.sqrt(error) > tolerance:
                    return None
                projected.append((u, v))
                previous_uv = (u, v)
                if not boundary or point != boundary[-1]:
                    boundary.append(point)
            spans = tuple(
                max(item[axis] for item in projected) - min(item[axis] for item in projected)
                for axis in (0, 1)
            )
            relative = tuple(spans[axis] / domain_spans[axis] for axis in (0, 1))
            constant_axis = 0 if relative[0] < relative[1] else 1
            varying_axis = 1 - constant_axis
            # STEP edge curves are often fitted independently from their
            # supporting surface. Permit small UV drift while requiring the
            # edge to remain strongly dominated by one parameter direction.
            if relative[constant_axis] > 2.5e-2 or relative[varying_axis] < 1e-5:
                return None
            varying_values = [item[varying_axis] for item in projected]
            ascending = all(a <= b + domain_spans[varying_axis] * 1e-6
                            for a, b in zip(varying_values, varying_values[1:]))
            descending = all(a >= b - domain_spans[varying_axis] * 1e-6
                             for a, b in zip(varying_values, varying_values[1:]))
            if not ascending and not descending:
                return None
            segment_ranges.append((constant_axis, sum(item[constant_axis] for item in projected) / len(projected),
                                   min(varying_values), max(varying_values)))

        # Remove duplicated segment junctions; retain all original samples on
        # every edge so adjacent faces continue to share identical boundaries.
        if len(boundary) > 1 and boundary[0] == boundary[-1]:
            boundary.pop()
        by_axis = {axis: [item for item in segment_ranges if item[0] == axis] for axis in (0, 1)}
        if any(len(by_axis[axis]) != 2 for axis in (0, 1)):
            return None
        for axis in (0, 1):
            first, second = by_axis[axis]
            if abs(first[1] - second[1]) <= domain_spans[axis] * 1e-5:
                return None
            if max(abs(first[2] - second[2]), abs(first[3] - second[3])) > domain_spans[1 - axis] * 2.5e-2:
                return None

        uv_boundary = []
        previous_uv = None
        for point in boundary:
            error, u, v = evaluator.project(point, previous_uv)
            if math.sqrt(error) > tolerance:
                return None
            uv_boundary.append(Point3(u, v, 0.0))
            previous_uv = (u, v)
        triangulated = self._ear_clip_loop(uv_boundary)
        if triangulated is None:
            return None
        # A generic ear clip of a rectangle with many collinear STEP rail
        # samples tends to produce a fan from one corner. Start with an
        # interior fan instead; refinement then splits interior spokes while
        # preserving every original trim segment.
        center_uv = Point3(
            sum(point.x for point in uv_boundary) / len(uv_boundary),
            sum(point.y for point in uv_boundary) / len(uv_boundary),
            0.0,
        )
        center = evaluator.evaluate(center_uv.x, center_uv.y)
        if center is None:
            return None
        uv_points = tuple(uv_boundary) + (center_uv,)
        center_id = len(uv_boundary)
        triangles = tuple(
            (index, (index + 1) % center_id, center_id)
            for index in range(center_id)
        )
        reference = triangulated[1][0]
        reference_area = (
            (uv_boundary[reference[1]].x - uv_boundary[reference[0]].x)
            * (uv_boundary[reference[2]].y - uv_boundary[reference[0]].y)
            - (uv_boundary[reference[1]].y - uv_boundary[reference[0]].y)
            * (uv_boundary[reference[2]].x - uv_boundary[reference[0]].x)
        )
        raw_areas = [
            (uv_points[b].x - uv_points[a].x) * (uv_points[c].y - uv_points[a].y)
            - (uv_points[b].y - uv_points[a].y) * (uv_points[c].x - uv_points[a].x)
            for a, b, c in triangles
        ]
        if (not raw_areas or min(abs(area) for area in raw_areas) <= 1e-12
                or any(area * raw_areas[0] <= 0 for area in raw_areas[1:])):
            return None
        if raw_areas[0] * reference_area < 0:
            triangles = tuple((a, c, b) for a, b, c in triangles)
        points = tuple(boundary) + (center,)
        boundary_edges = {
            tuple(sorted((index, (index + 1) % center_id)))
            for index in range(center_id)
        }
        points, triangles = self._refine_curved_trim_mesh(
            points, uv_points, triangles, [len(boundary)],
            lambda parameter: evaluator.evaluate(parameter.x, parameter.y),
            max(scale * 0.12, 1e-6), boundary_edges=boundary_edges,
        )
        return points, triangles

    def _open_ruled_spline_mesh(
        self, surface: StepEntity, loop_segments: list[list[tuple[Point3, ...]]]
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Mesh a single-span open spline patch ruled linearly across two rails.

        Preserve the STEP samples on both curved rails and leave the two shared
        straight trim edges unsplit. The narrow path is accepted only when the
        source surface confirms that interpolation across the patch is linear.
        """
        if len(loop_segments) != 1 or len(loop_segments[0]) != 4 or len(surface.arguments) < 13:
            return None
        if not (
            isinstance(surface.arguments[5], StepEnumeration)
            and surface.arguments[5].value.upper() == "F"
            and isinstance(surface.arguments[6], StepEnumeration)
            and surface.arguments[6].value.upper() != "T"
        ):
            return None
        degree_u, degree_v = (_number(surface.arguments[index]) for index in (1, 2))
        if any(value is None or value < 1 or not value.is_integer() for value in (degree_u, degree_v)):
            return None
        degree_u, degree_v = int(degree_u), int(degree_v)
        source_rows = [_aggregate(row) for row in _aggregate(surface.arguments[3])]
        if (not source_rows or len(source_rows) <= degree_u
                or any(len(row) != len(source_rows[0]) for row in source_rows)
                or len(source_rows[0]) <= degree_v):
            return None
        controls = tuple(
            tuple(self._cartesian(_ref(value) or -1) for value in row)
            for row in source_rows
        )
        if any(point is None for row in controls for point in row):
            return None

        def expanded_knots(multiplicity_index: int, value_index: int, count: int, degree: int):
            multiplicities = [_number(value) for value in _aggregate(surface.arguments[multiplicity_index])]
            values = [_number(value) for value in _aggregate(surface.arguments[value_index])]
            if (not values or len(multiplicities) != len(values)
                    or any(value is None or value < 1 or not value.is_integer() for value in multiplicities)
                    or any(value is None or not math.isfinite(value) for value in values)):
                return None
            knots = [float(knot) for mult, knot in zip(multiplicities, values) for _ in range(int(mult))]
            if (len(knots) != count + degree + 1
                    or any(first > second for first, second in zip(knots, knots[1:]))):
                return None
            return knots

        u_knots = expanded_knots(8, 10, len(controls), degree_u)
        v_knots = expanded_knots(9, 11, len(controls[0]), degree_v)
        if u_knots is None or v_knots is None or len(set(u_knots)) != 2 or len(set(v_knots)) != 2:
            return None
        u_start, u_end = u_knots[degree_u], u_knots[len(controls)]
        v_start, v_end = v_knots[degree_v], v_knots[len(controls[0])]

        def evaluate(u: float, v: float) -> Point3:
            u_basis = _RationalSplineSurface._basis(u, degree_u, u_knots, len(controls))
            v_basis = _RationalSplineSurface._basis(v, degree_v, v_knots, len(controls[0]))
            return Point3(*(
                sum(
                    u_basis[i] * v_basis[j] * getattr(controls[i][j], axis)
                    for i in range(len(controls)) for j in range(len(controls[0]))
                )
                for axis in ("x", "y", "z")
            ))

        segments = loop_segments[0]
        long_indices = [index for index, segment in enumerate(segments) if len(segment) > 2]
        line_indices = [index for index, segment in enumerate(segments) if len(segment) == 2]
        if len(long_indices) != 2 or len(line_indices) != 2:
            return None
        if len(segments[long_indices[0]]) != len(segments[long_indices[1]]):
            return None
        scale = max(
            self._distance(first, second)
            for first in segments[0] for second in segments[1]
        )
        tolerance = max(scale * 1e-5, 1e-6)
        rail_count = len(segments[long_indices[0]])

        def project_to_rail(point: Point3, u: float) -> tuple[float, float]:
            # Coarse search selects the right local minimum; a golden-section
            # search then finds the corresponding surface parameter accurately.
            divisions = max(32, (rail_count - 1) * 2)
            step = (v_end - v_start) / divisions
            parameters = [v_start + step * index for index in range(divisions + 1)]

            def distance_squared(parameter: float) -> float:
                return self._distance(point, evaluate(u, parameter)) ** 2

            nearest = min(range(len(parameters)), key=lambda index: distance_squared(parameters[index]))
            low = parameters[max(0, nearest - 1)]
            high = parameters[min(divisions, nearest + 1)]
            ratio = (math.sqrt(5.0) - 1.0) / 2.0
            left = high - ratio * (high - low)
            right = low + ratio * (high - low)
            left_distance, right_distance = distance_squared(left), distance_squared(right)
            for _ in range(20):
                if left_distance <= right_distance:
                    high, right, right_distance = right, left, left_distance
                    left = high - ratio * (high - low)
                    left_distance = distance_squared(left)
                else:
                    low, left, left_distance = left, right, right_distance
                    right = low + ratio * (high - low)
                    right_distance = distance_squared(right)
            parameter = (low + high) / 2
            return parameter, math.sqrt(distance_squared(parameter))

        rail_candidates = {}
        for segment_index in long_indices:
            source = segments[segment_index]
            for side, u in (("left", u_start), ("right", u_end)):
                projected = [project_to_rail(point, u) for point in source]
                parameters = [item[0] for item in projected]
                ascending = all(a <= b + 1e-9 for a, b in zip(parameters, parameters[1:]))
                descending = all(a >= b - 1e-9 for a, b in zip(parameters, parameters[1:]))
                if not ascending and not descending:
                    continue
                points = tuple(source if ascending else reversed(source))
                parameters = tuple(parameters if ascending else reversed(parameters))
                error = max(item[1] for item in projected)
                if error <= tolerance:
                    rail_candidates[(segment_index, side)] = (points, parameters, error)

        best_rails = None
        for first_index, second_index in permutations(long_indices):
            first = rail_candidates.get((first_index, "left"))
            second = rail_candidates.get((second_index, "right"))
            if first is None or second is None:
                continue
            parameter_error = max(abs(a - b) for a, b in zip(first[1], second[1]))
            if parameter_error > max((v_end - v_start) * 1e-5, 1e-8):
                continue
            score = first[2] ** 2 + second[2] ** 2 + parameter_error ** 2 * scale ** 2
            if best_rails is None or score < best_rails[0]:
                best_rails = score, first, second
        if best_rails is None:
            return None
        _, left_rail, right_rail = best_rails
        v_values = tuple((a + b) / 2 for a, b in zip(left_rail[1], right_rail[1]))
        if (v_values[0] > v_start + 1e-5 or v_values[-1] < v_end - 1e-5
                or any(a >= b for a, b in zip(v_values, v_values[1:]))):
            return None

        # Verify that both line trims match the two parameter-domain ends.
        corner_pairs = (
            ((evaluate(u_start, v_start), evaluate(u_end, v_start)),
             (evaluate(u_start, v_end), evaluate(u_end, v_end))),
        )
        line_choices = []
        for line_index in line_indices:
            line = segments[line_index]
            options = []
            for boundary_index, expected in enumerate(corner_pairs[0]):
                for oriented in (line, tuple(reversed(line))):
                    error = max(self._distance(a, b) for a, b in zip(oriented, expected))
                    options.append((error, boundary_index))
            line_choices.append(sorted(options)[0])
        if (line_choices[0][1] == line_choices[1][1]
                or max(item[0] for item in line_choices) > tolerance):
            return None

        # The two-column mesh is faithful only if the declared spline surface
        # is linear between its boundary rails across u.
        for v in v_values:
            first, last = evaluate(u_start, v), evaluate(u_end, v)
            for fraction in (0.25, 0.5, 0.75):
                actual = evaluate(u_start + (u_end - u_start) * fraction, v)
                expected = Point3(*(
                    getattr(first, axis) + (getattr(last, axis) - getattr(first, axis)) * fraction
                    for axis in ("x", "y", "z")
                ))
                if self._distance(actual, expected) > max(scale * 1e-5, 1e-8):
                    return None

        points = tuple(
            point
            for left, right in zip(left_rail[0], right_rail[0])
            for point in (left, right)
        )
        width = 2
        triangles = []
        for row in range(rail_count - 1):
            a = row * width
            b, c, d = a + 1, a + width, a + width + 1
            triangles.extend(((a, b, d), (a, d, c)))
        return points, tuple(triangles)

    def _conical_apex_fan(
        self, surface: StepEntity, loop: list[Point3]
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Tessellate a cone sector whose boundary includes its apex.

        Such a sector commonly has two adjacent circular arcs around its rim
        and two straight generators meeting at the apex.  Treating the arcs
        as opposite rails of a ruled strip drops the apex and folds the face
        across itself.  A fan is the natural tessellation for this topology.

        The apex can be offset from the surface placement when the reference
        radius is nonzero. Frustums and other bounded conical patches continue
        through the general ruled-surface path.
        """

        numbers = [_number(argument) for argument in surface.arguments if _number(argument) is not None]
        if len(numbers) < 2:
            return None
        reference_radius, semi_angle = numbers[:2]
        tangent = math.tan(semi_angle)
        if abs(tangent) <= 1e-12:
            return None
        placement_id = next((_ref(argument) for argument in surface.arguments if _ref(argument) is not None), None)
        placement = self.document.entity(placement_id or -1)
        if not placement or placement.type_name.upper() != "AXIS2_PLACEMENT_3D":
            return None
        origin_id = next((_ref(argument) for argument in placement.arguments if _ref(argument) is not None), None)
        origin = self._cartesian(origin_id or -1)
        axis_id = next((_ref(argument) for argument in placement.arguments[2:] if _ref(argument) is not None), None)
        axis = self._normalized(self._direction(axis_id)) if axis_id is not None else None
        if origin is None or axis is None:
            return None
        apex_axial = -reference_radius / tangent
        apex = Point3(*(
            getattr(origin, coordinate) + apex_axial * axis[index]
            for index, coordinate in enumerate(("x", "y", "z"))
        ))

        extent = max((self._distance(apex, point) for point in loop), default=0.0)
        tolerance = max(extent * 1e-9, 1e-10)
        surface_tolerance = max(extent * 2e-4, 1e-6)
        for point in loop:
            delta = (point.x - origin.x, point.y - origin.y, point.z - origin.z)
            axial = self._dot(delta, axis)
            radial = tuple(delta[index] - axial * axis[index] for index in range(3))
            if abs(math.sqrt(self._dot(radial, radial)) - abs(reference_radius + axial * tangent)) > surface_tolerance:
                return None
        apex_indices = [index for index, point in enumerate(loop) if self._distance(apex, point) <= tolerance]
        if len(apex_indices) != 1:
            return None

        apex_index = apex_indices[0]
        ordered = loop[apex_index:] + loop[:apex_index]
        points = tuple(ordered)
        triangles = tuple((0, index, index + 1) for index in range(1, len(points) - 1))
        return (points, triangles) if triangles else None

    def _closed_analytic_surface_mesh(
        self, face: StepEntity, surface: StepEntity | None
    ) -> Mesh | None:
        """Tessellate an unbounded closed sphere or torus without healing it."""

        if surface is None or surface.type_name.upper() not in {"SPHERICAL_SURFACE", "TOROIDAL_SURFACE"}:
            return None
        placement_id = next((_ref(arg) for arg in surface.arguments if _ref(arg) is not None), None)
        placement = self.document.entity(placement_id or -1)
        if not placement or placement.type_name.upper() != "AXIS2_PLACEMENT_3D":
            return None
        refs = [_ref(arg) for arg in placement.arguments if _ref(arg) is not None]
        origin = self._cartesian(refs[0]) if refs else None
        axis = self._normalized(self._direction(refs[1]) or (0.0, 0.0, 1.0)) if len(refs) > 1 else (0.0, 0.0, 1.0)
        x_axis = self._normalized(self._direction(refs[2])) if len(refs) > 2 and self._direction(refs[2]) else None
        if origin is None or axis is None:
            return None
        if x_axis is None:
            seed = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 1.0, 0.0)
            x_axis = self._normalized(self._cross(seed, axis))
        y_axis = self._normalized(self._cross(axis, x_axis)) if x_axis else None
        numbers = [_number(arg) for arg in surface.arguments if _number(arg) is not None]
        if x_axis is None or y_axis is None or not numbers:
            return None

        def point(radial: float, axial: float, angle: float) -> Point3:
            return Point3(*(
                getattr(origin, coord) + axial * axis[index]
                + radial * (math.cos(angle) * x_axis[index] + math.sin(angle) * y_axis[index])
                for index, coord in enumerate(("x", "y", "z"))
            ))

        around = 72
        points: list[Point3] = []
        triangles: list[tuple[int, int, int]] = []
        if surface.type_name.upper() == "SPHERICAL_SURFACE":
            radius, rings = numbers[0], 36
            points.append(point(0.0, radius, 0.0))
            for ring in range(1, rings):
                latitude = math.pi / 2 - math.pi * ring / rings
                for column in range(around):
                    points.append(point(radius * math.cos(latitude), radius * math.sin(latitude), 2 * math.pi * column / around))
            south = len(points)
            points.append(point(0.0, -radius, 0.0))
            for column in range(around):
                following = (column + 1) % around
                triangles.append((0, 1 + column, 1 + following))
                for ring in range(rings - 2):
                    row = 1 + ring * around
                    next_row = row + around
                    triangles.extend(((row + column, next_row + column, next_row + following),
                                      (row + column, next_row + following, row + following)))
                last_row = 1 + (rings - 2) * around
                triangles.append((last_row + column, south, last_row + following))
        else:
            if len(numbers) < 2:
                return None
            major, minor, tube_steps = numbers[0], numbers[1], 36
            for tube in range(tube_steps):
                tube_angle = 2 * math.pi * tube / tube_steps
                for column in range(around):
                    angle = 2 * math.pi * column / around
                    points.append(point(major + minor * math.cos(tube_angle), minor * math.sin(tube_angle), angle))
            for tube in range(tube_steps):
                next_tube = (tube + 1) % tube_steps
                for column in range(around):
                    following = (column + 1) % around
                    a, b = tube * around + column, tube * around + following
                    c, d = next_tube * around + column, next_tube * around + following
                    triangles.extend(((a, c, d), (a, d, b)))
        mesh_points = tuple(points)
        return Mesh(
            face.entity_id, mesh_points,
            self._oriented_face_triangles(face, surface, mesh_points, tuple(triangles)),
            curved=True,
            **self._face_metadata(face, surface),
        )

    def _toroidal_patch_mesh(
        self, surface: StepEntity, loop: list[Point3]
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Tessellate a rectangular patch of a toroidal surface.

        A constant-radius fillet is normally represented as a torus trimmed by
        four circular arcs.  Its boundary is non-planar, so a contour
        triangulator can only make a projected, folded approximation and may
        omit much of the face.  Recognise the common four-sided parameter-space
        rectangle and sample the torus itself instead.  Other trim topologies
        deliberately retain the generic fallback below.
        """

        numbers = [_number(argument) for argument in surface.arguments if _number(argument) is not None]
        if len(numbers) < 2:
            return None
        placement_id = next((_ref(argument) for argument in surface.arguments if _ref(argument) is not None), None)
        placement = self.document.entity(placement_id or -1)
        if not placement or placement.type_name.upper() != "AXIS2_PLACEMENT_3D":
            return None
        refs = [_ref(argument) for argument in placement.arguments if _ref(argument) is not None]
        origin = self._cartesian(refs[0]) if refs else None
        axis = self._normalized(self._direction(refs[1]) or (0.0, 0.0, 1.0)) if len(refs) > 1 else (0.0, 0.0, 1.0)
        x_axis = self._normalized(self._direction(refs[2])) if len(refs) > 2 and self._direction(refs[2]) else None
        if origin is None or axis is None:
            return None
        if x_axis is None:
            seed = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 1.0, 0.0)
            x_axis = self._normalized(self._cross(seed, axis))
        y_axis = self._normalized(self._cross(axis, x_axis)) if x_axis else None
        if y_axis is None:
            return None

        major_radius, minor_radius = numbers[:2]
        if minor_radius <= 1e-12:
            return None

        def parameters(point: Point3) -> tuple[float, float, float]:
            delta = (point.x - origin.x, point.y - origin.y, point.z - origin.z)
            axial = self._dot(delta, axis)
            radial = tuple(delta[index] - axial * axis[index] for index in range(3))
            radial_length = math.sqrt(self._dot(radial, radial))
            return (
                math.atan2(self._dot(radial, y_axis), self._dot(radial, x_axis)),
                math.atan2(axial, radial_length - major_radius),
                math.hypot(radial_length - major_radius, axial),
            )

        def interval(values: list[float]) -> tuple[float, float]:
            """Return the smallest continuous angular interval containing values."""
            normalized = sorted(value % (2 * math.pi) for value in values)
            gaps = [
                (normalized[(index + 1) % len(normalized)] - value) % (2 * math.pi)
                for index, value in enumerate(normalized)
            ]
            gap_index = max(range(len(gaps)), key=gaps.__getitem__)
            start = normalized[(gap_index + 1) % len(normalized)]
            return start, start + 2 * math.pi - gaps[gap_index]

        parameters_by_point = [parameters(point) for point in loop]
        if len(parameters_by_point) < 4:
            return None
        surface_tolerance = max(abs(major_radius), abs(minor_radius), 1.0) * 1e-6
        if any(abs(radius - minor_radius) > surface_tolerance for _, _, radius in parameters_by_point):
            return None
        u_start, u_end = interval([value[0] for value in parameters_by_point])
        v_start, v_end = interval([value[1] for value in parameters_by_point])
        u_span, v_span = u_end - u_start, v_end - v_start
        if u_span <= 1e-9 or v_span <= 1e-9 or u_span >= 2 * math.pi - 1e-9 or v_span >= 2 * math.pi - 1e-9:
            return None

        def unwrap(value: float, start: float) -> float:
            return value + 2 * math.pi * math.ceil((start - value) / (2 * math.pi))

        # Only accept a true parameter-space rectangle.  This prevents a
        # rounded or otherwise irregular torus trim from being expanded into
        # its enclosing rectangle.
        tolerance = 1e-6
        for u, v, _ in parameters_by_point:
            u, v = unwrap(u, u_start), unwrap(v, v_start)
            if min(abs(u - u_start), abs(u - u_end), abs(v - v_start), abs(v - v_end)) > tolerance:
                return None

        u_steps = max(1, math.ceil(u_span / (math.pi / 18)))
        v_steps = max(1, math.ceil(v_span / (math.pi / 18)))

        def point(u: float, v: float) -> Point3:
            radial = major_radius + minor_radius * math.cos(v)
            return Point3(*(
                getattr(origin, coord)
                + radial * (math.cos(u) * x_axis[index] + math.sin(u) * y_axis[index])
                + minor_radius * math.sin(v) * axis[index]
                for index, coord in enumerate(("x", "y", "z"))
            ))

        points = tuple(
            point(u_start + u_span * column / u_steps, v_start + v_span * row / v_steps)
            for row in range(v_steps + 1)
            for column in range(u_steps + 1)
        )
        triangles = []
        width = u_steps + 1
        for row in range(v_steps):
            for column in range(u_steps):
                a = row * width + column
                b, c, d = a + 1, a + width, a + width + 1
                triangles.extend(((a, c, d), (a, d, b)))
        return points, tuple(triangles)

    def _cylindrical_trim_mesh(
        self, surface: StepEntity, loops: list[list[Point3]]
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Triangulate closed cylindrical trims in their unwrapped UV plane.

        This handles irregular polygonal trims made from several circular and
        generator edges. Full-turn rims and seam-crossing openings use a
        separate periodic strip triangulator below.
        """

        if not loops:
            return None
        numbers = [_number(argument) for argument in surface.arguments if _number(argument) is not None]
        if not numbers or numbers[0] <= 1e-12:
            return None
        radius = numbers[0]
        placement_id = next(
            (_ref(argument) for argument in surface.arguments if _ref(argument) is not None),
            None,
        )
        placement = self.document.entity(placement_id or -1)
        if not placement or placement.type_name.upper() != "AXIS2_PLACEMENT_3D":
            return None
        refs = [_ref(argument) for argument in placement.arguments if _ref(argument) is not None]
        origin = self._cartesian(refs[0]) if refs else None
        axis = self._normalized(self._direction(refs[1])) if len(refs) > 1 and self._direction(refs[1]) else None
        x_axis = self._normalized(self._direction(refs[2])) if len(refs) > 2 and self._direction(refs[2]) else None
        if origin is None or axis is None:
            return None
        if x_axis is None:
            seed = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 1.0, 0.0)
            x_axis = self._normalized(self._cross(seed, axis))
        else:
            x_axis = self._normalized(
                tuple(x_axis[index] - self._dot(x_axis, axis) * axis[index] for index in range(3))
            )
        y_axis = self._normalized(self._cross(axis, x_axis)) if x_axis else None
        if x_axis is None or y_axis is None:
            return None

        # STEP exporters may encode a trim curve as a B-spline approximation
        # to an analytic cylinder.  Accept its small scale-relative residual
        # while keeping the shared boundary samples identical across faces.
        tolerance = max(abs(radius) * 2e-4, 1e-6)
        uv_loops: list[list[Point3]] = []
        for loop in loops:
            if len(loop) < 3:
                return None
            parameters = []
            raw_angles = []
            for point in loop:
                delta = (point.x - origin.x, point.y - origin.y, point.z - origin.z)
                axial = self._dot(delta, axis)
                radial = tuple(delta[index] - axial * axis[index] for index in range(3))
                radial_length = math.sqrt(self._dot(radial, radial))
                if abs(radial_length - radius) > tolerance:
                    return None
                raw_angles.append(math.atan2(self._dot(radial, y_axis), self._dot(radial, x_axis)))
                parameters.append(axial)

            unwrapped = [raw_angles[0]]
            for previous, current in zip(raw_angles, raw_angles[1:]):
                delta = (current - previous + math.pi) % (2.0 * math.pi) - math.pi
                if abs(abs(delta) - math.pi) <= 1e-8:
                    return None
                unwrapped.append(unwrapped[-1] + delta)
            closing_delta = (raw_angles[0] - raw_angles[-1] + math.pi) % (2.0 * math.pi) - math.pi
            if abs(unwrapped[-1] + closing_delta - unwrapped[0]) > 1e-7:
                return None
            uv_loops.append([Point3(u, v, 0.0) for u, v in zip(unwrapped, parameters)])

        # Put nested loops on the same periodic copy of the surface.
        reference_center = sum(point.x for point in uv_loops[0]) / len(uv_loops[0])
        for loop in uv_loops[1:]:
            center = sum(point.x for point in loop) / len(loop)
            shift = 2.0 * math.pi * round((reference_center - center) / (2.0 * math.pi))
            if shift:
                loop[:] = [Point3(point.x + shift, point.y, point.z) for point in loop]

        points = tuple(point for loop in loops for point in loop)

        def surface_point(uv: Point3) -> Point3:
            return Point3(*(
                getattr(origin, coord)
                + radius * (math.cos(uv.x) * x_axis[index] + math.sin(uv.x) * y_axis[index])
                + uv.y * axis[index]
                for index, coord in enumerate(("x", "y", "z"))
            ))

        if any(max(loop, key=lambda p: p.x).x - min(loop, key=lambda p: p.x).x >= 2.0 * math.pi - 1e-7
               for loop in uv_loops):
            return self._periodic_cylindrical_trim_mesh(uv_loops, points, surface_point)

        triangulated = self._ear_clip_loop(uv_loops[0]) if len(uv_loops) == 1 else None
        if triangulated is None:
            triangulated = self._triangulate_loops(uv_loops)
        if not triangulated:
            return None
        uv_points, triangles = triangulated
        triangles = self._consistent_uv_winding(uv_points, triangles)
        # Keep the original STEP trim samples on shared edges.  They are
        # already within tolerance of the cylinder; projecting them here
        # would make adjacent faces disagree along a shared B-spline edge.
        return self._refine_curved_trim_mesh(
            points, uv_points, triangles, [len(loop) for loop in loops],
            surface_point, max(radius * 0.18, 1e-6),
        )

    @staticmethod
    def _periodic_cylindrical_trim_mesh(
        uv_loops: list[list[Point3]], original_points: tuple[Point3, ...], surface_point
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Mesh full-turn trims as UV strips while retaining every STEP boundary sample.

        A periodic STEP face may traverse its seam twice.  In UV these are
        separate sides of a rectangle, although they coincide in 3D.  The
        contour triangulator can collapse the sides and omit boundary arcs.
        """
        if len(uv_loops) != 1:
            return None
        uv = uv_loops[0]
        if len(uv) != len(original_points):
            return None
        period = 2.0 * math.pi
        umin = min(p.x for p in uv)
        umax = max(p.x for p in uv)
        if abs(umax - umin - period) > 1e-6:
            return None

        def full_rims() -> list[list[int]]:
            rims = []
            for start in range(len(uv)):
                preceding = uv[(start - 1) % len(uv)]
                if abs(preceding.y - uv[start].y) < 1e-7:
                    continue
                chain = [start]
                while len(chain) < len(uv):
                    nxt = (chain[-1] + 1) % len(uv)
                    if abs(uv[nxt].y - uv[start].y) >= 1e-7:
                        break
                    chain.append(nxt)
                if len(chain) > 2 and abs(abs(uv[chain[-1]].x - uv[chain[0]].x) - period) < 1e-6:
                    if uv[chain[0]].x > uv[chain[-1]].x:
                        chain.reverse()
                    rims.append(chain)
            return rims

        rims = full_rims()
        if not rims:
            return None
        points = list(original_points)
        triangles: list[tuple[int, int, int]] = []

        def stitch(first: list[int], second: list[int]) -> bool:
            if len(first) < 2 or len(second) < 2:
                return False
            if abs(uv[first[0]].x - uv[second[0]].x) > 1e-6 or abs(uv[first[-1]].x - uv[second[-1]].x) > 1e-6:
                return False
            if any(uv[chain[i + 1]].x < uv[chain[i]].x - 1e-8 for chain in (first, second) for i in range(len(chain) - 1)):
                return False
            i = j = 0
            while i < len(first) - 1 or j < len(second) - 1:
                a, b = first[i], second[j]
                ua = uv[first[i + 1]].x if i + 1 < len(first) else math.inf
                ub = uv[second[j + 1]].x if j + 1 < len(second) else math.inf
                # A vertical step in a rail must be connected from the far
                # side of the opposite rail's next vertex.  Connecting it
                # from the near side overlaps the following triangle.
                if (abs(ub - uv[b].x) < 1e-8 and ua < math.inf and ua > ub + 1e-8
                        and abs(uv[second[j + 1]].y - uv[a].y) > abs(uv[b].y - uv[a].y)):
                    ub = ua + 1e-8
                if (abs(ua - uv[a].x) < 1e-8 and ub < math.inf and ub > ua + 1e-8
                        and abs(uv[first[i + 1]].y - uv[b].y) > abs(uv[a].y - uv[b].y)):
                    ua = ub + 1e-8
                if ua <= ub + 1e-8:
                    triangles.append((a, first[i + 1], b))
                    i += 1
                if ub <= ua + 1e-8:
                    triangles.append((first[i], second[j + 1], b))
                    j += 1
            return True

        if len(rims) == 1:
            rim = rims[0]
            opposite = [rim[-1]]
            cursor = rim[-1]
            while cursor != rim[0]:
                cursor = (cursor + 1) % len(uv)
                opposite.append(cursor)
            opposite.reverse()
            if uv[opposite[0]].x > uv[opposite[-1]].x:
                opposite.reverse()
            if not stitch(rim, opposite):
                return None
        elif len(rims) == 2:
            # Two complete end rims and an intersection curve form a cylinder
            # with an opening.  The opening has two monotone arcs from the
            # seam to their shared opposite tip.
            low, high = sorted(rims, key=lambda chain: uv[chain[0]].y)
            if abs(uv[low[0]].x - uv[high[0]].x) > 1e-6:
                return None
            middle_height = next((uv[i].y for i in range(len(uv))
                                  if abs(uv[i].x - umin) < 1e-7
                                  and uv[low[0]].y + 1e-7 < uv[i].y < uv[high[0]].y - 1e-7), None)
            if middle_height is None:
                return None
            inner = [i for i, p in enumerate(uv)
                     if uv[low[0]].y + 1e-7 < p.y < uv[high[0]].y - 1e-7
                     and p.x < umax - 1e-7]
            if not inner:
                return None
            tip = max(inner, key=lambda i: uv[i].x)
            tip_u = uv[tip].x
            if tip_u <= umin + 1e-7 or tip_u >= umax - 1e-7:
                return None
            upper = sorted((i for i in inner if i == tip or uv[i].y >= middle_height - 1e-7), key=lambda i: uv[i].x)
            lower = sorted((i for i in inner if i == tip or uv[i].y <= middle_height + 1e-7), key=lambda i: uv[i].x)
            if upper[0] != lower[0] or upper[-1] != lower[-1]:
                return None
            low_left = [i for i in low if uv[i].x <= tip_u + 1e-7]
            high_left = [i for i in high if uv[i].x <= tip_u + 1e-7]
            if not stitch(low_left, lower) or not stitch(upper, high_left):
                return None
            # Past the opening tip, an internal rail joins the two halves.
            # Its seam endpoint is the existing STEP seam vertex.
            seam_end = next((i for i, p in enumerate(uv)
                             if abs(p.x - umax) < 1e-7 and abs(p.y - middle_height) < 1e-7), None)
            if seam_end is None:
                return None
            middle = [tip]
            for i in low:
                if tip_u + 1e-7 < uv[i].x < umax - 1e-7:
                    parameter = Point3(uv[i].x, middle_height, 0.0)
                    uv.append(parameter)
                    points.append(surface_point(parameter))
                    middle.append(len(uv) - 1)
            middle.append(seam_end)
            low_right = [i for i in low if uv[i].x >= tip_u - 1e-7]
            high_right = [i for i in high if uv[i].x >= tip_u - 1e-7]
            if not stitch(low_right, middle) or not stitch(middle, high_right):
                return None
        else:
            return None

        triangles = [tri for tri in triangles if abs((uv[tri[1]].x - uv[tri[0]].x) * (uv[tri[2]].y - uv[tri[0]].y)
                                                  - (uv[tri[1]].y - uv[tri[0]].y) * (uv[tri[2]].x - uv[tri[0]].x)) > 1e-10]
        if not triangles:
            return None
        return tuple(points), GeometryBuilder._consistent_uv_winding(tuple(uv), tuple(triangles))

    def _conical_trim_mesh(
        self, surface: StepEntity, loops: list[list[Point3]], refine: bool = True,
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Triangulate a conical trim in its unwrapped parameter plane."""

        if not loops:
            return None
        numbers = [_number(argument) for argument in surface.arguments if _number(argument) is not None]
        if len(numbers) < 2:
            return None
        reference_radius, semi_angle = numbers[:2]
        tangent = math.tan(semi_angle)
        placement_id = next(
            (_ref(argument) for argument in surface.arguments if _ref(argument) is not None),
            None,
        )
        placement = self.document.entity(placement_id or -1)
        if not placement or placement.type_name.upper() != "AXIS2_PLACEMENT_3D":
            return None
        refs = [_ref(argument) for argument in placement.arguments if _ref(argument) is not None]
        origin = self._cartesian(refs[0]) if refs else None
        axis = self._normalized(self._direction(refs[1])) if len(refs) > 1 and self._direction(refs[1]) else None
        x_axis = self._normalized(self._direction(refs[2])) if len(refs) > 2 and self._direction(refs[2]) else None
        if origin is None or axis is None:
            return None
        if x_axis is None:
            seed = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 1.0, 0.0)
            x_axis = self._normalized(self._cross(seed, axis))
        else:
            x_axis = self._normalized(
                tuple(x_axis[index] - self._dot(x_axis, axis) * axis[index] for index in range(3))
            )
        y_axis = self._normalized(self._cross(axis, x_axis)) if x_axis else None
        if x_axis is None or y_axis is None:
            return None

        scale = max(abs(reference_radius), 1.0)
        for loop in loops:
            scale = max(
                scale,
                *(self._distance(origin, point) for point in loop),
            )
        tolerance = max(scale * 2e-4, 1e-6)
        uv_loops: list[list[Point3]] = []
        for loop in loops:
            if len(loop) < 3:
                return None
            parameters = []
            raw_angles = []
            for point in loop:
                delta = (point.x - origin.x, point.y - origin.y, point.z - origin.z)
                axial = self._dot(delta, axis)
                radial = tuple(delta[index] - axial * axis[index] for index in range(3))
                radial_length = math.sqrt(self._dot(radial, radial))
                signed_radius = reference_radius + axial * tangent
                if abs(radial_length - abs(signed_radius)) > tolerance or abs(signed_radius) <= tolerance:
                    return None
                angle = math.atan2(self._dot(radial, y_axis), self._dot(radial, x_axis))
                raw_angles.append(angle - math.pi if signed_radius < 0 else angle)
                parameters.append(axial)

            unwrapped = [raw_angles[0]]
            for previous, current in zip(raw_angles, raw_angles[1:]):
                delta = (current - previous + math.pi) % (2.0 * math.pi) - math.pi
                if abs(abs(delta) - math.pi) <= 1e-8:
                    return None
                unwrapped.append(unwrapped[-1] + delta)
            closing_delta = (raw_angles[0] - raw_angles[-1] + math.pi) % (2.0 * math.pi) - math.pi
            if abs(unwrapped[-1] + closing_delta - unwrapped[0]) > 1e-7:
                return None
            if max(unwrapped) - min(unwrapped) >= 2.0 * math.pi - 1e-7:
                return None
            uv_loops.append([Point3(u, v, 0.0) for u, v in zip(unwrapped, parameters)])

        reference_center = sum(point.x for point in uv_loops[0]) / len(uv_loops[0])
        for loop in uv_loops[1:]:
            center = sum(point.x for point in loop) / len(loop)
            shift = 2.0 * math.pi * round((reference_center - center) / (2.0 * math.pi))
            if shift:
                loop[:] = [Point3(point.x + shift, point.y, point.z) for point in loop]

        triangulated = self._triangulate_loops(uv_loops)
        if not triangulated:
            return None
        uv_points, triangles = triangulated
        triangles = self._consistent_uv_winding(uv_points, triangles)
        # Preserve the shared STEP edge samples rather than projecting each
        # face's approximation independently onto its analytic cone.
        points = tuple(point for loop in loops for point in loop)

        def surface_point(uv: Point3) -> Point3:
            axial = uv.y
            signed_radius = reference_radius + axial * tangent
            return Point3(*(
                getattr(origin, coord)
                + axial * axis[index]
                + signed_radius * (math.cos(uv.x) * x_axis[index] + math.sin(uv.x) * y_axis[index])
                for index, coord in enumerate(("x", "y", "z"))
            ))

        if not refine:
            return points, triangles
        return self._refine_curved_trim_mesh(
            points, uv_points, triangles, [len(loop) for loop in loops],
            surface_point, max(scale * 0.18, 1e-6),
        )

    @staticmethod
    def _consistent_uv_winding(
        uv_points: tuple[Point3, ...],
        triangles: tuple[tuple[int, int, int], ...],
    ) -> tuple[tuple[int, int, int], ...]:
        """Give every non-degenerate contour triangle the same UV winding."""

        reference_sign = 0
        areas = []
        for a, b, c in triangles:
            first, second, third = uv_points[a], uv_points[b], uv_points[c]
            area = (
                (second.x - first.x) * (third.y - first.y)
                - (second.y - first.y) * (third.x - first.x)
            )
            areas.append(area)
            if reference_sign == 0 and abs(area) > 1e-12:
                reference_sign = 1 if area > 0 else -1
        if reference_sign == 0:
            return triangles
        return tuple(
            (a, c, b) if area * reference_sign < 0 else (a, b, c)
            for (a, b, c), area in zip(triangles, areas)
        )

    def _refine_curved_trim_mesh(
        self,
        points: tuple[Point3, ...],
        uv_points: tuple[Point3, ...],
        triangles: tuple[tuple[int, int, int], ...],
        loop_lengths: list[int],
        surface_point,
        target_edge_length: float,
        boundary_edges: set[tuple[int, int]] | None = None,
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]]:
        """Subdivide long interior chords without splitting shared trim edges.

        The contour triangulator returns a topologically constrained UV mesh,
        but its 3-D vertices initially exist only on the trim.  Mapping those
        boundary-only triangles directly to a curved surface leaves long
        chords across the bore.  Split only interior edges and place the new
        vertices on the analytic surface; the original trim edges and their
        shared STEP samples remain unchanged.
        """

        if len(points) != len(uv_points) or not triangles:
            return points, triangles

        if boundary_edges is None:
            boundary_edges = set()
            offset = 0
            for length in loop_lengths:
                for index in range(length):
                    following = offset + (index + 1) % length
                    boundary_edges.add(tuple(sorted((offset + index, following))))
                offset += length

        refined_points = list(points)
        refined_uv = list(uv_points)
        refined_triangles = list(triangles)
        for _ in range(4):
            edge_lengths: dict[tuple[int, int], float] = {}
            for triangle in refined_triangles:
                for first, second in zip(triangle, (triangle[1], triangle[2], triangle[0])):
                    edge = tuple(sorted((first, second)))
                    if edge in boundary_edges:
                        continue
                    first_point = surface_point(refined_uv[first])
                    second_point = surface_point(refined_uv[second])
                    edge_lengths[edge] = self._distance(first_point, second_point)
            split_edges = {
                edge for edge, length in edge_lengths.items()
                if length > target_edge_length
            }
            if not split_edges:
                break

            next_triangle_count = 0
            for triangle in refined_triangles:
                split_count = sum(
                    tuple(sorted((first, second))) in split_edges
                    for first, second in zip(triangle, (triangle[1], triangle[2], triangle[0]))
                )
                next_triangle_count += 1 if split_count == 0 else 3 + split_count
            if next_triangle_count > self.MAX_CURVED_TRIM_TRIANGLES:
                break

            midpoint_ids: dict[tuple[int, int], int] = {}
            for first, second in split_edges:
                uv = Point3(
                    (refined_uv[first].x + refined_uv[second].x) / 2,
                    (refined_uv[first].y + refined_uv[second].y) / 2,
                    0.0,
                )
                midpoint_ids[(first, second)] = len(refined_points)
                refined_uv.append(uv)
                refined_points.append(surface_point(uv))

            next_triangles = []
            for triangle in refined_triangles:
                polygon = []
                split_count = 0
                for first, second in zip(triangle, (triangle[1], triangle[2], triangle[0])):
                    polygon.append(first)
                    edge = tuple(sorted((first, second)))
                    if edge in split_edges:
                        polygon.append(midpoint_ids[edge])
                        split_count += 1
                if not split_count:
                    next_triangles.append(triangle)
                    continue

                center_uv = Point3(
                    sum(refined_uv[index].x for index in triangle) / 3,
                    sum(refined_uv[index].y for index in triangle) / 3,
                    0.0,
                )
                center_id = len(refined_points)
                refined_uv.append(center_uv)
                refined_points.append(surface_point(center_uv))
                next_triangles.extend(
                    (center_id, polygon[index], polygon[(index + 1) % len(polygon)])
                    for index in range(len(polygon))
                )
            refined_triangles = next_triangles

        return tuple(refined_points), tuple(refined_triangles)

    def _stitch_closed_loops(
        self, first: list[Point3], second: list[Point3]
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Create a ruled closed strip between equally sampled boundary loops."""

        if len(first) != len(second) or len(first) < 3:
            return None
        count = len(first)
        candidates = []
        for source in (second, list(reversed(second))):
            for shift in range(count):
                aligned = source[shift:] + source[:shift]
                score = sum(self._distance(a, b) ** 2 for a, b in zip(first, aligned))
                candidates.append((score, aligned))
        aligned = min(candidates, key=lambda item: item[0])[1]
        points = tuple(first + aligned)
        triangles = []
        for index in range(count):
            following = (index + 1) % count
            triangles.extend(
                ((index, following, count + following), (index, count + following, count + index))
            )
        return points, tuple(triangles)

    def _full_cylindrical_strip_mesh(
        self, surface: StepEntity, segments: list[tuple[Point3, ...]]
    ) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Join two complete rims even when their STEP arcs are split differently.

        A seam-bounded cylinder has one face loop containing two full rims and
        one or more straight generators. The rims may each be a single closed
        edge or several arcs. Match samples by angle, rather than by their
        position in the loop or by coincident arc endpoints.
        """

        placement_id = next((_ref(arg) for arg in surface.arguments if _ref(arg) is not None), None)
        placement = self.document.entity(placement_id or -1)
        if not placement or placement.type_name.upper() != "AXIS2_PLACEMENT_3D":
            return None
        refs = [_ref(arg) for arg in placement.arguments if _ref(arg) is not None]
        origin = self._cartesian(refs[0]) if refs else None
        axis_direction = self._direction(refs[1]) if len(refs) > 1 else None
        x_direction = self._direction(refs[2]) if len(refs) > 2 else None
        axis = self._normalized(axis_direction) if axis_direction else None
        x_axis = self._normalized(x_direction) if x_direction else None
        radius = next((_number(arg) for arg in surface.arguments if _number(arg) is not None), None)
        if origin is None or axis is None or x_axis is None or radius is None or radius <= 0:
            return None
        x_axis = self._normalized(tuple(x_axis[i] - self._dot(x_axis, axis) * axis[i] for i in range(3)))
        y_axis = self._normalized(self._cross(axis, x_axis)) if x_axis else None
        if x_axis is None or y_axis is None:
            return None

        def coordinates(point: Point3) -> tuple[float, float, float]:
            delta = (point.x - origin.x, point.y - origin.y, point.z - origin.z)
            height = self._dot(delta, axis)
            radial = tuple(delta[i] - height * axis[i] for i in range(3))
            angle = math.atan2(self._dot(radial, y_axis), self._dot(radial, x_axis)) % (2 * math.pi)
            if angle > 2 * math.pi - 1e-7:
                angle = 0.0
            return height, angle, math.sqrt(self._dot(radial, radial))

        samples = [(point, coordinates(point)) for segment in segments for point in segment]
        if not samples:
            return None
        low = min(uv[0] for _, uv in samples)
        high = max(uv[0] for _, uv in samples)
        tolerance = max(radius, high - low, 1.0) * 1e-6
        if high - low <= tolerance or any(
            min(abs(uv[0] - low), abs(uv[0] - high)) > tolerance
            or abs(uv[2] - radius) > tolerance
            for _, uv in samples
        ):
            return None

        rims: list[list[tuple[float, Point3]]] = [[], []]
        sweeps = [0.0, 0.0]
        for segment in segments:
            values = [coordinates(point) for point in segment]
            level = 0 if abs(values[0][0] - low) <= tolerance else 1
            if any(abs(value[0] - (low, high)[level]) > tolerance for value in values):
                continue  # A generator joins the two rims.
            rims[level].extend((value[1], point) for value, point in zip(values, segment))
            if len(segment) > 2:
                sweeps[level] += sum(
                    (right[1] - left[1] + math.pi) % (2 * math.pi) - math.pi
                    for left, right in zip(values, values[1:])
                )
        if any(abs(abs(sweep) - 2 * math.pi) > 1e-5 for sweep in sweeps):
            return None

        ordered_rims = []
        for rim in rims:
            ordered = []
            for angle, point in sorted(rim, key=lambda item: item[0]):
                if not ordered or angle - ordered[-1][0] > 1e-7:
                    ordered.append((angle, point))
            ordered_rims.append(ordered)
        first, second = ordered_rims
        if len(first) < 3 or len(first) != len(second) or any(
            abs(a[0] - b[0]) > 1e-6 for a, b in zip(first, second)
        ):
            return None

        count = len(first)
        points = tuple(point for _, point in first + second)
        triangles = []
        for index in range(count):
            following = (index + 1) % count
            triangles.extend(((index, following, count + following),
                              (index, count + following, count + index)))
        return points, tuple(triangles)

    @staticmethod
    def _face_metadata(face: StepEntity, surface: StepEntity | None) -> dict[str, object]:
        sense = next((arg for arg in reversed(face.arguments) if isinstance(arg, StepEnumeration)), None)
        type_name = surface.type_name.upper() if surface else "UNKNOWN"
        return {
            "type_name": type_name,
            "sense": f".{sense.value.upper()}." if sense else ".T.",
            "sense_applied": type_name in {
                "PLANE", "CYLINDRICAL_SURFACE", "CONICAL_SURFACE",
                "SPHERICAL_SURFACE", "TOROIDAL_SURFACE", "B_SPLINE_SURFACE_WITH_KNOTS",
            },
        }

    def _oriented_face_triangles(
        self, face: StepEntity, surface: StepEntity | None,
        points: tuple[Point3, ...], triangles: tuple[tuple[int, int, int], ...],
        consistent: bool = False,
    ) -> tuple[tuple[int, int, int], ...]:
        """Align preview winding with the analytic surface and ADVANCED_FACE sense.

        UV-triangulated curved trims already have consistent parameter-space
        winding.  For those meshes, use one reliable triangle to orient the
        whole face; local normals on coarse cone chords can otherwise flip
        neighboring triangles independently.
        """

        supported = {
            "PLANE", "CYLINDRICAL_SURFACE", "CONICAL_SURFACE",
            "SPHERICAL_SURFACE", "TOROIDAL_SURFACE",
        }
        if surface is None or surface.type_name.upper() not in supported:
            return triangles
        placement_id = next((_ref(arg) for arg in surface.arguments if _ref(arg) is not None), None)
        placement = self.document.entity(placement_id or -1)
        if not placement or placement.type_name.upper() != "AXIS2_PLACEMENT_3D":
            return triangles
        refs = [_ref(arg) for arg in placement.arguments if _ref(arg) is not None]
        if not refs:
            return triangles
        origin = self._cartesian(refs[0])
        axis_direction = self._direction(refs[1]) if len(refs) > 1 else (0.0, 0.0, 1.0)
        axis = self._normalized(axis_direction) if axis_direction else None
        if origin is None or axis is None:
            return triangles
        sense = next((arg for arg in reversed(face.arguments) if isinstance(arg, StepEnumeration)), None)
        sign = -1.0 if sense and sense.value.upper() == "F" else 1.0
        result = []
        global_flip = None
        for a, b, c in triangles:
            p, q, r = (points[index] for index in (a, b, c))
            normal = self._cross((q.x - p.x, q.y - p.y, q.z - p.z), (r.x - p.x, r.y - p.y, r.z - p.z))
            surface_type = surface.type_name.upper()
            target = axis
            center = tuple((getattr(p, coord) + getattr(q, coord) + getattr(r, coord)) / 3 for coord in ("x", "y", "z"))
            delta = tuple(center[index] - getattr(origin, coord) for index, coord in enumerate(("x", "y", "z")))
            axial_distance = self._dot(delta, axis)
            radial = tuple(delta[index] - axial_distance * axis[index] for index in range(3))
            radial_unit = self._normalized(radial)
            if surface_type == "CYLINDRICAL_SURFACE":
                target = radial
            elif surface_type == "CONICAL_SURFACE" and radial_unit:
                numbers = [_number(arg) for arg in surface.arguments if _number(arg) is not None]
                semi_angle = numbers[1] if len(numbers) > 1 else 0.0
                target = tuple(
                    math.cos(semi_angle) * radial_unit[index] - math.sin(semi_angle) * axis[index]
                    for index in range(3)
                )
            elif surface_type == "SPHERICAL_SURFACE":
                target = delta
            elif surface_type == "TOROIDAL_SURFACE" and radial_unit:
                numbers = [_number(arg) for arg in surface.arguments if _number(arg) is not None]
                major_radius = numbers[0] if numbers else 0.0
                tube_center = tuple(
                    getattr(origin, coord) + major_radius * radial_unit[index]
                    for index, coord in enumerate(("x", "y", "z"))
                )
                target = tuple(center[index] - tube_center[index] for index in range(3))
            alignment = self._dot(normal, target) * sign
            if consistent:
                if global_flip is None and abs(alignment) > 1e-12:
                    global_flip = alignment < 0
                result.append((a, b, c))
            else:
                result.append((a, c, b) if alignment < 0 else (a, b, c))
        if consistent and global_flip:
            return tuple((a, c, b) for a, b, c in triangles)
        return tuple(result)

    def _loops_form_planar_face(
        self, loops: list[list[Point3]], surface: StepEntity | None
    ) -> bool:
        """Return whether all trim vertices lie on the face's declared plane.

        A PLANE's placement is authoritative when present.  Faces without a
        surface can still use the generic triangulator, but only after a
        boundary-derived plane has been found and every loop has been checked
        against it.  Other declared surface types need their own tessellator.
        """

        if not loops or (surface and surface.type_name.upper() != "PLANE"):
            return False
        points = [point for loop in loops for point in loop]
        if len(points) < 3:
            return False

        coordinates = [
            tuple(getattr(point, coordinate) for coordinate in ("x", "y", "z"))
            for point in points
        ]
        extents = [
            max(point[index] for point in coordinates) - min(point[index] for point in coordinates)
            for index in range(3)
        ]
        scale = max(extents)
        if scale <= 1e-12:
            return False
        coordinate_scale = max(abs(value) for point in coordinates for value in point)
        tolerance = max(scale * 1e-7, coordinate_scale * 1e-12, 1e-9)

        origin = None
        normal = None
        if surface:
            placement_id = next(
                (_ref(argument) for argument in surface.arguments if _ref(argument) is not None),
                None,
            )
            placement = self.document.entity(placement_id or -1)
            if placement and placement.type_name.upper() == "AXIS2_PLACEMENT_3D":
                placement_refs = [
                    _ref(argument)
                    for argument in placement.arguments
                    if _ref(argument) is not None
                ]
                if placement_refs:
                    origin = self._cartesian(placement_refs[0])
                if len(placement_refs) > 1:
                    direction = self._direction(placement_refs[1])
                    normal = self._normalized(direction) if direction else None

        if origin is None or normal is None:
            # Use the longest baseline from the first boundary point, then
            # the point farthest from that line.  This finds a stable plane
            # from non-collinear vertices in linear time.
            base = coordinates[0]
            baseline = max(
                (tuple(point[index] - base[index] for index in range(3)) for point in coordinates),
                key=lambda vector: self._dot(vector, vector),
            )
            cross = max(
                (self._cross(baseline, tuple(point[index] - base[index] for index in range(3)))
                 for point in coordinates),
                key=lambda vector: self._dot(vector, vector),
            )
            normal = self._normalized(cross)
            origin = Point3(*base)
            if normal is None:
                return False

        origin_values = (origin.x, origin.y, origin.z)
        return all(
            abs(self._dot(tuple(point[index] - origin_values[index] for index in range(3)), normal))
            <= tolerance
            for point in coordinates
        )

    @staticmethod
    def _ear_clip_loop(loop: list[Point3]) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Triangulate one simple UV contour, retaining every STEP edge point."""
        count = len(loop)
        if count < 3:
            return None
        xmin, xmax = min(p.x for p in loop), max(p.x for p in loop)
        ymin, ymax = min(p.y for p in loop), max(p.y for p in loop)
        if xmax <= xmin or ymax <= ymin:
            return None
        coordinates = tuple(((p.x - xmin) / (xmax - xmin), (p.y - ymin) / (ymax - ymin)) for p in loop)

        def cross(a, b, c):
            px, py = coordinates[a]
            qx, qy = coordinates[b]
            rx, ry = coordinates[c]
            return (qx - px) * (ry - py) - (qy - py) * (rx - px)

        signed_area = sum(
            coordinates[i][0] * coordinates[(i + 1) % count][1]
            - coordinates[i][1] * coordinates[(i + 1) % count][0]
            for i in range(count)
        )
        if abs(signed_area) <= 1e-12:
            return None
        original_order = list(range(count)) if signed_area > 0 else list(reversed(range(count)))
        for epsilon in (1e-12, 1e-15, 0.0):
            remaining = original_order.copy()
            triangles = []
            while len(remaining) > 3:
                ear = None
                for position in range(len(remaining)):
                    a, b, c = (remaining[position - 1], remaining[position],
                               remaining[(position + 1) % len(remaining)])
                    if cross(a, b, c) <= epsilon:
                        continue
                    ax, ay = coordinates[a]
                    bx, by = coordinates[b]
                    cx, cy = coordinates[c]
                    low_x, high_x = min(ax, bx, cx), max(ax, bx, cx)
                    low_y, high_y = min(ay, by, cy), max(ay, by, cy)
                    blocked = False
                    for other in remaining:
                        if other in (a, b, c):
                            continue
                        x, y = coordinates[other]
                        if not low_x - epsilon <= x <= high_x + epsilon or not low_y - epsilon <= y <= high_y + epsilon:
                            continue
                        if (cross(a, b, other) >= -epsilon
                                and cross(b, c, other) >= -epsilon
                                and cross(c, a, other) >= -epsilon):
                            blocked = True
                            break
                    if not blocked:
                        ear = position
                        triangles.append((a, b, c))
                        break
                if ear is None:
                    break
                remaining.pop(ear)
            if len(remaining) == 3 and cross(*remaining) > epsilon:
                triangles.append(tuple(remaining))
                return tuple(loop), tuple(triangles)
        return None

    @staticmethod
    def _triangulate_loops(loops: list[list[Point3]]) -> tuple[tuple[Point3, ...], tuple[tuple[int, int, int], ...]] | None:
        """Triangulate coplanar boundary loops, including inner holes.

        STEP does not require exporters to list the outer bound first (FreeCAD
        lists both circular holes before it in some files). VTK's contour
        triangulator classifies the closed contours geometrically, avoiding
        both that ordering assumption and the concave fan artifacts.
        """
        points = tuple(point for loop in loops for point in loop)
        vtk_points = vtk.vtkPoints()
        for point in points:
            vtk_points.InsertNextPoint(point.x, point.y, point.z)
        lines = vtk.vtkCellArray()
        offset = 0
        for loop in loops:
            polyline = vtk.vtkPolyLine()
            polyline.GetPointIds().SetNumberOfIds(len(loop) + 1)
            for index in range(len(loop)):
                polyline.GetPointIds().SetId(index, offset + index)
            polyline.GetPointIds().SetId(len(loop), offset)
            lines.InsertNextCell(polyline)
            offset += len(loop)
        data = vtk.vtkPolyData()
        data.SetPoints(vtk_points)
        data.SetLines(lines)
        triangulator = vtk.vtkContourTriangulator()
        triangulator.SetInputData(data)
        triangles_filter = vtk.vtkTriangleFilter()
        triangles_filter.SetInputConnection(triangulator.GetOutputPort())
        triangles_filter.Update()
        output = triangles_filter.GetOutput()
        triangles: list[tuple[int, int, int]] = []
        ids = vtk.vtkIdList()
        output.GetPolys().InitTraversal()
        while output.GetPolys().GetNextCell(ids):
            if ids.GetNumberOfIds() == 3:
                triangles.append(tuple(ids.GetId(index) for index in range(3)))
        return (points, tuple(triangles)) if triangles else None

    @staticmethod
    def _distance(first: Point3, second: Point3) -> float:
        return math.sqrt((first.x - second.x) ** 2 + (first.y - second.y) ** 2 + (first.z - second.z) ** 2)
