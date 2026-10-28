import math
from pathlib import Path

import pytest

from step_explorer.geometry import ControlNet, ControlNetStatus, GeometryBuilder, Point3
from step_explorer.geometry.completeness import assess_completeness
from step_explorer.step.parser import parse_step
from step_explorer.step.values import StepAggregate, StepNumber, StepReference


def test_same_direction_pairs_expose_edge_and_both_face_uses():
    source = Path(__file__).resolve().parents[1] / "tests/corpus/BROKEN_examples/same_direction_pairs/holefillets2_issue_2samedirpairs.step"
    document = parse_step(source.read_text(encoding="utf-8"))
    report = assess_completeness(document, GeometryBuilder(document).build())

    assert report.inconsistent_edge_count == len(report.same_direction_pairs) == 2
    assert [pair.edge_id for pair in report.same_direction_pairs] == [136, 137]
    assert [[use.face_id for use in pair.uses] for pair in report.same_direction_pairs] == [
        [142, 175], [142, 176],
    ]
    assert all(use.forward for pair in report.same_direction_pairs for use in pair.uses)
    assert all(document.entity(use.oriented_edge_id).type_name == "ORIENTED_EDGE"
               for pair in report.same_direction_pairs for use in pair.uses)


def test_empty_cartesian_point_is_ignored_in_full_and_incremental_builds():
    document = parse_step(
        "ISO-10303-21;DATA;#1=CARTESIAN_POINT();ENDSEC;END-ISO-10303-21;"
    )
    builder = GeometryBuilder(document)
    assert builder.build().points == {}
    assert builder.build(-1).points == {}
    assert builder.build(0).points == {}


def _polygon_face_source(loops, *, include_plane=False):
    """Build a small STEP face from straight-edged polygon loops."""

    entities = []
    next_id = 1

    def add(statement):
        nonlocal next_id
        entity_id = next_id
        entities.append(f"#{entity_id}={statement}")
        next_id += 1
        return entity_id

    plane_id = None
    if include_plane:
        origin_id = add("CARTESIAN_POINT('',(0.,0.,0.));")
        normal_id = add("DIRECTION('',(0.,0.,1.));")
        x_axis_id = add("DIRECTION('',(1.,0.,0.));")
        placement_id = add(
            f"AXIS2_PLACEMENT_3D('',#{origin_id},#{normal_id},#{x_axis_id});"
        )
        plane_id = add(f"PLANE('',#{placement_id});")

    bound_ids = []
    for loop_index, coordinates in enumerate(loops):
        vertex_ids = []
        for x, y, z in coordinates:
            coordinate = ",".join(f"{value:.12g}" for value in (x, y, z))
            point_id = add(f"CARTESIAN_POINT('',({coordinate}));")
            vertex_ids.append(add(f"VERTEX_POINT('',#{point_id});"))

        oriented_ids = []
        for index, start_id in enumerate(vertex_ids):
            end_id = vertex_ids[(index + 1) % len(vertex_ids)]
            edge_id = add(f"EDGE_CURVE('',#{start_id},#{end_id},$,.T.);")
            oriented_ids.append(add(f"ORIENTED_EDGE('',*,*,#{edge_id},.T.);"))
        loop_id = add(f"EDGE_LOOP('',({','.join(f'#{item}' for item in oriented_ids)}));")
        bound_type = "FACE_OUTER_BOUND" if loop_index == 0 else "FACE_BOUND"
        bound_ids.append(add(f"{bound_type}('',#{loop_id},.T.);"))

    surface = f"#{plane_id}" if plane_id is not None else "$"
    bounds = ",".join(f"#{item}" for item in bound_ids)
    add(f"ADVANCED_FACE('',({bounds}),{surface},.T.);")
    return "ISO-10303-21;DATA;\n" + "\n".join(entities) + "\nENDSEC;END-ISO-10303-21;"


def test_points_vertices_and_edges_are_interpreted():
    source = """ISO-10303-21;DATA;
    #1=CARTESIAN_POINT('',(0.,0.,0.));
    #2=CARTESIAN_POINT('',(1.,0.,0.));
    #3=VERTEX_POINT('',#1);
    #4=VERTEX_POINT('',#2);
    #5=EDGE_CURVE('',#3,#4,$,.T.);
    ENDSEC;END-ISO-10303-21;"""
    snapshot = GeometryBuilder(parse_step(source)).build()
    assert snapshot.points[1] == Point3(0.0, 0.0, 0.0)
    assert snapshot.vertices[3] == Point3(0.0, 0.0, 0.0)
    assert snapshot.polylines[0].points == (Point3(0.0, 0.0, 0.0), Point3(1.0, 0.0, 0.0))


