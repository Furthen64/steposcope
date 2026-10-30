# Experiment 1 results: substantially less scene rebuilding

Compared the original session `20261030-192445-445492-14343` with rerun `20261030-193202-628586-14931`.

| Measurement | Original | Rerun |
| --- | ---: | ---: |
| Last snapshot below step 16,000 | 15,927 | 15,584 |
| Playback callbacks in that range | 63 | 62 |
| Full scene rebuilds in that range | 45 | 1 |
| Sum of playback callback durations | 43.22 s | 13.06 s |
| Worst playback callback in that range | 2,957 ms | 376 ms |
| Actor maintenance exclusive time in that range | 18.83 s | 1.09 s |

The comparison windows end at slightly different steps, but cover nearly the same number of callbacks. Recorded callback work fell by approximately 3.3×. This is not a controlled end-to-end wall-clock benchmark; timer waits, interaction, and profiler output are outside the callback totals.

The rerun continued to step 29,744, with 168 of 169 presentations using incremental rendering. Near step 22,078, the snapshot covered seven callbacks with a maximum of 323 ms, rather than a multi-second stall. The worst callback anywhere in the rerun was 425 ms.

Rendering is now the largest remaining measured cost: 23.45 s exclusive across the rerun, with 344 render calls for 169 playback callbacks. The next small experiment would be avoiding the redundant render when clearing an already absent edge highlight. No additional optimization was applied during this analysis.
