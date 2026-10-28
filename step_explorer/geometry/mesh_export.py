"""Export one triangulated preview mesh to common polygon formats."""

from math import sqrt
from pathlib import Path


def save_mesh_as_stl(mesh, path: str | Path) -> int:
    """Write a mesh as an ASCII STL file and return its triangle count."""

    _validate_mesh(mesh)
    destination = Path(path)
    with destination.open("w", encoding="utf-8", newline="\n") as output:
        output.write(f"solid face_{mesh.entity_id}\n")
        for first, second, third in mesh.triangles:
            a, b, c = (mesh.points[index] for index in (first, second, third))
            normal = _unit_normal(a, b, c)
            output.write(f"  facet normal {_format_vector(normal)}\n")
            output.write("    outer loop\n")
            for point in (a, b, c):
                output.write(f"      vertex {_format_vector((point.x, point.y, point.z))}\n")
            output.write("    endloop\n  endfacet\n")
        output.write(f"endsolid face_{mesh.entity_id}\n")
    return len(mesh.triangles)


def save_mesh_as_obj(mesh, path: str | Path) -> int:
    """Write a mesh as a Wavefront OBJ file and return its triangle count."""

    _validate_mesh(mesh)
    destination = Path(path)
    with destination.open("w", encoding="utf-8", newline="\n") as output:
        output.write(f"# Face #{mesh.entity_id} exported by steposcope\n")
        output.write(f"o face_{mesh.entity_id}\n")
        for point in mesh.points:
            output.write(f"v {_format_vector((point.x, point.y, point.z))}\n")
        for first, second, third in mesh.triangles:
            output.write(f"f {first + 1} {second + 1} {third + 1}\n")
    return len(mesh.triangles)


def _validate_mesh(mesh) -> None:
    if not mesh.points or not mesh.triangles:
        raise ValueError("The analyzed face has no preview triangles to export")
    if any(index < 0 or index >= len(mesh.points) for triangle in mesh.triangles for index in triangle):
        raise ValueError("The analyzed face contains an invalid triangle index")


def _unit_normal(first, second, third) -> tuple[float, float, float]:
    ab = (second.x - first.x, second.y - first.y, second.z - first.z)
    ac = (third.x - first.x, third.y - first.y, third.z - first.z)
    normal = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    magnitude = sqrt(sum(component * component for component in normal))
    if magnitude <= 1e-30:
        return 0.0, 0.0, 0.0
    return tuple(component / magnitude for component in normal)


def _format_vector(vector) -> str:
    return " ".join(f"{component:.9g}" for component in vector)
