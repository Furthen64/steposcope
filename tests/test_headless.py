import json

import pytest

from step_explorer.headless import _face_color, _face_label_position, build_report, process_file, render_png
from step_explorer.geometry.builder import GeometryBuilder, Mesh, Point3
from step_explorer.main import _render_main
from step_explorer.step.parser import parse_step


def _box_source(flipped_face: int | None = None, low: int = -1, high: int = 1) -> str:
    points = ((low,low,low),(high,low,low),(high,high,low),(low,high,low),(low,low,high),(high,low,high),(high,high,high),(low,high,high))
    faces = ((0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7))
    lines = ["ISO-10303-21;DATA;"]
    for index, point in enumerate(points, 1):
        lines.append(f"#{index}=CARTESIAN_POINT('',({point[0]}.,{point[1]}.,{point[2]}.));")
    normals = ((0,0,-1),(0,0,1),(0,-1,0),(1,0,0),(0,1,0),(-1,0,0))
    for index, normal in enumerate(normals, 9):
        lines.append(f"#{index}=DIRECTION('',({normal[0]}.,{normal[1]}.,{normal[2]}.));")
    next_id = 15
    face_ids = []
    for face_number, vertices in enumerate(faces):
        oriented = []
        for start, end in zip(vertices, vertices[1:] + vertices[:1]):
            vertex_start, vertex_end, edge, occurrence = next_id, next_id + 1, next_id + 2, next_id + 3
            next_id += 4
            lines.append(f"#{vertex_start}=VERTEX_POINT('',#{start + 1});")
            lines.append(f"#{vertex_end}=VERTEX_POINT('',#{end + 1});")
            lines.append(f"#{edge}=EDGE_CURVE('',#{vertex_start},#{vertex_end},$,.T.);")
            lines.append(f"#{occurrence}=ORIENTED_EDGE('',*,*,#{edge},.T.);")
            oriented.append(occurrence)
        loop, bound, placement, surface, face_id = range(next_id, next_id + 5)
        next_id += 5
        lines.append(f"#{loop}=EDGE_LOOP('',({','.join('#' + str(value) for value in oriented)}));")
        lines.append(f"#{bound}=FACE_OUTER_BOUND('',#{loop},.T.);")
        lines.append(f"#{placement}=AXIS2_PLACEMENT_3D('',#{vertices[0] + 1},#{face_number + 9},$);")
        lines.append(f"#{surface}=PLANE('',#{placement});")
        sense = "F" if flipped_face == face_number else "T"
        lines.append(f"#{face_id}=ADVANCED_FACE('',(#{bound}),#{surface},.{sense}.);")
        face_ids.append(face_id)
    lines.append(f"#{next_id}=CLOSED_SHELL('',({','.join('#' + str(value) for value in face_ids)}));")
    lines.append("ENDSEC;END-ISO-10303-21;")
    return "\n".join(lines)


def test_box_report_flags_the_flipped_face(tmp_path):
    document = parse_step(_box_source(flipped_face=2))
    meshes = GeometryBuilder(document).build().faces
    report = build_report(tmp_path / "box.step", document, meshes)

    solid = report["solids"][0]
    inconsistent = [face["entity_id"] for face in solid["faces"] if not face["ok"]]
    assert solid["verdict"] == "inconsistent"
    assert solid["n_boundary_edges"] == 0
    assert len(solid["bad_edges"]) == 4
    assert inconsistent == [meshes[2].entity_id]
    json.dumps(report)


def test_far_from_origin_box_is_consistent(tmp_path):
    source = _box_source(low=50, high=70)
    document = parse_step(source)
    report = build_report(tmp_path / "far-box.step", document, GeometryBuilder(document).build().faces)

    solid = report["solids"][0]
    assert any(face["signed_volume_contribution"] < 0 for face in solid["faces"])
    assert solid["verdict"] == "consistent"
    assert solid["bad_edges"] == []
    assert all(face["ok"] for face in solid["faces"])


def test_headless_outputs_are_deterministic(tmp_path):
    source = tmp_path / "box.step"
    source.write_text(_box_source(), encoding="utf-8")

    process_file(source, tmp_path / "first" / "box", 160, 120, "iso", "normals")
    process_file(source, tmp_path / "second" / "box", 160, 120, "iso", "normals")

    assert (tmp_path / "first" / "box.json").read_bytes() == (tmp_path / "second" / "box.json").read_bytes()
    assert (tmp_path / "first" / "box.png").read_bytes() == (tmp_path / "second" / "box.png").read_bytes()


