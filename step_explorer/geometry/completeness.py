"""Lightweight model-level checks based on STEP face and edge topology."""

from __future__ import annotations

from dataclasses import dataclass

from ..step.parser import StepDocument
from ..step.values import StepAggregate, StepEnumeration, StepReference
from .builder import GeometrySnapshot


@dataclass(frozen=True)
class CompletenessReport:
    face_count: int
    meshed_face_count: int
    triangle_count: int
    edge_count: int
    open_edge_count: int
    non_manifold_edge_count: int
    unresolved_edge_count: int
    suspicious_normal_count: int
    inconsistent_edge_count: int = 0
    disconnected_loop_count: int = 0
    closed_unbounded_face_count: int = 0
    same_direction_pairs: tuple[SameDirectionPair, ...] = ()

    @property
    def is_closed(self) -> bool:
        return (
            self.face_count > 0
            and (self.edge_count > 0 or self.closed_unbounded_face_count == self.face_count)
            and self.meshed_face_count == self.face_count
            and self.open_edge_count == 0
            and self.non_manifold_edge_count == 0
            and self.unresolved_edge_count == 0
            and self.suspicious_normal_count == 0
            and self.inconsistent_edge_count == 0
            and self.disconnected_loop_count == 0
        )


@dataclass(frozen=True)
class FacePreviewGap:
    face_id: int
    surface_type: str
    reason: str


@dataclass(frozen=True)
class EdgeUse:
    face_id: int
    oriented_edge_id: int
    forward: bool
    oriented_forward: bool
    bound_forward: bool


@dataclass(frozen=True)
class SameDirectionPair:
    edge_id: int
    uses: tuple[EdgeUse, EdgeUse]


def _orientation(entity) -> bool | None:
    flag = next(
        (value.value.upper() for value in reversed(entity.arguments) if isinstance(value, StepEnumeration)),
        None,
    )
    return flag == "T" if flag in {"T", "F"} else None


def assess_completeness(document: StepDocument, snapshot: GeometrySnapshot) -> CompletenessReport:
    """Check face coverage, directed edge pairing, and boundary continuity.

    STEP edge identity is used for the watertight check, avoiding false gaps
    from independently tessellated faces whose floating point samples differ.
    This checks topological closure; it does not validate self-intersections or
    other geometric BREP constraints.
    """

    faces = [entity for entity in document.entities if entity.type_name.upper() == "ADVANCED_FACE"]
    meshes = {mesh.entity_id: mesh for mesh in snapshot.faces}
    edge_uses: dict[int, list[EdgeUse]] = {}
    unresolved = 0
    disconnected_loops = 0
    closed_unbounded_faces = 0

    for face in faces:
        face_forward = _orientation(face)
        if face_forward is None:
            unresolved += 1
        bound_aggregates = [argument for argument in face.arguments if isinstance(argument, StepAggregate)]
        unresolved += sum(
            not isinstance(value, StepReference)
            for argument in bound_aggregates for value in argument.values
        )
        bound_refs = [
            bound_ref
            for argument in bound_aggregates
            for bound_ref in argument.values
            if isinstance(bound_ref, StepReference)
        ]
        if not bound_refs:
            surface_ref = next(
                (value for value in face.arguments if isinstance(value, StepReference)), None
            )
            surface = document.entity(surface_ref.entity_id) if surface_ref else None
            if (
                len(bound_aggregates) == 1
                and not bound_aggregates[0].values
                and surface is not None
                and surface.type_name.upper() in {"SPHERICAL_SURFACE", "TOROIDAL_SURFACE"}
            ):
                closed_unbounded_faces += 1
            else:
                unresolved += 1
            continue

        for bound_ref in bound_refs:
            bound = document.entity(bound_ref.entity_id)
            if bound is None or bound.type_name.upper() not in {"FACE_OUTER_BOUND", "FACE_BOUND"}:
                unresolved += 1
                continue
            bound_forward = _orientation(bound)
            if bound_forward is None:
                unresolved += 1
            loop_ref = next((value for value in bound.arguments if isinstance(value, StepReference)), None)
            loop = document.entity(loop_ref.entity_id) if loop_ref else None
            if loop is None or loop.type_name.upper() != "EDGE_LOOP":
                unresolved += 1
                continue
            unresolved += sum(
                not isinstance(value, StepReference)
                for argument in loop.arguments if isinstance(argument, StepAggregate)
                for value in argument.values
            )
            oriented_refs = [
                value for argument in loop.arguments if isinstance(argument, StepAggregate)
                for value in argument.values if isinstance(value, StepReference)
            ]
            if not oriented_refs:
                unresolved += 1
                continue
            directed_vertices: list[tuple[int, int]] = []
            for oriented_ref in oriented_refs:
                oriented = document.entity(oriented_ref.entity_id)
                if oriented is None or oriented.type_name.upper() != "ORIENTED_EDGE":
                    unresolved += 1
                    continue
                oriented_forward = _orientation(oriented)
                edge_ref = next(
                    (value for value in reversed(oriented.arguments) if isinstance(value, StepReference)), None
                )
                edge = document.entity(edge_ref.entity_id) if edge_ref else None
                if oriented_forward is None or edge is None or edge.type_name.upper() != "EDGE_CURVE":
                    unresolved += 1
                    continue
                endpoint_refs = edge.arguments[1:3]
                if len(endpoint_refs) != 2 or not all(isinstance(value, StepReference) for value in endpoint_refs):
                    unresolved += 1
                    continue
                start_id, end_id = (value.entity_id for value in endpoint_refs)
                if any(
                    (vertex := document.entity(vertex_id)) is None or vertex.type_name.upper() != "VERTEX_POINT"
                    for vertex_id in (start_id, end_id)
                ):
                    unresolved += 1
                    continue
                if not oriented_forward:
                    start_id, end_id = end_id, start_id
                directed_vertices.append((start_id, end_id))
                if bound_forward is not None:
                    # ADVANCED_FACE.same_sense relates the face normal to its
                    # underlying surface. It does not reverse EDGE_LOOP traversal.
                    effective_forward = oriented_forward ^ (not bound_forward)
                    edge_uses.setdefault(edge.entity_id, []).append(
                        EdgeUse(face.entity_id, oriented.entity_id, effective_forward,
                                oriented_forward, bound_forward)
                    )
            if len(directed_vertices) == len(oriented_refs):
                disconnected_loops += sum(
                    end_id != directed_vertices[(index + 1) % len(directed_vertices)][0]
                    for index, (_, end_id) in enumerate(directed_vertices)
                )

    same_direction_pairs = tuple(
        SameDirectionPair(edge_id, (uses[0], uses[1]))
        for edge_id, uses in sorted(edge_uses.items())
        if len(uses) == 2 and uses[0].forward == uses[1].forward
    )
    return CompletenessReport(
        face_count=len(faces),
        meshed_face_count=len(set(meshes) & {face.entity_id for face in faces}),
        triangle_count=sum(len(mesh.triangles) for mesh in meshes.values()),
        edge_count=len(edge_uses),
        open_edge_count=sum(len(uses) == 1 for uses in edge_uses.values()),
        non_manifold_edge_count=sum(len(uses) > 2 for uses in edge_uses.values()),
        unresolved_edge_count=unresolved,
        suspicious_normal_count=sum(mesh.orientation_warning is not None for mesh in meshes.values()),
        inconsistent_edge_count=len(same_direction_pairs),
        disconnected_loop_count=disconnected_loops,
        closed_unbounded_face_count=closed_unbounded_faces,
        same_direction_pairs=same_direction_pairs,
    )