def test_control_net_preserves_grid_order():
    p00, p01, p02 = (
        Point3(0.0, 0.0, 0.0),
        Point3(0.0, 1.0, 0.0),
        Point3(0.0, 2.0, 0.0),
    )
    p10, p11, p12 = (
        Point3(1.0, 0.0, 0.0),
        Point3(1.0, 1.0, 0.0),
        Point3(1.0, 2.0, 0.0),
    )
    control_net = ControlNet(42, ((p00, p01, p02), (p10, p11, p12)))

    assert (control_net.row_count, control_net.column_count) == (2, 3)
    assert control_net.rows == ((p00, p01, p02), (p10, p11, p12))
    assert control_net.point_markers == (p00, p01, p02, p10, p11, p12)


def test_control_net_density_keeps_an_evenly_distributed_coarse_grid():
    control_net = ControlNet(
        42,
        tuple(
            tuple(Point3(float(row), float(column), 0.0) for column in range(5))
            for row in range(5)
        ),
    )

    coarse = control_net.at_density(0)
    assert coarse.rows == (
        (Point3(0.0, 0.0, 0.0), Point3(0.0, 4.0, 0.0)),
        (Point3(4.0, 0.0, 0.0), Point3(4.0, 4.0, 0.0)),
    )
    assert control_net.at_density(100) == control_net


@pytest.mark.parametrize(
    "rows, message",
    (
        (((Point3(0.0, 0.0, 0.0), Point3(0.0, 1.0, 0.0)),), "at least two rows"),
        (((Point3(0.0, 0.0, 0.0),), (Point3(1.0, 0.0, 0.0),)), "at least two columns"),
        (
            (
                (Point3(0.0, 0.0, 0.0), Point3(0.0, 1.0, 0.0)),
                (Point3(1.0, 0.0, 0.0),),
            ),
            "rectangular",
        ),
    ),
)
def test_control_net_rejects_incomplete_grids(rows, message):
    with pytest.raises(ValueError, match=message):
        ControlNet(42, rows)


def test_control_net_status_describes_each_playback_state():
    assert ControlNetStatus(1, "ready").description == "Control net ready"
    assert ControlNetStatus(2, "invalid").description == "Invalid control-point grid"
    assert ControlNetStatus(3, "waiting", (4,)).description == "Waiting for 1 control point"
    assert ControlNetStatus(4, "waiting", (5, 6)).description == "Waiting for 2 control points"


def test_bspline_surface_extracts_a_complete_control_net_after_forward_reference_arrives():
    source = """ISO-10303-21;DATA;
    #1=CARTESIAN_POINT('',(0.,0.,0.));
    #2=CARTESIAN_POINT('',(0.,1.,0.));
    #3=CARTESIAN_POINT('',(1.,0.,0.));
    #5=B_SPLINE_SURFACE_WITH_KNOTS('',1,1,((#1,#2),(#3,#4)),.UNSPECIFIED.,.F.,.F.,.F.,(2,2),(2,2),(0.,1.),(0.,1.),.UNSPECIFIED.);
    #4=CARTESIAN_POINT('',(1.,1.,0.));
    ENDSEC;END-ISO-10303-21;"""
    document = parse_step(source)

    incomplete_snapshot = GeometryBuilder(document).build(3)
    assert incomplete_snapshot.control_nets == []
    assert incomplete_snapshot.control_net_statuses == [ControlNetStatus(5, "waiting", (4,))]

    snapshot = GeometryBuilder(document).build()
    assert len(snapshot.control_nets) == 1
    assert snapshot.control_net_statuses == [ControlNetStatus(5, "ready")]
    control_net = snapshot.control_nets[0]
    assert control_net.entity_id == 5
    assert control_net.rows == (
        (Point3(0.0, 0.0, 0.0), Point3(0.0, 1.0, 0.0)),
        (Point3(1.0, 0.0, 0.0), Point3(1.0, 1.0, 0.0)),
    )