def test_face_colors_are_distinct_stable_and_render_deterministically(tmp_path):
    source = tmp_path / "box.step"
    source.write_text(_box_source(), encoding="utf-8")
    meshes = GeometryBuilder(parse_step(_box_source())).build().faces

    colors = [_face_color(mesh.entity_id) for mesh in meshes]
    assert len(set(colors)) == len(colors)
    assert _face_color(meshes[0].entity_id) == colors[0]
    assert _face_color(100) != _face_color(101)

    process_file(source, tmp_path / "first" / "box", 160, 120, "iso", "faces")
    process_file(source, tmp_path / "second" / "box", 160, 120, "iso", "faces")

    assert (tmp_path / "first" / "box.png").read_bytes() == (tmp_path / "second" / "box.png").read_bytes()


def test_views_flag_writes_named_views_and_default_stays_single_view(tmp_path):
    source = tmp_path / "box.step"
    source.write_text(_box_source(), encoding="utf-8")

    views_out = tmp_path / "views"
    assert _render_main([
        str(source), "--out", str(views_out), "--width", "160", "--height", "120",
        "--mode", "faces", "--views", "iso,front,top,right",
    ]) == 0
    assert {path.name for path in views_out.glob("*.png")} == {
        "box-iso.png", "box-front.png", "box-top.png", "box-right.png",
    }
    assert not (views_out / "box.png").exists()

    default_out = tmp_path / "default"
    assert _render_main([
        str(source), "--out", str(default_out), "--width", "160", "--height", "120",
    ]) == 0
    assert {path.name for path in default_out.glob("*.png")} == {"box.png"}


def test_faces_mode_renders_a_large_entity_id_and_skips_degenerate_labels(tmp_path):
    large_id = 10**30 + 123
    mesh = Mesh(
        large_id,
        (Point3(-1, -1, 0), Point3(1, -1, 0), Point3(1, 1, 0), Point3(-1, 1, 0)),
        ((0, 1, 2), (0, 2, 3)),
    )
    report = {"solids": [{"faces": [{"entity_id": large_id, "ok": True}]}]}

    render_png([mesh], report, tmp_path / "large.png", 160, 120, "top", "faces")

    assert (tmp_path / "large.png").stat().st_size > 0
    assert _face_label_position(Mesh(large_id, mesh.points, ()), 0.1) is None


def test_unbounded_sphere_face_builds_a_closed_consistent_mesh(tmp_path):
    source = """ISO-10303-21;DATA;
    #1=CARTESIAN_POINT('',(0.,0.,0.));
    #2=DIRECTION('',(0.,0.,1.));
    #3=DIRECTION('',(1.,0.,0.));
    #4=AXIS2_PLACEMENT_3D('',#1,#2,#3);
    #5=SPHERICAL_SURFACE('',#4,2.);
    #6=ADVANCED_FACE('',(),#5,.T.);
    #7=CLOSED_SHELL('',(#6));
    ENDSEC;END-ISO-10303-21;"""
    document = parse_step(source)
    meshes = GeometryBuilder(document).build().faces
    report = build_report(tmp_path / "sphere.step", document, meshes)

    assert len(meshes) == 1
    assert report["solids"][0]["verdict"] == "consistent"
    assert report["solids"][0]["n_boundary_edges"] == 0
    assert report["solids"][0]["total_signed_volume"] > 0


@pytest.mark.slow
def test_corpus_reports_are_wellformed_and_verdicts_match_their_evidence(corpus_models):
    assert corpus_models, "no STEP corpus found for the report sweep"
    for path, document, snapshot in corpus_models:
        report = build_report(path, document, list(snapshot.faces))
        json.dumps(report)
        for solid in report["solids"]:
            verdict = solid["verdict"]
            assert verdict in {"open", "unreliable", "inconsistent", "consistent"}, (path, verdict)
            if verdict == "consistent":
                assert solid["n_boundary_edges"] == 0, path
                assert solid["bad_edges"] == [], path
                assert solid["n_inconsistent"] == 0, path
                assert all(face["ok"] for face in solid["faces"]), path
            if solid["n_boundary_edges"]:
                assert verdict == "open", path
