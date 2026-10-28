# WINDOWS_README.md — steposcope on Windows

Fast path for a Windows user who just cloned the repo. The main
[README](README.md) has the full feature tour; this page covers setup and
the Windows-specific commands only. Written for PowerShell 5+ (built into
Windows) — no WSL needed.

## Prerequisites

1. **Python 3.12+** — check with `python --version`. If missing, grab it
   from <https://www.python.org/downloads/> (tick "Add python.exe to PATH").
2. **[uv](https://docs.astral.sh/uv/)** — open PowerShell and run:
   ```powershell
   irm https://astral.sh/uv/install.ps1 | iex
   ```
3. **Graphics drivers** — nothing to install beyond your normal GPU
   drivers; the renderer talks OpenGL through Qt/VTK.

## Install and run

```powershell
git clone https://github.com/Furthen64/STEPoscope.git
cd STEPoscope
uv sync             # ~1.4 GB: Qt + VTK + NumPy and friends (reuse the cache next time)
.\winlaunch.ps1     # GUI opens
```

`winlaunch.ps1` resolves the project from its own directory, so you can
call it from anywhere and pass STEP paths relative to wherever you are now:

```powershell
.\winlaunch.ps1 corpus\planar_only\box125.step
```

Or go straight through Python's console script (equivalent):

```powershell
uv run steposcope
```

Once the GUI is up, the same workflow as Linux applies: press **Play** and
watch the STEP file build up entity by entity; dig in with the Bodies list,
the Report button, and the interpretation panels.

## Things you can try first

- `.\winlaunch.ps1 corpus\complex_bspline\slotfillet3.step` — watch a
  B-spline surface + its fillet accumulate.
- `.\winlaunch.ps1 corpus\BROKEN_examples\...` — check the same-direction
  edge pairs and face-coverage diagnostics the Report surfaces.
- `.\winlaunch.ps1 corpus\planar_only\multiple_cubes.step` — play with the
  Bodies checkboxes and double-click to isolate individual bodies.

## Headless rendering from PowerShell

The `.sh` scripts are bash-only. Use the Python entry point directly:

```powershell
# isometric view of one file, written to .\reports\
uv run steposcope render corpus\planar_only\box125.step --out reports

# every view of every small demo model in the corpus (take your time)
uv run steposcope render corpus --out reports
```

Add `--width 1024 --height 768`, `--camera iso`, `--mode normals` etc. with
`uv run steposcope render --help` to see the full flag list.

## Known issues on Windows

- **Antivirus may complain the first time** Windows Defender scans the
  bundled cache — this is normal for a new tool and one of the reasons we
  do not ship UPX-packed binaries.
- **NVIDIA Optimus laptops**: if the window opens black or crashes on the
  first frame, force the discrete GPU for `steposcope` in Windows Settings >
  System > Display > Graphics.
- **Known issue**: `%APPDATA%` is currently not consulted. The config file
  lives at `%USERPROFILE%\.config\steposcope\config.toml` instead — you can
  copy `docs\steposcope.toml.example` there, or set the `STEPOSCOPE_CONFIG`
  environment variable to point anywhere you like. An unreadable config
  file falls back to defaults rather than blocking the app.

## What gets installed where

- Repository clone — everything, including ~40 tiny STEP demo models under `corpus\`.
- `uv` cache under `%LOCALAPPDATA%\uv\cache` — wheels downloaded once.
- Virtual environment `.venv\` inside the clone — delete it any time,
  `uv sync` rebuilds it.
