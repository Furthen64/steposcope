"""Copyable, evidence-based summary of a completed STEP preview."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .geometry.builder import GeometryBuilder, GeometrySnapshot
from .geometry.completeness import assess_completeness, find_face_preview_gaps
from .step.parser import StepDocument
from .step.values import StepAggregate, StepReference
from .ui.object_tree import document_bodies


POINT_WARNING_THRESHOLD = 1_000
SLOW_STEP_THRESHOLD_MS = 2_000.0
FACE_PROCESSING_LIMIT_SECONDS = 2.0
BODY_PROCESSING_LIMIT_SECONDS = 60.0
SURFACE_CONTROL_LIMIT = 1_000
EDGE_CONTROL_LIMIT = 256
PREVIEW_HEALTH_RED_BELOW = 80

# Headline penalty weights. Every check contributes points to one auditable
# total: 0 is a clean file, and the number keeps growing with severity instead
# of saturating at 100 like the two diagnostic sub-scores.
HEALTH_PENALTY_PER_POINT = 100
PAIN_PENALTY_PER_TEN_PERCENT = 500
POINT_PENALTY_PER_ENTITY = 10


@dataclass(frozen=True)
class PainAnalysis:
    score: int
    warnings: tuple[str, ...]
    body_times: tuple[tuple[int, str, float], ...]
    face_times: tuple[tuple[int, float], ...]
    exceedances: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class HealthDeduction:
    points: int
    reason: str
    measured: str
    formula: str


def report_red_reasons(health_score: int, pain: PainAnalysis) -> tuple[str, ...]:
    reasons = []
    if health_score < PREVIEW_HEALTH_RED_BELOW:
        reasons.append(f"preview/topology health {health_score}/100 < {PREVIEW_HEALTH_RED_BELOW}/100")
    reasons.extend(pain.warnings)
    return tuple(reasons)


def analyze_processing_pain(
    document: StepDocument,
    entity_processing_seconds: dict[int, float] | None = None,
    face_peak_seconds: dict[int, float] | None = None,
) -> PainAnalysis:
    """Find expensive handlers and unusually dense individual STEP geometry."""

    entity_seconds = entity_processing_seconds or {}
    face_seconds = face_peak_seconds or {}
    warnings: list[str] = []
    ratios: list[float] = []
    face_times = tuple(sorted(face_seconds.items(), key=lambda item: item[1], reverse=True))
    for face_id, seconds in face_times:
        if seconds > FACE_PROCESSING_LIMIT_SECONDS:
            warnings.append(
                f"Face #{face_id} geometry handler: {seconds:.3f} s "
                f"(limit: >{FACE_PROCESSING_LIMIT_SECONDS:g} s)."
            )
            ratios.append(seconds / FACE_PROCESSING_LIMIT_SECONDS)

    body_times = tuple(
        (body.entity_id, body.name,
         sum(entity_seconds.get(entity_id, 0.0) for entity_id in body.entity_ids))
        for body in document_bodies(document)
    )
    for body_id, name, seconds in body_times:
        if seconds > BODY_PROCESSING_LIMIT_SECONDS:
            warnings.append(
                f"Body #{body_id} ({name}) measured geometry handlers: {seconds:.3f} s "
                f"(limit: >{BODY_PROCESSING_LIMIT_SECONDS:g} s)."
            )
            ratios.append(seconds / BODY_PROCESSING_LIMIT_SECONDS)

    for entity in document.entities:
        grid = _control_grid(entity)
        if grid is not None:
            references = grid[0] * grid[1]
            if references > SURFACE_CONTROL_LIMIT:
                warnings.append(
                    f"Surface #{entity.entity_id}: {references:,} control-point references "
                    f"(limit: >{SURFACE_CONTROL_LIMIT:,} per surface)."
                )
                ratios.append(references / SURFACE_CONTROL_LIMIT)
        if entity.type_name.upper() == "POLYLINE" and len(entity.arguments) > 1:
            points = entity.arguments[1]
            count = len(points.values) if isinstance(points, StepAggregate) else 0
            if count > EDGE_CONTROL_LIMIT:
                warnings.append(
                    f"Polyline #{entity.entity_id}: {count:,} Cartesian-point references "
                    f"(limit: >{EDGE_CONTROL_LIMIT:,} per curve)."
                )
                ratios.append(count / EDGE_CONTROL_LIMIT)
        if entity.type_name.upper() != "EDGE_CURVE" or len(entity.arguments) < 4:
            continue
        curve_ref = entity.arguments[3]
        curve = document.entity(curve_ref.entity_id) if isinstance(curve_ref, StepReference) else None
        if curve is None or curve.type_name.upper() != "B_SPLINE_CURVE_WITH_KNOTS" or len(curve.arguments) < 3:
            continue
        controls = curve.arguments[2]
        count = len(controls.values) if isinstance(controls, StepAggregate) else 0
        if count > EDGE_CONTROL_LIMIT:
            warnings.append(
                f"Edge #{entity.entity_id} / curve #{curve.entity_id}: {count:,} control points "
                f"(limit: >{EDGE_CONTROL_LIMIT:,} per edge)."
            )
            ratios.append(count / EDGE_CONTROL_LIMIT)

    # Any exceeded limit is red. The score gives a rough sense of severity;
    # 50 is the limit, and values above it are painful.
    score = min(100, max(51, round(50 * max(ratios)))) if ratios else 0
    return PainAnalysis(
        score, tuple(warnings), body_times, face_times,
        tuple(zip(warnings, ratios)),  # warnings and ratios are appended in lockstep
    )


def preview_health_deductions(
    document: StepDocument, snapshot: GeometrySnapshot, topology, bad_face_count: int
) -> list[HealthDeduction]:
    """The three health deductions with the numbers that produced them."""

    records: list[HealthDeduction] = []
    if topology.face_count:
        missing = topology.face_count - topology.meshed_face_count
        if missing:
            points = min(30, round(30 * missing / topology.face_count))
            records.append(HealthDeduction(
                points, f"{missing} face(s) lack a preview mesh",
                f"missing {missing}/{topology.face_count} faces",
                f"30 * {missing} / {topology.face_count} = "
                f"{round(30 * missing / topology.face_count)}, capped at 30",
            ))
    else:
        records.append(HealthDeduction(
            30, "no ADVANCED_FACE entities", "0 ADVANCED_FACE entities",
            "fixed penalty 30",
        ))
    topology_issues = (topology.open_edge_count + topology.non_manifold_edge_count
                       + topology.unresolved_edge_count + topology.inconsistent_edge_count
                       + topology.disconnected_loop_count)
    if topology_issues:
        records.append(HealthDeduction(
            min(30, 5 * topology_issues), f"{topology_issues} topology warning(s)",
            f"open {topology.open_edge_count} + overused {topology.non_manifold_edge_count} + "
            f"unresolved {topology.unresolved_edge_count} + same-direction "
            f"{topology.inconsistent_edge_count} + disconnected {topology.disconnected_loop_count}"
            f" = {topology_issues}",
            f"5 * {topology_issues} = {5 * topology_issues}, capped at 30",
        ))
    if bad_face_count:
        divisor = max(1, topology.face_count)
        records.append(HealthDeduction(
            min(30, round(30 * bad_face_count / divisor)),
            f"{bad_face_count} face(s) failed preview checks",
            f"bad {bad_face_count}/{topology.face_count} faces",
            f"30 * {bad_face_count} / {divisor} = "
            f"{round(30 * bad_face_count / divisor)}, capped at 30",
        ))
    return records


def preview_health_score(document: StepDocument, snapshot: GeometrySnapshot) -> tuple[int, list[tuple[int, str]]]:
    """Return the report's heuristic health score and its explicit deductions."""

    topology = assess_completeness(document, snapshot)
    builder = GeometryBuilder(document)
    meshes = {mesh.entity_id: mesh for mesh in snapshot.faces}
    bad_face_count = sum(
        builder.assess_face(face.entity_id, meshes.get(face.entity_id)).category == "BAD"
        for face in document.entities if face.type_name.upper() == "ADVANCED_FACE"
    )
    deductions = preview_health_deductions(document, snapshot, topology, bad_face_count)
    return max(0, 100 - sum(record.points for record in deductions)), [
        (record.points, record.reason) for record in deductions
    ]


