# LINUX_README.md — steposcope on Linux

This page is the fast path for a Linux user who just cloned the repo and
wants to see STEP geometry on screen. The main [README](README.md) has the
full feature tour; this page covers setup and first run only.

## Prerequisites

1. **Python 3.12+** — `python3 --version` should print 3.12 or newer.
2. **[uv](https://docs.astral.sh/uv/)** — `curl -LsSf https://astral.sh/uv/install.sh | sh`
   (or your package manager: `sudo apt install uv` on recent distros, `pacman -S uv`).
3. **OpenGL system libraries** — any desktop Linux has them, but a bare
   server/minimal install may not:
   ```bash
   sudo apt install -y libgl1 libegl1 libxkbcommon0 libfontconfig1 libdbus-1-3
   ```
   (package names above are Debian/Ubuntu; equivalents exist on Fedora/Arch)

## Install and run

```bash
git clone https://github.com/Furthen64/STEPoscope.git
cd STEPoscope
./1.check_requirements.sh      # sanity check (optional but useful)
uv sync             # ~1.4 GB: Qt + VTK + NumPy and friends
./runwl.sh          # GUI opens
```

`./runwl.sh` forces the X11 platform backend (`QT_QPA_PLATFORM=xcb`) because
the VTK widget is happiest under XWayland; that is what you want on both
X11 sessions and modern Wayland desktops (GNOME/KDE).

Open a starter model right away:

```bash
./runwl.sh corpus/planar_only/box125.step
```

Then work your way through `corpus/`: each subfolder (see
[corpus/README.md](corpus/README.md)) shows one facet — analytic surfaces,
B-spline lofts, non-manifold bodies, broken models.

## Known issues on Linux

- **Wayland native sessions**: run via `./runwl.sh` (XWayland). Forcing
  `QT_QPA_PLATFORM=wayland` directly is not recommended — the VTK
  `QVTKRenderWindowInteractor` still expects an X11 surface and can hit
  BadWindow crashes.
- **Headless/CI machines without a display** work fine using the offscreen
  pathway:
  ```bash
  QT_QPA_PLATFORM=offscreen uv run pytest -q          # full test suite
  uv run steposcope render model.step --out reports   # PNG render, no GUI
  ```
- **NVIDIA + Wayland**: if the window opens black, `export __GLX_VENDOR_LIBRARY_NAME=`
  collision is the usual suspect — running under `./runwl.sh` (XWayland) sidesteps it.
- The `.sh` process launchers in `headless/` are bash-only. Windows users use
  the two commands documented in [WINDOWS_README.md](WINDOWS_README.md).

## Run the demos, break the parser

Once the GUI is up, three things to try first:

1. **Playback**: `./runwl.sh corpus/complex_bspline/slotfillet3.step` — watch
   how surface information unfolds one entity at a time; Play can pause/scrub,
   Turbo goes entity-batch-speed.
2. **Regenerate a knowledge base**: open `corpus/BROKEN_examples/...` files
   to see the Report panel flag inconsistent edges and face-coverage gaps
   (the "same-direction edge pairs" section).
3. **Snapshot several views of a model** without the GUI:
   ```bash
   ./headless/render_step_file.sh corpus/analytic_curved/ --width 800 --height 600
   ```
   writes iso/front/top/right PNGs under `temp/render-step/`.
