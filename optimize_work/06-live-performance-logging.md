# Live timing snapshots and blame chart

Implemented as an opt-in diagnostic. Start the application normally, with one environment variable:

```bash
STEPOSCOPE_PROFILE=1 ./launch.sh corpus/TOUGH_stresstests/AL39.STEP
```

The terminal prints the output directory, then a ranked timing summary about every two seconds while work completes. Each run creates its own timestamped directory under `output/performance/`, which is already ignored by Git. Set `STEPOSCOPE_PROFILE_DIR=/some/path` to choose another root.

Two files are produced:

- `latest.md`: a periodically replaced blame chart for the latest measurement window, with percentage bars, self milliseconds, inclusive milliseconds, calls, and maximum call duration.
- `timings.jsonl`: an append-only history, one JSON object per window. Includes window and cumulative phase timings, playback/entity context, and the ten slowest completed instrumented calls across the run.

For a continuously refreshed text view, use the printed directory:

```bash
watch -n 2 cat output/performance/<session>/latest.md
```

The terminal itself also emits the top five phases, without needing a separate viewer. These are timing/state snapshots, not viewport screenshots.

## What is attributed

Parsing; file opening; playback callbacks and builder advances; edge sampling; face lookup and tessellation; warning/completeness/control-panel updates; details and tree focus; label maps and placement; actor maintenance; full/incremental scene updates; clipping; and actual `Render()` calls, including camera/selection renders.

Context includes the source path, last playback step (one-based), entity order (zero-based), STEP ID/type, point/vertex/face/polyline counts, actor count, labels/Turbo state, and full versus incremental rendering. Context describes the latest observed state; a Turbo callback can cover many entities, so its slow event is associated with its final entity rather than blaming that entity for the whole batch. Scene counts update at presentation time, not every builder advance.

**Blame is ranked by exclusive/self wall time.** For example, if a playback callback takes 5 seconds and spends 4 seconds in rendering, rendering gets those 4 seconds and the callback gets its remaining uninstrumented work. Inclusive durations and maxima remain available for understanding whole callbacks. Do not sum inclusive times; parent and child overlap. Slowest-call entries likewise include overlapping parent/child calls.

Percentages cover measured code, not all elapsed time: timer delays, idle time, and uninstrumented callbacks are excluded. A window may span a long individual call. Nested calls are accumulated in the window where their enclosing outer call finishes, rather than split across time boundaries. Parse/open time appears separately and in cumulative totals; use the latest playback window when investigating playback.

## Behavior and limits

Disabled unless explicitly enabled at process start. Memory use is bounded by the fixed phase set and ten retained slow calls. No synchronous disk writes occur per entity or actor; output is written at window boundaries or normal shutdown. An enabled-only Qt timer flushes partial windows when playback pauses.

The logger runs on the UI thread. If a VTK render blocks for five seconds, the next snapshot arrives after it returns and records that duration. It cannot show the Python/native stack of an operation while it is hung, and a hard kill can lose the unflushed window. A separate sampling profiler would be the next tool if we need to inspect an operation that never returns.

Logging adds timing and aggregation overhead, especially with many actor calls. File writes and summary formatting are not included in phase totals. Compare profiling on/off before treating small timing differences as optimization gains. Output errors disable logging without aborting application work.

The first AL39 run should establish whether `viewport.render`, `viewport.actors`, geometry, or UI work dominates at the slowdown. That evidence should select the first optimization proposal.