def penalty_score(
    deductions: list[HealthDeduction], pain: PainAnalysis, point_count: int
) -> tuple[int, list[tuple[int, str]]]:
    """One auditable headline number: bigger means worse, 0 means nothing found."""

    contributions: list[tuple[int, str]] = []
    for record in deductions:
        penalty = record.points * HEALTH_PENALTY_PER_POINT
        contributions.append((
            penalty,
            f"health -{record.points} pt ({record.measured}) x {HEALTH_PENALTY_PER_POINT}"
            f" = {penalty:,}  [{record.reason}]",
        ))
    for message, ratio in pain.exceedances:
        penalty = max(1, round((ratio - 1) * 10)) * PAIN_PENALTY_PER_TEN_PERCENT
        contributions.append((
            penalty,
            f"pain {ratio:.2f}x limit ({round((ratio - 1) * 10)} steps past 1.00x"
            f" x {PAIN_PENALTY_PER_TEN_PERCENT}) = {penalty:,}  [{message}]",
        ))
    point_excess = max(0, point_count - POINT_WARNING_THRESHOLD)
    if point_excess:
        penalty = point_excess * POINT_PENALTY_PER_ENTITY
        contributions.append((
            penalty,
            f"complexity {point_excess:,} CARTESIAN_POINT above the"
            f" {POINT_WARNING_THRESHOLD:,} threshold x {POINT_PENALTY_PER_ENTITY}"
            f" = {penalty:,}",
        ))
    return sum(points for points, _ in contributions), contributions


