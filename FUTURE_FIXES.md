# FUTURE_FIXES.md — shrinking the release footprint

Speculative notes from a 2026-10-27 discussion. Nothing here is implemented;
item order is the suggested order of attack.

Baseline: the dev `.venv` is ~1.4 GB. The big three are
PySide6 (~650 MB), VTK (`vtkmodules`, ~565 MB), and NumPy (~60 MB).

## Current state of the code (facts, verified by grep)

- ~~All VTK imports go through `vtkmodules.all` (kitchen-sink import) in
  `step_explorer/snapshots.py`, `step_explorer/headless.py`,
  `step_explorer/geometry/builder.py`, `step_explorer/visualization/vtk_view.py`.~~
  Fixed 2026-10-27: all imports now go through `step_explorer/vtk_lite.py`
  (see item 1).
- VTK classes actually used (full list): PolyData, PolyDataMapper,
  PolyDataNormals, TriangleFilter, ContourTriangulator, FeatureEdges,
  CellPicker, sources (Arrow/Axes/Line/Outline), points/cells/arrays
  (Points, CellArray, FloatArray, IdList, PolyLine, Triangle), renderer
  stack (Renderer, RenderWindow, Actor, Property), interactor style
  (TrackballCamera), text actors (Billboard/Text), PNGWriter,
  WindowToImageFilter — roughly 10-12 of VTK's ~90 modules.
- PySide6 usage: Widgets, Core, Gui, plus QtTest in one place.
- The `viskores` libs (~60 MB inside `vtkmodules`) are never used; they are
  only mapped because of the `vtkmodules.all` import. ~~(still true)~~
  Fixed 2026-10-27 by item 1: no VTK `.so` outside the ~22 needed modules
  gets mapped.

## 1. Modular VTK imports — cheapest, best leverage — DONE (2026-10-27)

Implemented via `step_explorer/vtk_lite.py`: explicit imports of the ~20
classes in use plus the OpenGL2/FreeType backends, exposed as a `vtk`
namespace. All 6 `vtkmodules.all` import sites now go through it
(`snapshots.py` x3, `headless.py`, `geometry/builder.py`,
`visualization/vtk_view.py`), and `from vtk.util.numpy_support ...` was
switched to `vtkmodules.util.numpy_support` (the `vtk` shim is itself a
kitchen-sink re-export). Verified: 22 VTK modules mapped instead of ~90+,
RSS after import ~220 MB → ~149 MB, test suite green (CI-style env:
`QT_QPA_PLATFORM=offscreen` with DISPLAY/WAYLAND_DISPLAY unset — under a
Wayland session the render smoke test hangs the same way with and without
this change).

Original notes:

Switch `vtkmodules.all` to explicit imports, e.g. `vtkmodules.core`,
`vtkFiltersSources`, `vtkRenderingOpenGL2`, etc.

- Disk size does not shrink on its own (the wheel ships all `.so` files),
  but runtime RSS drops a lot because shared objects are lazily mmapped.
- Unlocks item 3: bundling tools only package what is imported, and that is
  where large disk savings land.
- Effort: mechanical, roughly an hour.
- Caveats: `vtk.util.numpy_support` comes through the compatibility shim;
  text actors drag in `vtkRenderingFreeType`; pick the module set by trial.

## 2. PySide6 → PySide6-Essentials

Only QtCore/QtGui/QtWidgets/QtTest are used; the umbrella `PySide6` package
also pulls the Addons wheel (WebEngine, Charts, Quick, 3D, ...). Depending on
`PySide6-Essentials` instead can cut ~150-250 MB.

- Check first: which wheel provides `QtTest` (used in exactly one place).
  Unverified.

## 3. Packaged release artifact (PyInstaller / Nuitka / briefcase)

If a release means a distributable bundle, this is where the win lands.
With items 1+2 done, the bundler includes only what is imported —
realistically ~350-450 MB total instead of a full dev venv. Also strips
dev dependencies (`pytest`, `pylint`, ...) automatically, and users no
longer need a Python environment at all.

## 4. Replace VTK entirely — big rewrite, v2.0 territory

VTK is effectively used as a mesh-display and picking layer, and the
tessellated geometry is already built in numpy (`geometry/builder.py`).
A custom `QOpenGLWidget` + shaders (~500 lines) or pyqtgraph's OpenGL item
could replace all 565 MB. Fiddly parts: `vtkCellPicker` picking and text
overlays. High risk; only worth it if distribution size is strategic
(e.g. educational distribution).

## What cannot be removed

- Qt widget stack — that is the app.
- NumPy and its ~27 MB OpenBLAS — geometry math. Non-negotiable.

## Minor cheap shavings

- `--onefile` installers with zstd/UPX trade disk for startup decode time;
  skip UPX (antivirus false positives).
- Strip `.pyi` stubs, translation files, and `__pycache__` from packaged
  bundles (thousands of files' worth).
- matplotlib + fontTools + PIL (~55 MB): figure out why they are in the
  tree at all (likely transitive); if tests/dev-only, they vanish under
  item 3. If something runtime needs them, make that dependency explicit.

## Ranking

Items 1 → 2 → 3: 1.4 GB → ~400 MB with low risk, each independently
shippable. Item 4 is a separate major-version project, not a release
thinner.
