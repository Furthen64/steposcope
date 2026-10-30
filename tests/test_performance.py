import io
import json

import pytest

from step_explorer.performance import PerformanceLog


def test_nested_time_is_not_double_counted_and_windows_reset(tmp_path):
    now = [0.0]
    log = PerformanceLog(tmp_path, clock=lambda: now[0], stream=io.StringIO())
    log.context.update(step=22000, entity_id=22001)
    with log.span('tick'):
        now[0] = 1.0
        with log.span('render'):
            now[0] = 4.0
        now[0] = 5.0
    snapshot = json.loads((tmp_path / 'timings.jsonl').read_text())
    rows = {row['phase']: row for row in snapshot['phases']}
    assert rows['tick']['inclusive_ms'] == 5000
    assert rows['tick']['self_ms'] == 2000
    assert rows['render']['self_ms'] == 3000
    assert rows['render']['blame_percent'] == 60
    assert snapshot['context']['step'] == 22000
    assert 'render' in (tmp_path / 'latest.md').read_text()
    with log.span('render'):
        now[0] = 8.0
    snapshots = [json.loads(line) for line in (tmp_path / 'timings.jsonl').read_text().splitlines()]
    assert len(snapshots) == 2
    assert snapshots[-1]['phases'][0]['calls'] == 1
    assert snapshots[-1]['cumulative']['render']['calls'] == 2


def test_exception_unwinds_and_partial_window_can_flush(tmp_path):
    now = [0.0]
    log = PerformanceLog(tmp_path, clock=lambda: now[0], stream=io.StringIO())
    with pytest.raises(RuntimeError), log.span('failed'):
        now[0] = 0.25
        raise RuntimeError('test')
    assert log.stack == []
    log.flush()
    assert json.loads((tmp_path / 'timings.jsonl').read_text())['phases'][0]['max_ms'] == 250


def test_unwritable_output_disables_logging_without_failing_work(tmp_path):
    blocked = tmp_path / 'file'
    blocked.write_text('not a directory')
    log = PerformanceLog(blocked, interval=0, stream=io.StringIO())
    with log.span('work'):
        pass
    assert not log.enabled


def test_slow_event_retention_is_bounded(tmp_path):
    now = [0.0]
    log = PerformanceLog(tmp_path, interval=1000, clock=lambda: now[0])
    for index in range(30):
        log.context['entity_id'] = index
        with log.span('work'):
            now[0] += index
    assert len(log.slowest) == 10
    assert log.slowest[0]['entity_id'] == 29
