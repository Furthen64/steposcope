"""Write a STEP exchange file containing shape data and its dependencies."""

from datetime import datetime
from pathlib import Path

from .parser import StepDocument


_GEOMETRY_TYPES = {
    "CARTESIAN_POINT", "DIRECTION", "VECTOR", "AXIS1_PLACEMENT", "AXIS2_PLACEMENT_2D",
    "AXIS2_PLACEMENT_3D", "LINE", "CIRCLE", "ELLIPSE", "PLANE",
    "CYLINDRICAL_SURFACE", "CONICAL_SURFACE", "SPHERICAL_SURFACE",
    "TOROIDAL_SURFACE", "VERTEX_POINT", "EDGE_CURVE", "ORIENTED_EDGE",
    "EDGE_LOOP", "VERTEX_LOOP", "FACE_BOUND", "FACE_OUTER_BOUND",
    "ADVANCED_FACE", "CLOSED_SHELL", "OPEN_SHELL", "MANIFOLD_SOLID_BREP",
    "BREP_WITH_VOIDS", "SHELL_BASED_SURFACE_MODEL", "FACETED_BREP",
}


def _is_shape_data(type_name: str) -> bool:
    name = type_name.upper()
    return (
        name in _GEOMETRY_TYPES
        or name.endswith("_SHAPE_REPRESENTATION")
        or name in {
            "SHAPE_REPRESENTATION", "SHAPE_DEFINITION_REPRESENTATION",
            "GEOMETRIC_CURVE_SET", "GEOMETRIC_SET",
        }
        or name.startswith(("B_SPLINE_", "RATIONAL_B_SPLINE_", "TRIMMED_CURVE"))
        or name.endswith(("_BREP", "_SURFACE", "_CURVE"))
    )


def geometry_entity_ids(document: StepDocument) -> set[int]:
    """Return shape entities and the complete reference closure they require."""

    selected: set[int] = set()
    pending = [entity.entity_id for entity in document.entities if _is_shape_data(entity.type_name)]
    while pending:
        entity_id = pending.pop()
        if entity_id in selected:
            continue
        entity = document.entity(entity_id)
        if entity is None:
            raise ValueError(f"STEP file references missing entity #{entity_id}")
        selected.add(entity_id)
        pending.extend(entity.references)
    return selected


def geometry_only_step(document: StepDocument, filename: str) -> str:
    """Build a parseable STEP file, retaining original geometry records and schema."""

    schema = next((record.raw for record in document.header if record.name.upper() == "FILE_SCHEMA"), None)
    if schema is None:
        raise ValueError("The STEP file has no FILE_SCHEMA header, so it cannot be exported safely")
    selected = geometry_entity_ids(document)
    if not selected:
        raise ValueError("The STEP file contains no recognized geometry to export")
    escaped_name = Path(filename).name.replace("'", "''")
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    records = [entity.raw for entity in document.entities if entity.entity_id in selected]
    return "\n".join((
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION(('Geometry only'),'2;1');",
        f"FILE_NAME('{escaped_name}','{timestamp}',(),(),'steposcope','','');",
        schema,
        "ENDSEC;",
        "DATA;",
        *records,
        "ENDSEC;",
        "END-ISO-10303-21;",
        "",
    ))


def save_geometry_only_step(document: StepDocument, path: str | Path) -> int:
    """Save a geometry-only exchange file and return its entity count."""

    destination = Path(path)
    source = geometry_only_step(document, destination.name)
    destination.write_text(source, encoding="utf-8")
    return len(geometry_entity_ids(document))