def test_bspline_surface_skips_a_malformed_control_grid():
    source = """ISO-10303-21;DATA;
    #1=CARTESIAN_POINT('',(0.,0.,0.));
    #2=CARTESIAN_POINT('',(0.,1.,0.));
    #3=CARTESIAN_POINT('',(1.,0.,0.));
    #4=B_SPLINE_SURFACE_WITH_KNOTS('',1,1,((#1,#2),(#3)),.UNSPECIFIED.,.F.,.F.,.F.,(2,2),(2,2),(0.,1.),(0.,1.),.UNSPECIFIED.);
    ENDSEC;END-ISO-10303-21;"""

    snapshot = GeometryBuilder(parse_step(source)).build()
    assert snapshot.control_nets == []
    assert snapshot.control_net_statuses == [ControlNetStatus(4, "invalid")]


@pytest.mark.slow
def test_bspline_surfaces_wait_for_forward_control_points_during_playback(small_corpus):
    """A surface truncated before its last control point must report waiting."""

    checked = 0
    for path in small_corpus:
        document = parse_step(path.read_text(encoding="utf-8", errors="replace"))
        order = {entity.entity_id: index for index, entity in enumerate(document.entities)}
        surfaces = [
            entity for entity in document.entities
            if entity.type_name.upper() == "B_SPLINE_SURFACE_WITH_KNOTS"
        ]
        if not surfaces:
            continue
        ready = GeometryBuilder(document).build()
        ready_states = {status.entity_id: status.state for status in ready.control_net_statuses}
        for surface in surfaces:
            point_ids = [
                reference
                for reference in surface.references
                if reference in order
                and document.entity(reference).type_name.upper() == "CARTESIAN_POINT"
            ]
            if not point_ids or ready_states.get(surface.entity_id) != "ready":
                continue
            last_index = max(order[point_id] for point_id in point_ids)
            surface_index = order[surface.entity_id]
            if last_index <= surface_index:
                continue
            waiting = GeometryBuilder(document).build(last_index - 1)
            statuses = {status.entity_id: status for status in waiting.control_net_statuses}
            assert surface.entity_id in statuses, (path, surface.entity_id)
            assert statuses[surface.entity_id].state == "waiting", (path, surface.entity_id)
            assert surface.entity_id not in {net.entity_id for net in waiting.control_nets}, path
            checked += 1
    assert checked, "no corpus model exercised forward-referenced control points"


def test_face_loop_respects_oriented_edge_sense():
    source = """ISO-10303-21;DATA;
    #1=CARTESIAN_POINT('',(0.,0.,0.));
    #2=CARTESIAN_POINT('',(1.,0.,0.));
    #3=CARTESIAN_POINT('',(1.,1.,0.));
    #4=CARTESIAN_POINT('',(0.,1.,0.));
    #5=VERTEX_POINT('',#1);
    #6=VERTEX_POINT('',#2);
    #7=VERTEX_POINT('',#3);
    #8=VERTEX_POINT('',#4);
    #9=EDGE_CURVE('',#5,#6,$,.T.);
    #10=EDGE_CURVE('',#7,#6,$,.T.);
    #11=EDGE_CURVE('',#7,#8,$,.T.);
    #12=EDGE_CURVE('',#8,#5,$,.T.);
    #13=ORIENTED_EDGE('',*,*,#9,.T.);
    #14=ORIENTED_EDGE('',*,*,#10,.F.);
    #15=ORIENTED_EDGE('',*,*,#11,.T.);
    #16=ORIENTED_EDGE('',*,*,#12,.T.);
    #17=EDGE_LOOP('',(#13,#14,#15,#16));
    #18=FACE_OUTER_BOUND('',#17,.T.);
    #19=ADVANCED_FACE('',(#18),$,.T.);
    ENDSEC;END-ISO-10303-21;"""

    snapshot = GeometryBuilder(parse_step(source)).build()

    assert len(snapshot.faces) == 1
    assert snapshot.faces[0].points == (
        Point3(0.0, 0.0, 0.0),
        Point3(1.0, 0.0, 0.0),
        Point3(1.0, 1.0, 0.0),
        Point3(0.0, 1.0, 0.0),
    )


def test_planar_face_triangulation_preserves_an_inner_hole():
    source = _polygon_face_source(
        (
            ((0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)),
            ((1, 1, 0), (3, 1, 0), (3, 3, 0), (1, 3, 0)),
        ),
        include_plane=True,
    )

    face = GeometryBuilder(parse_step(source)).build().faces[0]
    area = 0.0
    for a, b, c in face.triangles:
        first, second, third = (face.points[index] for index in (a, b, c))
        area += abs(
            (second.x - first.x) * (third.y - first.y)
            - (second.y - first.y) * (third.x - first.x)
        ) / 2

    assert area == pytest.approx(12.0)


