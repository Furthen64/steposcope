# AL39 optimization work: current status

Updated after the floating paused-preview feature. The numbered files are a chronological record of proposals, measurements, experiments, and usability fixes; they are not seventeen completed performance optimizations. Later findings supersede the original priorities.

## Original proposals

| Proposal | Investigation | Implementation |
| --- | --- | --- |
| [01: Batch point rendering](01-batch-point-rendering.md) | Synthetic VTK comparison and real pause stalls confirmed excessive actor cost | Core batching now implemented: two ordinary marker actors plus selection. Batch arrays rebuild on position changes; append-only buffer updates remain open. |
| [02: Reduce presentation work](02-reduce-presentation-work.md) | Actual GUI logs identify repeated scene work as a major cost | Partially implemented: default-density invalidation and duplicate rendering fixed; Turbo presentation now coalesces changes across work slices. Conditional clipping and changed-only panel updates remain open. |
| [03: Incremental labels/display modes](03-incremental-display-modes.md) | Code inspected; actual slow-run settings had labels off | Not implemented. Label/normal overlays, label-map caching, and body-visibility deltas remain open. The density fix addresses another style invalidation issue, not this complete proposal. |
| [04: Geometry bookkeeping](04-geometry-bookkeeping.md) | Early builder profiled; long-run logs show increasing cost | Not implemented. Snapshot copying, indexing/sorting, dependency traversal, and failed-mesh caching remain unchanged. |

None of the four broad proposals is fully complete. Two small experiments have measured performance wins, alongside working diagnostics and several completed inspection/usability fixes.

## Measured wins

- [Default-density experiment and results](09-experiment-results.md): full rebuilds fell from 45 to 1 over similar first-16,000-step ranges; recorded callback time fell from 43.22 s to 13.06 s (about 3.3× less work).
- [Skip empty highlight clearing](10-experiment-skip-empty-highlight-render.md): render calls fell from 344 to 166 over similar first-30,000-step ranges; callback time fell from 45.71 s to 32.83 s (about 28% less).

These are matched-range processing comparisons, not controlled whole-file benchmarks. We have not established end-to-end completion speed or final model correctness for AL39.

## What the long run tells us

[The 77-minute session](14-long-playback-findings.md) reached step 187,290 (71.3%). In its late range, measured work was approximately 50% rendering, 17% surface controls, and 16% clipping. Builder slices had shrunk to roughly 4–5 source records before each expensive presentation. Surface-control rebuilding alone consumed about 15 minutes across the run.

**Current experiment: [ten-second heavy-file GUI updates and batched positions](20-ten-second-refresh-and-pause-stalls.md).** Turbo accumulates progress across short builder slices, waits ten seconds after each presentation for files with 50,000+ records (250 ms for smaller files), and flushes on pause/completion. The line list updates at the same cadence. The latest log exposed a 172-second paused redraw from individual point actors, now replaced by batches. End-to-end throughput and onscreen pause improvements still need a rerun.

Then consider updating only changed surface-control channels, followed by batching edge actors. Point batching remains useful for paused inspection, but playback now normally hides position markers for long files. Label/display work is lower priority for the measured settings.

## Supporting work completed

- [05: Baseline measurements](05-measurements-and-validation.md), [06: live profiling](06-live-performance-logging.md), [07: initial runtime diagnosis](07-first-runtime-findings.md), [08: first experiment](08-experiment-default-density.md).
- [11: clipping guard](11-distant-point-clipping.md), [12: robust autozoom](12-robust-preview-framing.md), [13: independent position visibility](13-position-visibility.md).
- [15: viewport input lock](15-playback-input-lock.md), superseded where applicable by [16: Ctrl+P, remembered confirmation, paused markers](16-playback-shortcut-and-paused-preview.md).
- [17: static last-paused preview](17-last-paused-preview.md), verified working by the user.

Grid/axis sizing still uses global bounds and can be distorted by distant construction points. Robust autozoom fixed camera fitting, not those helper sizes.

[18: Multicore processing](18-multicore-processing.md) evaluates worker processes, dependency-safe geometry batches, a later face-tessellation pool, and why presentation coalescing is still necessary. This is a design proposal, not implemented work.

Run `./rundebug.sh` to profile AL39. It uses the normal `launch.sh` path with `STEPOSCOPE_PROFILE=1`; snapshots go to a separate session under `output/performance/`.
