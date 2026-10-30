# 1. Batch point rendering — recommended first experiment

## Evidence

`VtkView._add_point()` in `step_explorer/visualization/vtk_view.py` creates a `vtkPoints`, `vtkPolyData`, `vtkVertexGlyphFilter`, mapper, and actor for each point/vertex. Incremental playback avoids rebuilding the entire scene under the default display modes, but every `Render()` still processes the accumulated scene.

At order 21,989, AL39 has 6,721 visible Cartesian points and no built polylines or faces in the sampled prefix. Turbo's point hiding only applies after drawable faces, polylines, or control nets exist (`MainWindow._show_snapshot()`), so a point-heavy prefix can remain expensive even with Turbo enabled.

An isolated offscreen VTK test with 6,700 points took 211–213 ms per warm render using individual actors, versus 23–29 ms using one actor. Construction took about 3.09 s to add 5,700 actors to an existing 1,000-actor scene, versus 4.4 ms to construct all 6,700 points as a batch. These are synthetic measurements, not predicted GUI frame times; see [measurement details](05-measurements-and-validation.md).

## Proposed change

Store ordinary Cartesian points in one polydata/actor and topology vertices in another. Append or update their point coordinates and color arrays from `snapshot.render_updates`. Maintain mappings between STEP entity IDs and VTK point/cell indices.

Use a small separate actor for the current selection, preserving its size, color, and flash without rebuilding the point batch. Track selected entities that are not themselves point records. Ensure any picking path involving batched geometry can recover the original entity ID. Preserve the existing face-picking behavior and point visibility rules.

Keep faces and polylines on their current rendering paths for the first experiment. Batching those later would require additional decisions about per-face materials, mesh edges, selection, and picking.

## Tradeoffs and validation

Appending must mark the relevant VTK arrays/data as modified. Removal, backward navigation, file changes, and visibility filters need either index management or a deliberate batch rebuild. Do not mutate snapshots that other UI/reporting code retains.

Compare playback at the same orders before/after, with Turbo on and off. Verify selected point size/color, point/vertex distinction, labels, hide/show bodies, backward stepping, and reopening a file. Record actor count, render duration, throughput, and memory. The acceptance criterion is a much smaller actor count and lower measured frame cost with matching visible geometry; no fixed speedup is promised.
