# Experiment 2: skip rendering when no issue highlight needs clearing

`VtkView.set_issue_edge(None)` now stops pending issue blinking and returns without rendering if there are no issue-edge actors, saved face properties, overview-edge actors, or saved overview properties to clear. It still clears the logical issue ID.

Removing an existing highlight follows the original cleanup and render path. Regression checks cover individual edge and face highlights and overview edge and face highlights, including restoration of face and backface properties. Repeated clearing after cleanup produces no further render.

No point batching, clipping changes, or scheduling changes are included. The default-density optimization from experiment 1 remains active.

Rerun `./rundebug.sh`. Compare `viewport.render` calls per `playback.tick` against session `20261030-193202-628586-14931` (344 renders for 169 callbacks). With no issue highlights, expect roughly one render per callback instead of two. Other events such as camera interaction can still add render calls. Compare callback duration at similar playback positions to judge the actual benefit.

## Rerun result

Session `20261030-193613-375847-15392` confirms the expected reduction. Comparing snapshots ending near step 30,000:

| Measurement | Experiment 1 | Experiment 2 |
| --- | ---: | ---: |
| Last included step | 29,744 | 29,681 |
| Playback callbacks | 169 | 165 |
| Render calls | 344 | 166 |
| Sum of callback durations | 45.71 s | 32.83 s |
| Render time | 23.45 s | 10.91 s |
| Worst callback | 425 ms | 357 ms |

Approximately 28% less callback time over similar playback ranges, with roughly one render per callback. The first 16,000-step windows also improved, from 13.06 s to 9.51 s across 62 callbacks in each run. These are recorded processing durations, not a controlled end-to-end elapsed-time benchmark. The latest session continued to step 39,251. No additional code changes were made during this comparison.
