# Investigation evidence and experiment plan

## Local observations

File: `corpus/TOUGH_stresstests/AL39.STEP`, 23,486,968 bytes, 262,660 parsed entities. Tests used the existing `.venv/bin/python`, without changing dependencies.

| Observation | Result |
| --- | --- |
| Parse, two runs | 13.59 s and 14.19 s |
| Entity totals | 80,406 Cartesian points; 9,801 vertices; 30,944 oriented edges; 15,472 edge curves; 6,106 advanced faces |
| Fresh `GeometryBuilder.build(21989)` | 50 ms; 6,721 points, no built polylines or faces |
| Sequential `build(i, measure_times=True)`, orders 21,990–22,029 | Each printed as 0.0–0.7 ms; about 7 ms cumulative builder time under cProfile |
| Largest sampled builder cost | Dictionary copies, about 3 ms total |

Orders here are zero-based positions in `document.entities`. The UI's progress count is one greater. STEP entity IDs are separate and are not strictly ordered in this file.

This benchmark jump-built the prefix, then advanced 40 records. It did not replay all preceding records or measure Qt event handling, details/tree updates, warnings, VTK rendering, or the full application's memory history. It does not rule out isolated costly faces or slowdowns elsewhere.

Saved QSettings values read without modification: `show_entity_labels=false`, `normal_display=Hidden`, `fast_preview=true`, `autozoom=false`; label limit unset. An already-running app or another machine can have different effective settings.

## Isolated renderer experiment

Created a VTK renderer and an offscreen 900×650 render window. Points were distributed as `(i % 100, (i // 100) % 100, i % 7)` with point size 6. Each individual point used the same basic points/polydata/vertex-glyph/mapper/actor pipeline as `_add_point()`. A second process used one such pipeline holding all points. Camera was reset before three renders.

| Scene | Construction | First render | Subsequent renders |
| --- | --- | --- | --- |
| 1,000 individual actors | 277 ms | 651 ms | 52, 59 ms |
| 6,700 individual actors | 3,091 ms to add the next 5,700 actors | 991 ms | 211, 213 ms |
| 6,700 points in one actor | 4.4 ms for all points | 365 ms | 29, 23 ms |

VTK warned that it could not connect to `DISPLAY=:0`; it nevertheless completed the offscreen renders. These results establish a useful actor-count comparison in this environment, not the user's onscreen GPU performance. Geometry, overlays, colors, and selection were simplified. First-render times include initialization and should not be treated as steady-state frame cost. Trials were small, so ratios are indicative only.

## Baseline to capture before implementation

Use the same AL39 file, viewport size, saved settings, and machine. Start with uninterrupted playback from the beginning to beyond 22,000, then compare matched windows around 10,000, 20,000, 22,000 and later orders. Record both the slow entity's ID and its playback position. Run a small corpus model as a regression comparison.

Add lightweight aggregate timings around builder, warning/control updates, details/tree updates, actor maintenance, and `Render()`. Record incremental-render eligibility and its rejection reason, visible item/actor counts, actual batch size, total callback time, entities/second, median/p95 frame time, worst stalls, and process memory. Avoid synchronous per-entity disk logging inside timed paths.

The displayed per-step time is not an isolated geometry-handler measurement: `_playback_tick()` uses time since the previous tick finished, including timer/event-loop delay, and assigns it to the final entity of a Turbo batch. Use separate phase measurements to attribute cost.

## First experiment and decision rule

Implement only point/vertex batching first, then repeat matched runs. Retain it if rendering and playback throughput improve materially while selection, visibility, and geometry remain correct. If the five-second stalls persist, use phase timings to choose the next proposal instead of assuming all remaining time is rendering. Keep parse time, initial scene construction, steady playback, and final completeness/report generation separate.

No application tests were run for this documentation-only investigation. Implementation should use the existing relevant tests plus focused correctness checks and the AL39 performance comparison; timing thresholds should not become brittle CI assertions.
