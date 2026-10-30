# Experiment 1: default density entries preserve incremental rendering

Changed only the control-net density component of the viewport style signature. An absent density already renders at `DEFAULT_CONTROL_NET_DENSITY` (100), so an explicit entry with that same value is now omitted from the signature. Discovering more surfaces with default settings no longer changes the style signature and forces all actors to be rebuilt.

Actual custom densities still change the signature, including restoring a custom value to the default. Other reasons for full scene rendering remain in place. No actor batching, timer changes, or edge-highlight render changes are included.

Regression checks cover newly added default and custom densities and returning to default. Run the same AL39 command with profiling enabled and compare against the first session at matched steps. Expect fewer full rebuilds and actor creations; render calls may still be approximately twice per callback because that is a separate issue.

```bash
STEPOSCOPE_PROFILE=1 ./launch.sh corpus/TOUGH_stresstests/AL39.STEP
```

Each run creates a new session directory, preserving the first log for comparison. The real-world speed improvement remains to be measured by this rerun.
