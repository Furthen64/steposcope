#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# The current Python QVTKRenderWindowInteractor uses an X11-native surface.
# Running Qt through XWayland avoids BadWindow errors in Wayland sessions.
# Override when testing a native Qt Wayland backend:
#   QT_QPA_PLATFORM=wayland ./runwl.sh
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"

exec uv run steposcope "$@"

