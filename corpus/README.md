# STEP example corpus

Files are grouped by the geometry used by their faces, from planar surfaces to
B-spline surfaces. The folders describe geometry complexity, not whether every
face renders successfully in the viewport.

| Folder | Face geometry |
| --- | --- |
| `planar_only/` | Planes only, including open and nonmanifold models |
| `analytic_curved/` | Planes and/or cylinders, cones, spheres, or tori |
| `small_bspline_lofts/` | Degree 3 B-spline loft faces with 4×4 control grids |
| `complex_bspline/` | Larger or higher degree nonrational B-spline faces |
| `rational_bspline/` | Rational B-spline faces |

Run `./headless/manual_view_render_step.sh corpus/` from the repository root to render
every example, including files with an uppercase `.STEP` extension.
