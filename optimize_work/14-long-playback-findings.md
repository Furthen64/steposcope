# Long AL39 run: later playback bottlenecks

Session `20261030-205207-499413-21757` contains 2,397 timing snapshots and records 4,624.6 seconds elapsed (77.1 minutes). The final step is 187,290 of 262,660: 71.3%, with 75,370 records remaining. Elapsed time includes opening, idle time and interaction, so it is not all processing time.

Final scene: 57,367 Cartesian points, 4,944 vertices, 7,095 polylines, zero built faces, and 7,096 actors. Position markers are hidden, labels disabled, Turbo active. Incremental rendering is still working. The wireframe is consistent with the absence of built faces in the recorded state; face work may still arrive later because STEP references can point forward.

| Playback range | Callbacks | Measured exclusive work | Leading costs |
| --- | ---: | ---: | --- |
| Up to 49,888 | 616 | 40 s | Surface controls 39%, tick overhead 26%, builder 17% |
| 50,000–99,883 | 2,684 | 234 s | Surface controls 51%, tick overhead 21%, builder 15% |
| 100,000–149,998 | 5,584 | 842 s | Surface controls 40%, render 25%, clipping 8% |
| 150,000–187,290 | 8,280 | 2,713 s | Render 50%, surface controls 17%, clipping 16% |

Percentages are exclusive wall time in instrumented code, excluding waits and profiler output. The last range includes some non-playback UI activity; recorded playback callbacks themselves sum to 2,698 seconds there.

## What changed as the scene grew

Turbo processes at most 256 source records, but stops its batch when builder work reaches 20 ms. Increasing snapshot-copying and geometry costs mean the final range processes roughly 4.5 records per callback. Each callback then presents the whole accumulated scene. The last snapshot has five callbacks for eleven builder calls, five renders totaling 1.44 seconds, and clipping totaling 0.45 seconds. This is repeated viewport work for very little source progress.

The earlier duplicate-render fix remains effective: one render per callback in the first three ranges. There are only two nonincremental presentations in the last range, including interaction outside normal Turbo playback. This is a different bottleneck from the original repeated full-scene rebuilds.

Surface controls consumed approximately 923 seconds (15.4 minutes) across the playback-context windows. `_update_surface_controls()` destroys and recreates all surface channels whenever statuses change, even when only one surface has changed. This is a substantial independent cost, not tessellation.

## Recommended next experiment

Decouple the builder's 20 ms input-service budget from presentation. Retain short work slices and yield to Qt, but merge deltas over multiple slices before paying for a scene render and related panels. Use accumulated work/progress to trigger presentation, with explicit flushing on pause, seek, mode changes, and playback completion. Merely adding a 30 FPS target would not help a render that already takes hundreds of milliseconds: every slice would already be late for that deadline.

Validate all intermediate changes are merged, backward jumps cannot reuse stale deltas, and stopping immediately presents the final processed state. Compare source records per render and throughput at similar late-file orders, not only frame rate.

Two subsequent experiments remain justified: update only changed surface-control channels, then batch edge polydata to reduce thousands of actors and clipping traversal cost. Builder snapshot-copy/sort costs also merit later profiling, but geometry algorithms are not the dominant measured late-file cost.

This analysis makes no further application changes. The run did not reach face tessellation at completion, so it cannot establish final processing time or final model correctness.