@pytest.mark.parametrize("include_plane", (False, True))
def test_nonplanar_bounded_face_never_reaches_planar_triangulator(
    include_plane, monkeypatch
):
    source = _polygon_face_source(
        (((0, 0, 0), (2, 0, 0), (2, 2, 1), (0, 2, 0)),),
        include_plane=include_plane,
    )
    triangulation_inputs = []

    def record_triangulation(loops):
        triangulation_inputs.append(loops)
        return None

    monkeypatch.setattr(
        GeometryBuilder, "_triangulate_loops", staticmethod(record_triangulation)
    )
    snapshot = GeometryBuilder(parse_step(source)).build()

    assert snapshot.faces == []
    assert sum(line.category == "edge" for line in snapshot.polylines) == 4
    assert triangulation_inputs == []


def test_circle_edge_is_sampled_as_an_arc():
    source = """ISO-10303-21;DATA;
    #1=CARTESIAN_POINT('',(0.,0.,0.));
    #2=DIRECTION('',(0.,0.,1.));
    #3=DIRECTION('',(1.,0.,0.));
    #4=AXIS2_PLACEMENT_3D('',#1,#2,#3);
    #5=CIRCLE('',#4,1.);
    #6=CARTESIAN_POINT('',(1.,0.,0.));
    #7=CARTESIAN_POINT('',(-1.,0.,0.));
    #8=VERTEX_POINT('',#6);
    #9=VERTEX_POINT('',#7);
    #10=EDGE_CURVE('',#8,#9,#5,.T.);
    ENDSEC;END-ISO-10303-21;"""

    edge = GeometryBuilder(parse_step(source)).build().polylines[0]

    assert len(edge.points) == 19
    assert edge.points[0] == Point3(1.0, 0.0, 0.0)
    assert edge.points[-1] == Point3(-1.0, 0.0, 0.0)
    assert max(point.y for point in edge.points) == 1.0


def test_ellipse_edge_is_sampled_as_an_arc():
    source = """ISO-10303-21;DATA;
    #1=CARTESIAN_POINT('',(0.,0.,0.));
    #2=DIRECTION('',(0.,0.,1.));
    #3=DIRECTION('',(1.,0.,0.));
    #4=AXIS2_PLACEMENT_3D('',#1,#2,#3);
    #5=ELLIPSE('',#4,2.,1.);
    #6=CARTESIAN_POINT('',(2.,0.,0.));
    #7=CARTESIAN_POINT('',(-2.,0.,0.));
    #8=VERTEX_POINT('',#6);
    #9=VERTEX_POINT('',#7);
    #10=EDGE_CURVE('',#8,#9,#5,.T.);
    ENDSEC;END-ISO-10303-21;"""

    edge = GeometryBuilder(parse_step(source)).build().polylines[0]

    assert len(edge.points) == 19
    assert edge.points[0] == Point3(2.0, 0.0, 0.0)
    assert edge.points[-1] == Point3(-2.0, 0.0, 0.0)
    assert max(point.y for point in edge.points) == 1.0


