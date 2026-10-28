"""Coarse progress reporting for long headless runs.

A STEP file can take minutes to read and tessellate, and a run that prints
nothing until it finishes is indistinguishable from a hung one. This writes to
stderr (stdout stays free for machine-readable output) in a form that reads well
both interactively and in a redirected log:

- on a TTY, a long per-item loop rewrites one line in place with ``\\r``;
- when redirected, it instead emits a line each time the percentage crosses a
  step, so a captured log gets a readable trail rather than thousands of
  overwritten frames.

Everything is opt-in. Callers that pass no reporter stay completely silent, so
the interactive UI and the test suite are unaffected.
"""

from __future__ import annotations

import sys
import time
from typing import TextIO


class Progress:
    """Stage banners plus a throttled per-item counter."""

    def __init__(
        self,
        enabled: bool = True,
        stream: TextIO | None = None,
        step_percent: int = 10,
        min_interval: float = 1.5,
    ) -> None:
        self.enabled = enabled
        self.stream = stream if stream is not None else sys.stderr
        self.step_percent = max(1, step_percent)
        self.min_interval = min_interval
        self._tty = False
        self._dirty = False
        self._last_step = -1
        self._last_emit = 0.0
        try:
            self._tty = bool(self.stream.isatty())
        except (AttributeError, ValueError):
            self._tty = False

    # ---- output primitives -------------------------------------------------

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except (ValueError, OSError):      # stream closed underneath us
            self.enabled = False

    def _clear_line(self) -> None:
        if self._tty and self._dirty:
            self._write("\r" + " " * 78 + "\r")
            self._dirty = False

    # ---- public API --------------------------------------------------------

    def stage(self, label: str) -> None:
        """Announce a stage starting."""
        if not self.enabled:
            return
        self._clear_line()
        self._write(f"  -> {label}\n")

    def done(self, label: str, seconds: float) -> None:
        """Close out the current stage with its elapsed time."""
        if not self.enabled:
            return
        self._clear_line()
        self._write(f"  ok {label} ({seconds:.2f}s)\n")

    def note(self, text: str) -> None:
        if self.enabled:
            self._clear_line()
            self._write(f"  {text}\n")

    def item(self, done: int, total: int, label: str = "") -> None:
        """Report position within a long loop.

        Throttled: on a TTY at most every ``min_interval`` seconds, otherwise
        only when the percentage crosses a ``step_percent`` boundary (and always
        on the first and last item, so the trail starts and ends cleanly).
        """
        if not self.enabled or total <= 0:
            return
        pct = int(done * 100 / total)
        finished = done >= total
        now = time.monotonic()
        if self._tty:
            if not finished and now - self._last_emit < self.min_interval:
                return
            self._write(f"\r     {label} {done}/{total} ({pct}%)   ")
            self._dirty = True
            self._last_emit = now
            return
        step = pct // self.step_percent
        if not finished and step == self._last_step and now - self._last_emit < self.min_interval:
            return
        self._last_step = step
        self._last_emit = now
        self._write(f"     {label} {done}/{total} ({pct}%)\n")

    def end_item(self) -> None:
        """Finish a rewritten in-place line so the next stage starts clean."""
        if self.enabled and self._tty and self._dirty:
            self._write("\n")
            self._dirty = False
