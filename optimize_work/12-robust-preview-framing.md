# Autozoom using the main cluster of preview positions

AL39's distant point #8792 is referenced as the origin of LINE #50030. It is not a spline control point. A line's parameter origin need not lie near the bounded edge that uses it, so raw Cartesian positions are not necessarily a good model envelope.

Before visible faces exist, automatic framing and the Fit button now use the main cluster of displayed preview positions rather than VTK's complete scene bounds. Once visible faces exist, the existing triangle-based fitting remains unchanged.

Algorithm:

1. Gather preview positions, respecting whether standalone point markers are currently shown. Discard non-finite coordinates from framing calculations and deduplicate positions so repeated references cannot bias the estimate.
2. For fewer than 20 unique positions, retain the complete envelope.
3. Otherwise find the coordinate-wise median, measure distances to it, and find the radius containing 90% of positions.
4. Fit the full envelope of positions within ten times that radius. This extra margin retains ordinary extremities rather than cropping exactly to a percentile.

The calculation is independent of model units. It changes only camera bounds: entities, coordinates, rendering, exports, and reports are preserved. Empty previews retain VTK's previous fallback behavior. The camera direction is preserved by ResetCamera(bounds).

This is a heuristic, not a geometric validity classification. A tiny remote component constituting less than 10% of unique positions can be excluded if sufficiently far away; larger separated groups are represented by the 90% radius and stay in frame. The user can still navigate to distant geometry. Grid and axis sizing continue to use their existing global-envelope rules; this change specifically addresses camera framing.

Tests cover a remote origin, small scenes, substantial separated components, unit scaling, repeated references, and existing face-based camera behavior. Run `./rundebug.sh` with autozoom enabled and advance beyond step 8,793 to check the user-visible result.
