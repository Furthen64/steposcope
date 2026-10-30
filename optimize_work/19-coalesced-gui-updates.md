# Experiment: coalesce Turbo GUI updates across processing slices

Turbo retains its existing maximum of 256 source records or approximately 20 ms of geometry work per timer callback. It now accumulates changes across callbacks and presents after at least 250 ms since the previous presentation finished, instead of presenting after every callback. This yields at most roughly four normal presentations per second when rendering is cheap, and fewer when it is expensive.

The interval starts after rendering completes. A slow render cannot consume the entire next work interval and force another render after only a few source records. Qt still receives events between the short processing callbacks, so Ctrl+P can pause. An individual slow geometry operation or render still cannot be interrupted in mid-call.

Current-line selection/scrolling, details, progress slider, status, surface controls, and viewport presentation are updated together. Per-source geometry/report measurements still run for every source record. The log's `step` can therefore advance beyond `presented_step`, which identifies the last coalesced presentation.

All geometry deltas between presentations are merged, including deletions and replacements. If any snapshot requires a full rebuild, that requirement remains until the next presentation. Only the latest complete snapshot and a merged change map are retained, not a queue of every intermediate snapshot.

Pause and completion flush immediately. Pausing shows the latest processed state with positions restored. Explicit seeks discard pending deltas and use a complete redraw when necessary. Opening a file clears pending work. An external style/visibility redraw using the old displayed snapshot is promoted to the latest pending snapshot, avoiding stale geometry.

Normal playback without Turbo and explicit single stepping continue presenting every requested step. The static last-paused preview is unaffected.

Validation: 34 focused tests passed across coalescing, playback listing, input locking, paused previews, and duration/report behavior. New deterministic tests cover deferred line/progress updates, merging changes across slices, pause flush, forward/backward seek, end-of-file flush, style/file changes, preserving full-redraw requirements, and ensuring render duration does not consume the next progress interval.

Rerun `./rundebug.sh` with Turbo enabled. Compare `geometry.build` calls per `ui.present`/`viewport.render` call, time spent in surface controls and clipping, and source progress per elapsed second at similar late-file orders. Speedup on AL39 remains to be measured; the 250 ms interval is an initial setting, not a proven optimum.
