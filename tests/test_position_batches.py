from dataclasses import replace
from time import perf_counter
from unittest.mock import Mock

from step_explorer.geometry.builder import GeometrySnapshot, Point3


def render(view, snapshot, selected=None, **options):
    view._initialized = True
    view.autozoom = False
    view._profiled_render = Mock()
    view.show_snapshot(snapshot, selected_id=selected, **options)


def test_large_paused_cloud_preserves_all_points_with_bounded_actor_count(view):
    points = {i: Point3(float(i % 200), float(i // 200), 0) for i in range(40_000)}
    vertices = {40_000 + i: Point3(float(i), 0, 0) for i in range(3000)}
    snapshot = GeometrySnapshot(points=points, vertices=vertices)
    started = perf_counter()
    render(view, snapshot, selected=5)
    print(f'40,000 points + 3,000 vertices: {perf_counter() - started:.3f}s')
    assert len(view._actors) == 3  # Two batches and one selected marker.
    assert sum(a.GetMapper().GetInput().GetNumberOfPoints() for a in view._actors) == 43_000
    assert view._actors_by_entity[5][0].GetProperty().GetPointSize() == 10
    assert len(view._position_batch_actors) == 2
    render(view, snapshot, show_points=False, fast_preview=True)
    assert view._actors == []
    assert view._position_batch_actors == []


def test_incremental_batch_applies_point_changes_deletions_and_selection(view):
    snapshot = GeometrySnapshot(points={1: Point3(0, 0, 0), 2: Point3(1, 0, 0)})
    render(view, snapshot, selected=1)
    changed = Point3(5, 0, 0)
    updated = replace(snapshot, points={2: changed, 3: Point3(7, 0, 0)},
                      render_updates={1: None, 2: ('point', changed), 3: ('point', Point3(7, 0, 0))})
    render(view, updated, selected=3, incremental=True)
    assert len(view._actors) == 2
    assert 1 not in view._actors_by_entity
    assert len(view._position_batch_actors) == 1
    batch = view._position_batch_actors[0].GetMapper().GetInput()
    assert batch.GetNumberOfPoints() == 1
    assert batch.GetPoint(0) == (5, 0, 0)
    assert view._actors_by_entity[3][0].GetProperty().GetPointSize() == 10
    # Moving selection returns the previous selected position to the batch.
    render(view, replace(updated, render_updates={}), selected=2, incremental=True)
    assert view._position_batch_actors[0].GetMapper().GetInput().GetPoint(0) == (7, 0, 0)
    assert len(view._actors) == 2


def test_position_batches_keep_label_candidates(view):
    snapshot = GeometrySnapshot(points={1: Point3(0, 0, 0), 2: Point3(1, 0, 0)})
    view._place_labels = Mock()
    render(view, snapshot, labels=True)
    assert {spec.entity_id for spec in view._label_specs} == {1, 2}
    assert len(view._position_batch_actors) == 1
