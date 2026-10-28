#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'USAGE'
Usage: ./headless/render_step_file.sh <STEP_FILE|DIRECTORY|GLOB> [render options...]

Render STEP geometry from isometric, front, top, and right views. A separate
PNG is written for each view. Render options are passed through to
manual_view_render_step.sh; pass --views to select a different set of views.

Examples:
  ./headless/render_step_file.sh model.step
  ./headless/render_step_file.sh corpus/ --width 800 --height 600
  ./headless/render_step_file.sh model.step --views iso,over,under
USAGE
}

if (( $# < 1 )) || [[ "$1" == "-h" || "$1" == "--help" ]]; then
    usage
    exit $(( $# < 1 ? 2 : 0 ))
fi

INPUT="$1"
shift

for option in "$@"; do
    if [[ "$option" == "--views" || "$option" == --views=* ]]; then
        exec "$SCRIPT_DIR/manual_view_render_step.sh" "$INPUT" "$@"
    fi
done

exec "$SCRIPT_DIR/manual_view_render_step.sh" "$INPUT" --views iso,front,top,right "$@"
