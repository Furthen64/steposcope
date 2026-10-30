# Last-run analysis and fixes for pause stalls

Analyzed session `20261030-231637-632531-68151`: 360 completed timing snapshots, 902.67 seconds elapsed, last processed step 156,531 (59.6% of AL39). The last presented step was 156,526. `runtimelog.txt` still contains an older run, so this analysis uses the newest JSONL session.

## Captured stalls

At step 132,017, a paused presentation took **171.80 seconds**. It showed 40,465 Cartesian points, 2,456 vertices, and 1,080 polylines using 44,001 actors. The next recorded full playback presentation at step 132,126 took **103.15 seconds** while hiding markers again and reducing the scene to 1,090 actors. Parent phase timings overlap; do not add their durations as separate work.

The expensive pause path was introduced by restoring all positions for inspection. Creating a separate actor per point made pause expensive; removing those actors on resume was also expensive. This was not an expensive face tessellation: no faces had been built in the last recorded state.

Across the complete recorded run, exclusive time included surface controls 171.49 s, actor work 165.97 s, scene maintenance 107.02 s, and geometry bookkeeping 132.46 s. These cumulative numbers include paused interaction, not just playback.

The final completed windows show ordinary incremental playback, with renders around 80 ms and surface-control rebuilds around 200 ms. A hard kill loses the active/unflushed work. The log therefore establishes the earlier pause/resume stalls, but cannot identify the final unfinished operation at the exact moment of termination.

## Changes

- **Heavy Turbo presentation interval is now 10 seconds** for documents with at least 50,000 records. Smaller documents retain 250 ms. Short processing slices and immediate pause/completion flushing remain.
- **Point/vertex markers are batched into at most two actors**, with a separate selected marker preserving its color and larger size. No points are sampled out or removed from snapshots. Incremental point edits, removals, and selection changes rebuild the affected batch data rather than create thousands of actors. Label candidates are retained; face-analysis rendering keeps its special per-entity boundary/context behavior.
- **Closing stops playback and discards pending presentation work** without invoking the paused inspection redraw.

The batch arrays are rebuilt when their positions/selection change; appending to persistent GPU buffers is not implemented. Edge actors and surface-control rebuilding are still future optimization targets. A currently executing render or geometry operation remains synchronous and cannot be interrupted by the close handler.

## Validation

57 focused tests passed across marker batches, presentation coalescing, viewport locking, camera behavior, labels, face analysis, selection, and playback listing. New tests verify the ten-second deadline and closing without a pause redraw. A synthetic scene containing 40,000 points and 3,000 vertices used only three actors including selection; CPU scene preparation took about **0.31 s**, with native rendering mocked. This is not a measured onscreen AL39 pause time or a direct speedup ratio.

Rerun `./rundebug.sh`. In Turbo, the current-line list and viewport should update about every ten seconds on AL39. Pause should flush current progress and restore positions without a separate actor per point. Compare pause/resume phase times, actor counts, and source progress between presentations. Ctrl+P responsiveness still depends on Qt receiving control after the current synchronous operation.
