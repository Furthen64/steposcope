#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

usage() {
    cat <<'USAGE'
Usage: ./headless/manual_view_render_step.sh <STEP_FILE|DIRECTORY|GLOB> [render options...]

Render STEP geometry and write a JSON report plus PNG preview(s).
Any file, directory or glob works, e.g. a path under the bundled corpus:
  ./headless/manual_view_render_step.sh model.step --width 800 --height 600
  ./headless/manual_view_render_step.sh corpus/ --camera iso --mode faces

The default preview uses shaded surfaces; pass --mode normals to inspect
face orientation colors.

Set HEADLESS_OUT to change the output directory (default: temp/render-step).
USAGE
}

if (( $# < 1 )) || [[ "$1" == "-h" || "$1" == "--help" ]]; then
    usage
    exit $(( $# < 1 ? 2 : 0 ))
fi

INPUT="$1"
shift

OUTPUT_DIR="${HEADLESS_OUT:-temp/render-step}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PROJECT_DIR/.cache/uv}"

echo "Rendering: $INPUT"
echo "Output:    $OUTPUT_DIR"
for option in "$@"; do
    if [[ "$option" == "--mode" || "$option" == --mode=* ]]; then
        exec uv run python -m step_explorer render "$INPUT" --out "$OUTPUT_DIR" "$@"
    fi
done
exec uv run python -m step_explorer render "$INPUT" --out "$OUTPUT_DIR" --mode shaded "$@"
