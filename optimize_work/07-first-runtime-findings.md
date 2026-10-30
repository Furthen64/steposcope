# First AL39 runtime findings

Analyzed `runtimelog.txt` and all 28 records in `output/performance/20261030-192445-445492-14343/timings.jsonl`. The last recorded playback step is 15,927, so this run does not yet measure the originally reported slowdown around 22,000.

## Measured result

Across snapshots with playback step context, 43.90 seconds were attributed to instrumented work:

| Phase (exclusive time) | Seconds | Share |
| --- | ---: | ---: |
| Actor creation/update | 18.83 | 42.9% |
| Other scene work, including full-scene teardown | 8.88 | 20.2% |
| VTK rendering | 9.34 | 21.3% |
| Clipping updates | 3.33 | 7.6% |
| Geometry builder excluding timed edge/face children | 0.44 | 1.0% |

All viewport phases together account for 92.0%. These are measured-code percentages, excluding idle time, profiler output, and uninstrumented callbacks. Geometry's inclusive cumulative build time was 0.47 seconds.

At step 15,443 a whole Turbo callback took 2.96 seconds. Geometry work for that snapshot window totaled about 10 ms. At the last snapshot there were 4,879 Cartesian points, 30 vertices, 4,909 actors, and no built faces or polylines. Labels were disabled and Turbo enabled, with points still visible.

There were 63 playback callbacks, 63 scene presentations, but only 18 used incremental rendering: **45 of 63 rebuilt the full scene**. There were 128 render calls in the playback-context windows, approximately two per callback. This makes the scene size expensive both to recreate and to draw.

## Code explanations

### Control-net density entries can invalidate the entire scene

`MainWindow._update_surface_controls()` adds `surface_id: 100` to `control_net_densities` for each discovered surface status, including surfaces that are not ready to draw. `_render_style_signature()` includes every density dictionary entry. When new entries arrive, `_can_render_incrementally()` rejects the previous signature, causing complete scene reconstruction.

This is a concrete route to unnecessary rebuilds even when there is no drawable control net. It is consistent with the frequent full rebuilds in this run. The current log does not record the exact rejection reason, so it does not prove this is the cause of every fallback.

### Clearing an absent edge issue renders again

After presenting the snapshot, `MainWindow._show_snapshot()` calls `_show_edge_issue()`. With no edge issues, this calls `VtkView.set_issue_edge(None)`, which renders even when there is no previous issue highlight to clear. The scene presentation already rendered. This directly explains a redundant-render route consistent with the approximately two renders per callback observed.

## Revised recommendation

Try a small change first: **keep rendering incremental when new default density entries appear, and make clearing an already absent issue highlight a no-op**. Preserve a deliberate redraw when an existing highlight is actually removed. For real density edits, invalidate/update the affected control-net actors rather than globally ignoring density changes.

Add render-rejection reasons to the diagnostic context as part of validating that change. Compare at matched playback orders: full rebuild count, actor creation calls, renders per callback, and callback duration. Single stepping, hide/show, density edits, and issue-highlight removal must remain correct.

Then implement point batching if ordinary renders remain expensive. The evidence still supports that proposal, but preventing full rebuilds and duplicate renders is a smaller first experiment with a direct code explanation. Do not prioritize geometry algorithms based on this run.

Application code was not changed during this log analysis.
