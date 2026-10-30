# 3. Preserve incremental geometry with labels and other display modes

## Evidence

`VtkView._can_render_incrementally()` requires labels to be disabled, normals to be `Hidden`, face analysis to be inactive, and a matching render style signature. When those conditions fail, `_render_snapshot()` removes and reconstructs the entire scene.

The label budget limits created labels, but does not prevent rebuilding all geometry or projecting all label candidates. `MainWindow._label_texts()` and `_label_orders()` also rebuild dictionaries over the entire document on every presentation when labels are enabled. AL39 has 262,660 entities.

The saved settings read during this investigation had labels disabled and normals `Hidden`. This proposal therefore addresses a conditional performance cliff, not the leading explanation of the slowdown with those settings.

## Proposed change

Separate geometry actors from label actors and normal/orientation overlays. Continue applying geometry deltas while refreshing only overlays affected by selection, new geometry, camera movement, or changed display settings.

Cache label text and order maps once per document. Keep the existing label limit and screen-space thinning. Rebuild placement on relevant changes, with a limited cadence during Turbo. A style change can legitimately cause one complete refresh; it should not force complete geometry reconstruction on every following step.

Body hiding currently disables incremental rendering in `MainWindow._show_snapshot()`. A later extension can filter incoming deltas through cached hidden IDs, with a full refresh on a visibility change. Treat this as separate from the initial labels change to keep validation manageable.

## Tradeoffs and validation

Labels depend on camera and selection even when geometry is unchanged. Test movement, zoom, selection, label age/fade, label budgets, normal modes, face analysis transitions, and hidden bodies. Verify geometry actor count stays stable across repeated presentations and that overlays update correctly. Existing label, normal, selection, and control-net tests are relevant.
