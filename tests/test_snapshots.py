import json

from step_explorer.geometry.builder import GeometryBuilder, Point3
from step_explorer.main import _load_snapshot_config, _snapshots_main
from step_explorer.snapshots import (
    Element,
    SnapshotPlan,
    bounding_box,
    camera_params,
    collect_elements,
    geometry_event_orders,
    parallel_scale,
    plan_orders,
    run,
)
from step_explorer.step.parser import parse_step

from test_headless import _box_source


def test_default_snapshot_config_comes_from_headless_folder(tmp_path, monkeypatch):
    config_dir = tmp_path / "headless"
    config_dir.mkdir()
    (config_dir / "snapshots.json").write_text('{"interval": 17}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _load_snapshot_config(None)["interval"] == 17


def _elements():
    document = parse_step(_box_source())
    snapshot = GeometryBuilder(document).build()
    return document, collect_elements(document, snapshot)


def test_geometry_events_are_distinct_and_sorted():
    _, elements = _elements()
    events = geometry_event_orders(elements)
    assert events == tuple(sorted(set(events)))
    # Faces are the top significance tier, so the run ends at the last face's order.
    assert events[-1] == max(e.appearance for e in elements if e.kind == "face")


def test_events_collapse_to_the_top_significance_tier():
    # Points/vertices/edges appear on their own but only the two faces advance frames.
    elements = [
        Element(1, 10, "point", None),
        Element(2, 11, "vertex", None),
        Element(3, 12, "polyline", None),
        Element(4, 13, "face", None),
        Element(5, 14, "point", None),
        Element(6, 15, "face", None),
    ]
    assert geometry_event_orders(elements) == (4, 6)


def test_events_fall_back_to_edges_when_there_are_no_faces():
    elements = [
        Element(1, 10, "point", None),
        Element(2, 11, "polyline", None),
        Element(3, 12, "point", None),
        Element(4, 13, "polyline", None),
    ]
    assert geometry_event_orders(elements) == (2, 4)


def test_bounding_box_ignores_stray_placement_points_but_keeps_point_clouds():
    stray = Element(99, 500, "point", Point3(-50.0, 0.0, 50.0))
    corner_a = Element(1, 1, "vertex", Point3(0.0, 0.0, 0.0))
    corner_b = Element(2, 2, "vertex", Point3(1.0, 1.0, 1.0))
    assert bounding_box([stray, corner_a, corner_b]) == (0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    # A file that is nothing but points still frames on those points.
    cloud = [Element(1, 1, "point", Point3(0.0, 0.0, 0.0)), Element(2, 2, "point", Point3(2.0, 0.0, 0.0))]
    assert bounding_box(cloud) == (0.0, 0.0, 0.0, 2.0, 0.0, 0.0)


def test_faces_appear_no_earlier_than_their_points():
    _, elements = _elements()
    point_appearances = [e.appearance for e in elements if e.kind == "point"]
    face_appearances = [e.appearance for e in elements if e.kind == "face"]
    assert face_appearances, "expected the box to tessellate faces"
    # The box lists points before faces, so a face's closure reaches its last point.
    assert max(face_appearances) >= max(point_appearances)


def test_bounding_box_spans_the_full_unit_cube():
    _, elements = _elements()
    box = bounding_box(elements)
    assert box == (-1, -1, -1, 1, 1, 1)


def test_plan_orders_keeps_the_final_frame_and_caps_count():
    events = tuple(range(1, 11))
    assert plan_orders(events, 3, 0) == (3, 6, 9, 10)
    assert plan_orders(events, 5, 0) == (5, 10)
    capped = plan_orders(events, 1, 4)
    assert capped[-1] == events[-1]
    assert len(capped) <= 5
    assert plan_orders((), 5, 0) == ()


def test_camera_zoom_is_shared_across_views():
    _, elements = _elements()
    box = bounding_box(elements)
    iso = camera_params(box, "iso")
    top = camera_params(box, "top")
    front = camera_params(box, "front")
    assert iso["focal_point"] == top["focal_point"] == front["focal_point"]
    assert iso["parallel_scale"] == top["parallel_scale"] == front["parallel_scale"] == parallel_scale(box)
    assert iso["position"] != top["position"]
    assert front["view_up"] == (0, 0, 1)
    assert iso["view_up"] == (0, 1, 0)


def test_over_and_under_are_distinct_side_shots_at_the_same_zoom():
    _, elements = _elements()
    box = bounding_box(elements)
    over = camera_params(box, "over")
    under = camera_params(box, "under")
    iso = camera_params(box, "iso")
    # Same framing scale and center as every other view.
    assert over["parallel_scale"] == under["parallel_scale"] == iso["parallel_scale"]
    assert over["focal_point"] == under["focal_point"] == iso["focal_point"]
    # "over" sits above the part, "under" below it, and both differ from iso.
    assert over["position"][2] > box[2] and under["position"][2] < box[2]
    assert len({over["position"], under["position"], iso["position"]}) == 3
    # Both angled shots keep world +Z as the image's up direction.
    assert over["view_up"] == under["view_up"] == (0, 0, 1)


def test_run_writes_every_frame_view_and_a_manifest(tmp_path):
    document, elements = _elements()
    box = bounding_box(elements)
    orders = plan_orders(geometry_event_orders(elements), 8, 0)
    plan = SnapshotPlan(
        source=tmp_path / "box.step",
        out_dir=tmp_path / "frames",
        interval=8,
        views=("iso", "top"),
        orders=orders,
        total_events=len(geometry_event_orders(elements)),
        width=120,
        height=90,
    )
    plan.out_dir.mkdir(parents=True)

    manifest = run(plan, elements, box, show_arrows=True, show_points=False, caption=True)

    assert manifest["frame_count"] == len(orders)
    for index in range(1, len(orders) + 1):
        for view in ("iso", "top"):
            png = plan.out_dir / f"frame{index:04d}-{view}.png"
            assert png.exists() and png.stat().st_size > 0
    manifest_path = plan.out_dir / "manifest.json"
    loaded = json.loads(manifest_path.read_text())
    assert loaded["bounding_box"] == list(box)
    assert loaded["interval"] == 8
    assert loaded["views"] == ["iso", "top"]
    assert loaded["frames"][-1]["order"] == orders[-1]
    assert loaded["frames"][-1]["counts"]["faces"] == 6


def test_face_count_only_grows_through_the_sequence(tmp_path):
    document, elements = _elements()
    box = bounding_box(elements)
    orders = plan_orders(geometry_event_orders(elements), 6, 0)
    plan = SnapshotPlan(
        source=tmp_path / "box.step",
        out_dir=tmp_path / "frames",
        interval=6,
        views=("iso",),
        orders=orders,
        total_events=0,
        width=120,
        height=90,
    )
    plan.out_dir.mkdir(parents=True)
    manifest = run(plan, elements, box, show_arrows=False, caption=False)
    face_counts = [frame["counts"]["faces"] for frame in manifest["frames"]]
    assert face_counts == sorted(face_counts)
    assert face_counts[-1] == 6


def test_snapshots_cli_end_to_end(tmp_path):
    source = tmp_path / "box.step"
    source.write_text(_box_source(), encoding="utf-8")
    out = tmp_path / "out"

    code = _snapshots_main([str(source), "--out", str(out), "--interval", "8", "--views", "iso", "--yes", "--quiet"])

    assert code == 0
    run_dirs = [path for path in out.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "manifest.json").exists()
    assert list(run_dirs[0].glob("frame*.png"))
