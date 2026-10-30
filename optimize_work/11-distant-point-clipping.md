# Disappearing markers with Turbo off

The screenshots show a separate problem from Turbo visibility: the grid and markers disappear around playback step 10,017, with Turbo unchecked.

AL39 point #8792 occurs at zero-based entity order 8792 (playback step 8793). Its Y coordinate is 498996.51863839995 and Z is 498981.80001264805. At order 7925, point coordinates span roughly X -418..61, Y -99..106, Z -80..219. This new point expands the scene by three orders of magnitude.

A regression scene using the distant coordinates reproduces VTK selecting a near clipping plane beyond the camera's focal distance for a camera looking toward the outlier. That excludes the nearby geometry even though the camera has not moved. The same global bounds also expand the coordinate axes and grid.

Changed `_reset_clipping_range()` to detect when VTK's near plane reaches/passes the focal distance and move it to 0.001 times that distance. Preserve VTK's far plane and camera framing, and keep the original STEP point. Normal clipping calculations remain unchanged when the near plane already lies before the focal point. The diagnostic context now includes clipping range and camera distance.

Regression tests cover perspective and parallel projection, preserving position, focal point, and far range. This is a targeted disappearance fix; grid spacing and axis sizing still use global bounds and can remain oversized around distant construction points. The exact screenshot camera pose was not captured, so confirm the user-visible result with a rerun.

Run `./rundebug.sh`, disable Turbo to match the screenshots, and advance beyond step 8,793. Check that the local points remain visible through step 10,017. No source entities are removed or coordinates changed.