def test_toroidal_fillet_patches_are_sampled_on_their_surfaces():
    """A non-planar fillet boundary must not be flattened by contour triangulation."""
    source = """ISO-10303-21;DATA;
    #1=CARTESIAN_POINT('',(0.,0.,0.));
    #2=DIRECTION('',(0.,0.,1.));
    #3=DIRECTION('',(1.,0.,0.));
    #4=AXIS2_PLACEMENT_3D('',#1,#2,#3);
    #5=TOROIDAL_SURFACE('',#4,4.,1.);
    #6=CARTESIAN_POINT('',(5.,0.,0.));
    #7=CARTESIAN_POINT('',(0.,5.,0.));
    #8=CARTESIAN_POINT('',(0.,4.,1.));
    #9=CARTESIAN_POINT('',(4.,0.,1.));
    #10=VERTEX_POINT('',#6); #11=VERTEX_POINT('',#7);
    #12=VERTEX_POINT('',#8); #13=VERTEX_POINT('',#9);
    #14=CIRCLE('',#4,5.);
    #15=CARTESIAN_POINT('',(0.,4.,0.));
    #16=DIRECTION('',(0.,1.,0.));
    #17=AXIS2_PLACEMENT_3D('',#15,#3,#16);
    #18=CIRCLE('',#17,1.);
    #19=CARTESIAN_POINT('',(0.,0.,1.));
    #20=AXIS2_PLACEMENT_3D('',#19,#2,#3);
    #21=CIRCLE('',#20,4.);
    #22=CARTESIAN_POINT('',(4.,0.,0.));
    #23=DIRECTION('',(0.,1.,0.));
    #24=AXIS2_PLACEMENT_3D('',#22,#23,#3);
    #25=CIRCLE('',#24,1.);
    #26=EDGE_CURVE('',#10,#11,#14,.T.);
    #27=EDGE_CURVE('',#11,#12,#18,.T.);
    #28=EDGE_CURVE('',#12,#13,#21,.F.);
    #29=EDGE_CURVE('',#13,#10,#25,.T.);
    #30=ORIENTED_EDGE('',*,*,#26,.T.); #31=ORIENTED_EDGE('',*,*,#27,.T.);
    #32=ORIENTED_EDGE('',*,*,#28,.T.); #33=ORIENTED_EDGE('',*,*,#29,.T.);
    #34=EDGE_LOOP('',(#30,#31,#32,#33));
    #35=FACE_OUTER_BOUND('',#34,.T.);
    #36=ADVANCED_FACE('',(#35),#5,.T.);
    ENDSEC;END-ISO-10303-21;"""

    face = GeometryBuilder(parse_step(source)).build().faces[0]

    assert face.type_name == "TOROIDAL_SURFACE"
    assert face.curved
    assert len(face.triangles) == 162
    assert all(
        math.hypot(math.hypot(point.x, point.y) - 4.0, point.z) == pytest.approx(1.0)
        for point in face.points
    )
    assert all(
        point.x >= -1e-9 and point.y >= -1e-9 and -1e-9 <= point.z <= 1.0 + 1e-9
        for point in face.points
    )


def _placement_frame(builder, document, placement_id):
    placement = document.entity(placement_id)
    references = [
        argument.entity_id
        for argument in placement.arguments
        if isinstance(argument, StepReference)
    ]
    origin = builder._cartesian(references[0])
    axis = builder._normalized(builder._direction(references[1]))
    return origin, axis


def _surface_numbers(entity):
    return [
        float(argument.value)
        for argument in entity.arguments
        if isinstance(argument, StepNumber)
    ]


@pytest.mark.slow
def test_curved_faces_sample_their_declared_analytic_surfaces(corpus_models):
    """Every meshed cylinder, cone and torus segment must lie on its surface."""

    verified = {"CYLINDRICAL_SURFACE": 0, "CONICAL_SURFACE": 0, "TOROIDAL_SURFACE": 0}
    for path, document, snapshot in corpus_models:
        builder = GeometryBuilder(document)
        for face in snapshot.faces:
            if face.type_name not in verified:
                continue
            assert face.curved, (path, face.entity_id)
            assert all(all(index < len(face.points) for index in triangle) for triangle in face.triangles), path
            surface_id = next(
                argument.entity_id
                for argument in document.entity(face.entity_id).arguments
                if isinstance(argument, StepReference)
            )
            surface = document.entity(surface_id)
            placement_id = next(
                argument.entity_id
                for argument in surface.arguments
                if isinstance(argument, StepReference)
            )
            origin, axis = _placement_frame(builder, document, placement_id)
            numbers = _surface_numbers(surface)
            for point in face.points:
                delta = (point.x - origin.x, point.y - origin.y, point.z - origin.z)
                axial = builder._dot(delta, axis)
                radial_vector = tuple(delta[index] - axial * axis[index] for index in range(3))
                radial = math.sqrt(builder._dot(radial_vector, radial_vector))
                if face.type_name == "CYLINDRICAL_SURFACE":
                    assert radial == pytest.approx(numbers[0], rel=5e-3, abs=1e-4), (path, face.entity_id)
                elif face.type_name == "CONICAL_SURFACE":
                    assert radial == pytest.approx(abs(numbers[0] + axial * math.tan(numbers[1])), rel=5e-3, abs=1e-4), (path, face.entity_id)
                else:
                    assert math.hypot(radial - numbers[0], axial) == pytest.approx(numbers[1], rel=5e-3, abs=1e-4), (path, face.entity_id)
            verified[face.type_name] += 1
    assert verified["CYLINDRICAL_SURFACE"] and verified["CONICAL_SURFACE"], "corpus lacked cylinders and cones"


