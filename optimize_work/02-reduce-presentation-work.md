# 2. Reduce repeated presentation work

## Existing behavior

`MainWindow._playback_tick()` already batches up to 256 entities or approximately 20 ms of builder work in Turbo. It merges `render_updates` before presenting the batch. This is useful and should be retained.

However, the budget excludes presentation time. `VtkView._render_snapshot_incremental()` calls `Render()` even when updates are empty. It also processes the previous and current selected entities, resets clipping, and may recreate actors just to change selection. Thus cheap metadata records can still trigger costly viewport work. A single expensive build or render can exceed the 20 ms budget; the budget cannot interrupt it.

## Proposed change

Track whether geometry, selection appearance, labels, camera, overlays, or display style actually changed. Skip a scene render when all are unchanged. Continue updating the textual current entity and playback progress so single stepping remains understandable.

For Turbo, accumulate changes and present at a controlled cadence, initially targeting 20–30 frames per second when processing is fast enough. Respect an elapsed-work budget so input events get serviced. Present the final state immediately on pause, end of playback, or explicit single stepping. Do not skip source entities or timing/report bookkeeping.

Update a selection overlay or actor properties rather than remove/recreate unchanged point or face geometry. Avoid recomputing clipping bounds unless geometry/camera state requires it. Cache warning-note summaries by changed face IDs rather than scan all existing faces in `_update_preview_warnings()` every presentation. Coalesce repeated text/layout updates when their content is unchanged.

## Expected benefit and limits

This should improve metadata-heavy playback and reduce unnecessary expensive scene renders. It will not make an unavoidable slow render fast; pair it with point batching. Turbo and autozoom were already on and off respectively in the settings read during investigation, so simply enabling Turbo or disabling autozoom is not a sufficient recommendation for this checkout.

## Validation

Measure builder and presentation time separately, renders per second, entities per second, and input responsiveness. Exercise long metadata stretches, selection changes with empty geometry deltas, end-of-file flushing, pause/resume, backward stepping, and body visibility. Preserve all merged updates and final geometry; a high step count with a stale viewport is not success.
