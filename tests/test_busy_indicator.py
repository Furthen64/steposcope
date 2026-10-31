from io import BytesIO
import json
from types import SimpleNamespace

from step_explorer.busy_indicator import ActivityState, BusyIndicator, media_directory


def test_short_work_never_shows_images_and_long_work_switches_to_result():
    now = [0.0]
    state = ActivityState(clock=lambda: now[0])
    state.receive(dict(event='begin', key='load', label='Loading', time=0))
    now[0] = 4.9
    assert state.display() is None
    now[0] = 5.1
    assert state.display() == ('waiting', 'Loading')
    state.receive(dict(event='end', key='load', success=True))
    assert state.display() == ('result', 'Ready')
    state.receive(dict(event='heartbeat', time=now[0]))
    now[0] += 4.1
    assert state.display() is None


def test_heartbeat_detects_blocked_ui_without_a_named_operation():
    now = [0.0]
    state = ActivityState(clock=lambda: now[0])
    now[0] = 6
    assert state.display()[0] == 'waiting'
    state.receive(dict(event='heartbeat', time=6))
    assert state.display()[0] == 'result'


def test_nested_operations_remain_waiting_until_all_finish():
    now = [0.0]
    state = ActivityState(clock=lambda: now[0])
    state.receive(dict(event='begin', key='play', label='Playing', time=0))
    now[0] = 7
    state.receive(dict(event='begin', key='other', label='Other', time=7))
    assert state.display() == ('waiting', 'Playing')
    state.receive(dict(event='end', key='other', success=True))
    assert state.display()[0] == 'waiting'
    state.receive(dict(event='end', key='play', success=True))
    assert state.display()[0] == 'result'


def test_failed_operation_does_not_show_success_image():
    now = [0.0]
    state = ActivityState(clock=lambda: now[0])
    state.receive(dict(event='begin', key='load', label='Loading', time=0))
    now[0] = 6
    state.receive(dict(event='end', key='load', success=False))
    assert state.display()[0] == 'error'


def test_controller_sends_balanced_messages_and_closes_pipe(monkeypatch):
    pipe = BytesIO()
    monkeypatch.setattr('step_explorer.busy_indicator.subprocess.Popen', lambda *a, **k: SimpleNamespace(stdin=pipe))
    indicator = BusyIndicator()
    with indicator.operation('Loading'):
        indicator.heartbeat()
    messages = [json.loads(line) for line in pipe.getvalue().splitlines()]
    assert [m['event'] for m in messages] == ['begin', 'heartbeat', 'end']
    assert messages[0]['key'] == messages[-1]['key']
    indicator.close()
    assert pipe.closed


def test_media_files_are_available():
    assert (media_directory() / 'waiting.png').is_file()
    assert (media_directory() / 'result.png').is_file()


def test_helper_process_starts_and_exits_when_parent_pipe_closes():
    import os
    import subprocess
    import sys

    process = subprocess.Popen(
        [sys.executable, '-m', 'step_explorer.busy_indicator', str(media_directory())],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
    )
    try:
        _, errors = process.communicate(b'', timeout=10)
        assert process.returncode == 0, errors.decode()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
