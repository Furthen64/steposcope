"""Opt-in, UI-thread wall-time attribution (STEPOSCOPE_PROFILE=1)."""

from __future__ import annotations

import atexit
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import json
import os
from pathlib import Path
import sys
from time import perf_counter


class PerformanceLog:
    def __init__(self, directory, *, interval=2.0, clock=perf_counter, stream=None):
        self.directory = Path(directory)
        self.interval = interval
        self.clock = clock
        self.stream = stream if stream is not None else sys.stderr
        self.started = self.last_emit = clock()
        self.stack = []
        self.window = {}
        self.total = {}
        self.context = {}
        self.slowest = []
        self.enabled = True

    @contextmanager
    def span(self, name):
        if not self.enabled:
            yield
            return
        frame = [self.clock(), 0.0]
        self.stack.append(frame)
        try:
            yield
        finally:
            elapsed = self.clock() - frame[0]
            self.stack.pop()
            own = max(0.0, elapsed - frame[1])
            if self.stack:
                self.stack[-1][1] += elapsed
            for table in (self.window, self.total):
                row = table.setdefault(name, dict(calls=0, inclusive_ms=0.0, self_ms=0.0, max_ms=0.0))
                row['calls'] += 1
                row['inclusive_ms'] += elapsed * 1000
                row['self_ms'] += own * 1000
                row['max_ms'] = max(row['max_ms'], elapsed * 1000)
            self.slowest.append(dict(phase=name, elapsed_ms=elapsed * 1000, **self.context))
            self.slowest.sort(key=lambda row: row['elapsed_ms'], reverse=True)
            del self.slowest[10:]
            if not self.stack and self.clock() - self.last_emit >= self.interval:
                self.flush()

    def flush(self):
        if not self.enabled or not self.window or self.stack:
            return
        now = self.clock()
        measured = sum(row['self_ms'] for row in self.window.values())
        ranked = [dict(phase=name, **row,
                       blame_percent=100 * row['self_ms'] / measured if measured else 0)
                  for name, row in sorted(self.window.items(), key=lambda item: -item[1]['self_ms'])]
        snapshot = dict(timestamp=datetime.now(timezone.utc).isoformat(),
                        elapsed_seconds=now - self.started, window_seconds=now - self.last_emit,
                        context=dict(self.context), phases=ranked, cumulative=self.total,
                        slowest=self.slowest)
        lines = ['# Live performance blame', '',
                 'Wall time in instrumented code; self time excludes timed children.',
                 'Percentages exclude idle/timer waits and uninstrumented callbacks.', '',
                 f"Context: `{json.dumps(self.context, sort_keys=True)}`", '',
                 '| Phase | Blame | Self ms | Inclusive ms | Calls | Max ms |',
                 '| --- | --- | ---: | ---: | ---: | ---: |']
        for row in ranked:
            bar = '█' * round(row['blame_percent'] / 5)
            lines.append(f"| {row['phase']} | {bar} {row['blame_percent']:.1f}% | "
                         f"{row['self_ms']:.2f} | {row['inclusive_ms']:.2f} | "
                         f"{row['calls']} | {row['max_ms']:.2f} |")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / 'timings.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(snapshot) + '\n')
            temporary = self.directory / 'latest.tmp'
            temporary.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            temporary.replace(self.directory / 'latest.md')
            top = ', '.join(f"{row['phase']} {row['blame_percent']:.0f}% "
                            f"({row['self_ms']:.0f}ms)" for row in ranked[:5])
            print(f"[performance] {self.context.get('step', '?')}: {top}", file=self.stream)
        except (OSError, ValueError) as exc:
            self.enabled = False
            try:
                print(f'[performance] logging disabled: {exc}', file=self.stream)
            except (OSError, ValueError):
                pass
        self.window = {}
        self.last_emit = self.clock()


_log = None
_configured = False


def get_log():
    global _log, _configured
    if not _configured:
        _configured = True
        if os.environ.get('STEPOSCOPE_PROFILE', '').lower() in {'1', 'true', 'yes'}:
            root = Path(os.environ.get('STEPOSCOPE_PROFILE_DIR', 'output/performance'))
            session = datetime.now().strftime('%Y%m%d-%H%M%S-%f') + f'-{os.getpid()}'
            _log = PerformanceLog(root / session)
            atexit.register(_log.flush)
            print(f'[performance] snapshots: {_log.directory}', file=sys.stderr)
    return _log


def context(**values):
    log = get_log()
    if log is not None:
        log.context.update(values)


def timed(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            log = get_log()
            if log is None:
                return function(*args, **kwargs)
            with log.span(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


@contextmanager
def span(name):
    log = get_log()
    if log is None:
        yield
    else:
        with log.span(name):
            yield