def find_face_preview_gaps(
    document: StepDocument,
    snapshot: GeometrySnapshot,
    through_order: int,
    face_ids: set[int] | None = None,
    mesh_ids: set[int] | None = None,
) -> tuple[FacePreviewGap, ...]:
    """Find reached faces that remain unmeshed after their STEP dependencies arrive.

    A face is not called unsupported while playback is still waiting for an
    entity in its referenced geometry graph. Once that graph is present, a
    missing mesh is actionable: either the surface type is not tessellated by
    the preview or its trim could not be converted to triangles.
    """

    if mesh_ids is None:
        mesh_ids = {mesh.entity_id for mesh in snapshot.faces}
    gaps: list[FacePreviewGap] = []
    supported_surfaces = {
        "PLANE",
        "CYLINDRICAL_SURFACE",
        "CONICAL_SURFACE",
        "SPHERICAL_SURFACE",
        "TOROIDAL_SURFACE",
    }

    candidates = (
        (document.entity(entity_id) for entity_id in face_ids)
        if face_ids is not None else document.entities
    )
    for face in candidates:
        if face is None:
            continue
        if face.type_name.upper() != "ADVANCED_FACE" or face.order > through_order:
            continue
        if face.entity_id in mesh_ids:
            continue

        pending = [face]
        visited: set[int] = set()
        waiting_for_dependency = False
        unresolved_dependency = False
        while pending:
            current = pending.pop()
            if current.entity_id in visited:
                continue
            visited.add(current.entity_id)
            for reference in current.references:
                dependency = document.entity(reference)
                if dependency is None:
                    unresolved_dependency = True
                elif dependency.order > through_order:
                    waiting_for_dependency = True
                else:
                    pending.append(dependency)
        if waiting_for_dependency:
            continue

        surface_ref = next(
            (argument for argument in face.arguments if isinstance(argument, StepReference)),
            None,
        )
        surface = document.entity(surface_ref.entity_id) if surface_ref is not None else None
        surface_type = surface.type_name.upper() if surface is not None else "UNKNOWN"
        if unresolved_dependency:
            reason = "unresolved STEP reference in face geometry"
        elif surface_type == "B_SPLINE_SURFACE_WITH_KNOTS":
            reason = "this B-spline trim is not supported by the preview tessellator; its control net is shown"
        elif surface_type not in supported_surfaces:
            reason = f"surface type {surface_type} is not supported by the preview tessellator"
        else:
            reason = f"could not triangulate the {surface_type} face boundary"
        gaps.append(FacePreviewGap(face.entity_id, surface_type, reason))

    return tuple(gaps)