def _control_grid(entity) -> tuple[int, int] | None:
    if entity.type_name.upper() != "B_SPLINE_SURFACE_WITH_KNOTS" or len(entity.arguments) < 4:
        return None
    grid = entity.arguments[3]
    if not isinstance(grid, StepAggregate) or not grid.values:
        return None
    rows = grid.values
    if not all(isinstance(row, StepAggregate) for row in rows):
        return None
    widths = {len(row.values) for row in rows}
    return (len(rows), widths.pop()) if len(widths) == 1 else None


def build_llm_report(
    source_path: str | Path,
    document: StepDocument,
    snapshot: GeometrySnapshot,
    playback_step_ms: dict[int, float] | None = None,
    entity_processing_seconds: dict[int, float] | None = None,
    face_peak_seconds: dict[int, float] | None = None,
) -> str:
    """Summarize observable preview facts; never present them as BREP validity."""

    source = Path(source_path).expanduser().resolve()
    counts = Counter(entity.type_name.upper() for entity in document.entities)
    topology = assess_completeness(document, snapshot)
    gaps = find_face_preview_gaps(document, snapshot, len(document.entities) - 1)
    builder = GeometryBuilder(document)
    meshes = {mesh.entity_id: mesh for mesh in snapshot.faces}
    face_results = [
        (face, builder.assess_face(face.entity_id, meshes.get(face.entity_id)))
        for face in document.entities if face.type_name.upper() == "ADVANCED_FACE"
    ]
    bad_faces = [(face, result) for face, result in face_results if result.category == "BAD"]
    uncertain_faces = [(face, result) for face, result in face_results if result.category == "JUDGEMENT CALL"]
    grids = [
        (entity.entity_id, grid)
        for entity in document.entities
        if (grid := _control_grid(entity)) is not None
    ]
    timings = playback_step_ms or {}
    slow = sorted(
        ((elapsed, document.entity(entity_id)) for entity_id, elapsed in timings.items()
         if elapsed >= SLOW_STEP_THRESHOLD_MS and document.entity(entity_id) is not None),
        reverse=True, key=lambda item: item[0],
    )

    point_count = counts["CARTESIAN_POINT"]
    health_records = preview_health_deductions(document, snapshot, topology, len(bad_faces))
    score = max(0, 100 - sum(record.points for record in health_records))
    deductions = [(record.points, record.reason) for record in health_records]
    pain = analyze_processing_pain(document, entity_processing_seconds, face_peak_seconds)
    red_reasons = report_red_reasons(score, pain)
    total, contributions = penalty_score(health_records, pain, point_count)
    verdict = "BAD" if red_reasons else "OK"

    lines = [
        "STEPOSCOPE REPORT FOR LLM",
        f"Source: {source}",
        f"Size: {source.stat().st_size:,} bytes" if source.is_file() else "Size: unavailable",
        f"Entities: {len(document.entities):,}",
        "",
        f"Score: {total:,} [{verdict}]",
        *(f"  BAD because: {reason}" for reason in red_reasons),
        "",
        "SCORING BREAKDOWN",
        "Headline score = sum of penalty points: 0 when nothing was found, unbounded as",
        f"severity grows. Weights: each preview/topology health deduction point costs"
        f" {HEALTH_PENALTY_PER_POINT};",
        f"each pain check costs {PAIN_PENALTY_PER_TEN_PERCENT} per full 10% past its limit"
        f" (2.00x = 10 steps = {10 * PAIN_PENALTY_PER_TEN_PERCENT:,});",
        f"each CARTESIAN_POINT above {POINT_WARNING_THRESHOLD:,} costs {POINT_PENALTY_PER_ENTITY}.",
        f"Verdict rule: [BAD] when health < {PREVIEW_HEALTH_RED_BELOW}/100 or any pain limit is"
        " exceeded - the same rule that turns the Report button red.",
        "The two sub-scores are diagnostics and are never added into the headline number:",
        f"  preview/topology health starts at 100 and loses points, {score}/100 here;",
        f"  processing pain is 50 at a limit and above it past one, {pain.score}/100 here.",
    ]
    if contributions:
        lines.append("Contributions, largest first:")
        for penalty, label in sorted(contributions, reverse=True):
            lines.append(f"  +{penalty:,} {label}")
        lines.append(f"  = {total:,} penalty point(s) from {len(contributions)} contribution(s)")
    else:
        lines.append("No health deduction, pain limit or complexity threshold was hit.")
    lines.extend([
        "",
        f"Preview and topology health: {score}/100",
        f"Processing pain: {pain.score}/100 ({'RED' if pain.warnings else 'GREEN'})",
        f"Report button: {'RED' if red_reasons else 'GREEN'}",
        "Scope: heuristic score for this Steposcope preview and STEP edge connectivity; "
        "not a CAD/BREP validity certificate.",
        "Complexity and playback time are reported separately because they depend on the "
        "producer and local machine, not necessarily on geometry correctness.",
        "Deductions: " + ("; ".join(f"-{points} {reason}" for points, reason in deductions)
                          if deductions else "none"),
        "",
        "PROCESSING PAIN CHECKS",
        "Limits: face geometry handler >2 s; body geometry handlers >60 s; "
        "one surface >1,000 control-point references; one edge or polyline >256 control points.",
        "Measured handler times exclude playback delay, rendering, and UI work. "
        "Body time sums handler work for entities reachable from that body.",
    ])
    lines.extend(pain.warnings if pain.warnings else ["No processing pain limit exceeded."])
    if pain.body_times:
        lines.append("Body handler time:")
        lines.extend(f"  #{body_id} {name}: {seconds:.3f} s"
                     for body_id, name, seconds in pain.body_times)
    else:
        lines.append("No STEP body records found for per-body timing.")
    if not entity_processing_seconds and not face_peak_seconds:
        lines.append("No isolated handler timings recorded; time-based checks are unavailable.")
    lines.extend([
        "",
        "ENTITY COUNTS",
    ])
    lines.extend(f"{name}: {count:,}" for name, count in counts.most_common())
    lines.extend(["", "COMPLEXITY FLAGS"])
    lines.append(
        f"{'WARNING' if point_count > POINT_WARNING_THRESHOLD else 'OK'}: "
        f"{point_count:,} CARTESIAN_POINT entities "
        f"(warning threshold: >{POINT_WARNING_THRESHOLD:,})."
    )
    if grids:
        lines.append("B-spline surface control grids (rows x columns):")
        lines.extend(f"  #{entity_id}: {rows} x {columns} = {rows * columns:,} references"
                     for entity_id, (rows, columns) in grids)
    else:
        lines.append("B-spline surface control grids: none found.")
    lines.extend([
        "",
        "MODEL CHECK",
        f"Faces meshed: {topology.meshed_face_count}/{topology.face_count}",
        f"Preview triangles: {topology.triangle_count:,}",
        f"STEP edges: {topology.edge_count:,}; open: {topology.open_edge_count}; "
        f"overused: {topology.non_manifold_edge_count}; unresolved: {topology.unresolved_edge_count}",
        f"Same-direction edge pairs: {topology.inconsistent_edge_count}; "
        f"disconnected loop joins: {topology.disconnected_loop_count}; "
        f"suspected normal issues: {topology.suspicious_normal_count}",
        "This check uses STEP edge identity. It does not detect self-intersections "
        "or prove geometric validity.",
        "",
        "FACE PREVIEW CHECKS",
        f"BAD: {len(bad_faces)}; JUDGEMENT CALL: {len(uncertain_faces)}; "
        f"OK: {len(face_results) - len(bad_faces) - len(uncertain_faces)}",
    ])
    if topology.same_direction_pairs:
        lines.append("Same-direction EDGE_CURVE IDs: " +
                     ", ".join(f"#{pair.edge_id}" for pair in topology.same_direction_pairs))
    for face, result in face_results:
        if result.category != "OK":
            lines.append(f"#{face.entity_id} {result.surface_type}: {result.category} "
                         f"(source line {face.span.line})")
            lines.extend(f"  {reason}" for reason in result.reasons)
    for mesh in snapshot.faces:
        if mesh.preview_note:
            lines.append(f"#{mesh.entity_id} preview note: {mesh.preview_note}")
    if not face_results:
        lines.append("No advanced faces found.")
    lines.extend(["", "PREVIEW GAPS"])
    lines.extend(f"#{gap.face_id} {gap.surface_type}: {gap.reason}" for gap in gaps)
    if not gaps:
        lines.append("None reported.")
    lines.extend(["", "SLOW PLAYBACK STEPS"])
    lines.append(
        f"Threshold: >={SLOW_STEP_THRESHOLD_MS:,.0f} ms. These are observed step times, "
        "including playback delay, UI updates, rendering, and possibly fast-preview batching; "
        "they are not isolated face computation times."
    )
    if slow:
        lines.extend(f"#{entity.entity_id} {entity.type_name}: {elapsed:,.2f} ms "
                     f"(source line {entity.span.line})" for elapsed, entity in slow[:30])
        if len(slow) > 30:
            lines.append(f"... {len(slow) - 30} more steps above threshold")
    else:
        lines.append("None recorded above threshold." if timings else
                     "No playback timings recorded. Play through the file to collect them.")
    return "\n".join(lines) + "\n"