def _polygon_contains(polygon, point):
    """Ray-cast containment in the polygon's own 2D plane."""

    inside = False
    count = len(polygon)
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        if (ay > point[1]) != (by > point[1]):
            edge_x = ax + (point[1] - ay) * (bx - ax) / (by - ay)
            if point[0] < edge_x:
                inside = not inside
    return inside


@pytest.mark.slow
def test_planar_faces_never_bridge_their_inner_loops(corpus_models):
    """Triangulation of a holed plate may not draw chords straight across a hole."""

    exercised = 0
    for path, document, snapshot in corpus_models:
        builder = GeometryBuilder(document)
        meshes = {face.entity_id: face for face in snapshot.faces}
        edge_points = {line.entity_id: line.points for line in snapshot.polylines}
        for entity in document.entities:
            if entity.type_name.upper() != "ADVANCED_FACE":
                continue
            face = meshes.get(entity.entity_id)
            if face is None or face.type_name != "PLANE":
                continue
            bound_refs = [
                item.entity_id
                for argument in entity.arguments
                for item in (argument.values if isinstance(argument, StepAggregate) else ())
                if isinstance(item, StepReference)
            ]
            if not any(
                document.entity(bound_id).type_name.upper() == "FACE_OUTER_BOUND"
                for bound_id in bound_refs
            ):
                continue
            holes = []
            for bound_id in bound_refs:
                bound = document.entity(bound_id)
                if bound.type_name.upper() != "FACE_BOUND":
                    continue
                loop_id = next(
                    argument.entity_id
                    for argument in bound.arguments
                    if isinstance(argument, StepReference)
                )
                loop = document.entity(loop_id)
                chain = []
                for item in loop.arguments[1].values:
                    oriented = document.entity(item.entity_id)
                    points = edge_points.get(oriented.arguments[3].entity_id)
                    if points is None:
                        chain = []
                        break
                    if oriented.arguments[-1].value.upper() == "F":
                        points = points[::-1]
                    chain.extend(points[:-1])
                if len(chain) >= 3:
                    holes.append(chain)
            if not holes:
                continue
            exercised += 1
            surface = document.entity(next(
                argument.entity_id
                for argument in entity.arguments
                if isinstance(argument, StepReference)
            ))
            placement = document.entity(next(
                argument.entity_id
                for argument in surface.arguments
                if isinstance(argument, StepReference)
            ))
            references = [
                argument.entity_id
                for argument in placement.arguments
                if isinstance(argument, StepReference)
            ]
            origin = builder._cartesian(references[0])
            normal = builder._normalized(builder._direction(references[1]))
            tangent = builder._normalized(builder._direction(references[2]))
            bitangent = (
                normal[1] * tangent[2] - normal[2] * tangent[1],
                normal[2] * tangent[0] - normal[0] * tangent[2],
                normal[0] * tangent[1] - normal[1] * tangent[0],
            )

            def flatten(point):
                delta = (point.x - origin.x, point.y - origin.y, point.z - origin.z)
                return (builder._dot(delta, tangent), builder._dot(delta, bitangent))

            polygons = [
                [flatten(point) for point in hole]
                for hole in holes
                if len({(round(value.x, 6), round(value.y, 6), round(value.z, 6)) for value in hole}) >= 3
            ]
            for triangle in face.triangles:
                centroid = tuple(
                    sum(getattr(face.points[index], coordinate) for index in triangle) / 3
                    for coordinate in ("x", "y", "z")
                )
                flat = flatten(Point3(*centroid))
                for polygon in polygons:
                    assert not _polygon_contains(polygon, flat), (path, entity.entity_id, triangle)
    assert exercised, "no holed planar face found in the corpus"


@pytest.mark.slow
def test_complex_surfaces_are_never_meshed_as_planar_caps(corpus_models):
    for path, document, snapshot in corpus_models:
        meshes = {face.entity_id: face for face in snapshot.faces}
        for entity in document.entities:
            if entity.type_name.upper() != "ADVANCED_FACE":
                continue
            surface_id = next(
                (argument.entity_id for argument in entity.arguments if isinstance(argument, StepReference)),
                None,
            )
            surface = document.entity(surface_id or -1)
            if surface and surface.type_name.upper() == "COMPLEX":
                face = meshes.get(entity.entity_id)
                if face is not None:
                    assert face.type_name != "PLANE", (path, entity.entity_id)
