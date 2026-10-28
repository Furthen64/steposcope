# steposcope

A STEP file microscope. Explore, visualize, and debug STEP geometry through step-by-step rendering.

## Quick Start

```bash
./1.check_requirements.sh      # Verify your system has what's needed
uv sync             # Install all dependencies automatically
./launch.sh          # Launch the application
```

To open a specific STEP file:

```bash
./launch.sh path/to/model.step
```

On Windows, use PowerShell from the repository directory:

```powershell
uv sync
.\winlaunch.ps1
.\winlaunch.ps1 .\corpus\planar_only\box125.step
```

## What You'll See

steposcope gui is an interactive app where you:
- Navigate through STEP entities one by one
- Watch 3D geometry build up progressively
- Inspect raw entity data and references
- Inspect individual problematic faces if you like
- Locate inconsistent face and edge orientations in the completed model
- Toggle between different visualization modes (normals, faces, control nets)
- Show or hide individual bodies using the checkboxes in the **Bodies** list above the entity tree

Use the playback controls to step forward/backward or animate through the entire file.
Open **Edit → Preferences** (`Ctrl+,` or `Ctrl+Shift+P`) for navigation, viewport, label, and
playback settings. Changes apply immediately and are saved automatically.
The Bodies list uses names stored on STEP solid or surface records when available;
unnamed bodies are shown by type and STEP entity number. Double-click a body to show
only it, or click **Show all** to restore every body. Visibility changes affect the
preview only.

## Playback and report

First-run defaults are **Auto zoom on**, **Max speed 100%**, and **Turbo off**.
Saved preferences take precedence on later launches. Turbo processes several
consecutive STEP entities per viewport refresh when enabled.

After playback reaches the last entity, the **Report** button beside Play opens
a technical report that you can copy for an LLM or save elsewhere. It stays
available if you scrub back through the file. The report includes the full
source path, STEP entity counts, spline control grid sizes, face preview checks,
topology findings, per-body processing time, and slow playback steps.

Report uses two separate scores. **Preview and topology health** starts at
100/100 and loses points for missing preview faces, topology warnings, and
failed face preview checks. **Processing pain** reflects the largest exceeded
processing or geometry-density limit, capped at 100/100. Report turns red when
health is below 80/100 or any of these pain limits is exceeded:

| Check | Red when |
| --- | --- |
| One `ADVANCED_FACE` geometry handler | More than 2 seconds |
| One STEP body | More than 60 seconds of measured geometry-handler work across its reachable entities |
| One B-spline surface | More than 1,000 control-point references |
| One B-spline edge or polyline | More than 256 control points or Cartesian-point references |

Otherwise Report uses the same green as Play. The report names each triggering
entity and gives its measured value. It also flags files with more than 1,000
`CARTESIAN_POINT` entities as a complexity clue; that total alone does not turn
Report red.

Geometry-handler timings measure Steposcope's preview work. The separate
**Step (ms)** values in the Playback list include playback delay, UI updates,
rendering, and possibly Turbo batching. These times depend on the local machine.
The scores are diagnostics for the preview and STEP edge connectivity, not a
certificate of CAD solid validity.

## Geometry preview

The viewport builds a tessellated preview from STEP entities. It supports common
analytic surfaces and several trimmed B-spline surface forms, including the
open loft patches used by the bundled squareloft examples. For a trim the
preview cannot triangulate, Steposcope reports the gap and shows the surface's
control net where available.

Preview meshes are for visualization; they do not modify the STEP file.
Entity IDs, source positions, arguments, and references remain available for
inspection.

## Inspecting same-direction edge pairs

After playback reaches the end of a STEP file, the viewport's **Model check**
summarizes face coverage and edge connectivity. A **same-direction pair** means
two face boundaries use the same `EDGE_CURVE` in the same effective direction.
The direction depends on each `ORIENTED_EDGE` sense and its face-bound sense;
the count alone does not identify which STEP flag needs correction.

When the check finds these pairs, the **Same-direction edge pairs** section
opens in the Interpretation panel. A clickable warning badge also appears in
the viewport so the issue is visible even when the panel is closed. Use
**Previous** and **Next** to inspect
each pair. The section lists the `EDGE_CURVE`, both `ADVANCED_FACE` and
`ORIENTED_EDGE` IDs, and their sense flags. Its detail buttons open those
entities without leaving the completed model view. The selected edge is
magenta, its two faces are colored separately, and **Fit affected edge**
frames the edge in the viewport.

**Blink affected edge and faces** flashes the selected edge three times, then
each connected face three times in turn. **Highlight edges and faces** is a
toggle for a single view of *all* same-direction pairs: the model becomes
grey, affected edges bright red, and affected faces receive stable green,
blue, and yellow shades. Turn it off to return to the selected pair. The
blink button returns to the selected-pair view before playing its sequence.

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/) package manager
- ~1.5 GB of free disk space for the virtual environment (`uv sync` creates it)

> [!WARNING]
> The dependency install is large, roughly 1.4 GB, because steposcope ships
> full GUI and rendering stacks:
>
> | Library | Approx. size | Why it's needed |
> | --- | --- | --- |
> | [PySide6](https://doc.qt.io/qtforpython/) | ~650 MB | Qt desktop GUI (windows, widgets, OpenGL bindings) |
> | [VTK](https://www.vtk.org/) | ~565 MB | 3D geometry rendering and tessellation |
> | [NumPy](https://numpy.org/) | ~60 MB | Vectorized math for surface/edge geometry |
>
> The remaining few hundred MB are transitive dependencies (Qt's bundled
> libraries, fonts) and dev tools (`pytest`, `pylint`, etc.). To skip the dev
> tooling, install with `uv sync --no-group dev`.

Run `./1.check_requirements.sh` to verify your installation — it will tell you exactly what to install if anything is missing.

## Headless rendering

Render a STEP file without opening the desktop viewer:

```bash
uv run steposcope render path/to/model.step --out reports
```

The launchers in [`headless/`](headless/) run without opening the desktop viewer.
`manual_view_render_step.sh` renders a single view by default and takes any
file, directory, or glob — including models under the bundled `corpus/` —
while passing additional render options through:

```bash
./headless/manual_view_render_step.sh path/to/model.step --width 800 --height 600
./headless/manual_view_render_step.sh corpus/ --camera iso --mode faces
```

`render_step_file.sh` uses the same renderer to write isometric, front, top,
and right views as separate PNGs. Pass `--views` to choose a different set.

`headless/gensnapshots.sh` creates a time-lapse of a STEP file's geometry
building up. Its defaults come from [`headless/snapshots.json`](headless/snapshots.json);
[`headless/snapshots.example.json`](headless/snapshots.example.json) is a template.

Output goes to `temp/render-step` by default. Set `HEADLESS_OUT` to choose a
different output directory. Run either script with `--help` for usage.

## STEP geometry compatibility

This chart describes the **viewport preview**. STEP entities can still be
inspected even when their geometry cannot be fully rendered.
The bundled examples are grouped by face geometry under [`corpus/`](corpus/README.md).

| Geometry in the STEP file | Preview support |
| --- | --- |
| Straight edges and simple planar faces (including squares) | Yes |
| Circular and elliptical edges | Yes |
| Common curved faces (cylinders, cones, spheres, and tori) | Yes, for supported trims |
| Trimmed B-spline surfaces, including the bundled square lofts | Partial; some trims render, others show a control net or a reported gap |
| Highly complex NURBS geometry | No reliable full preview yet |
