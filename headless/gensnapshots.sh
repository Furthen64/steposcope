#!/usr/bin/env bash
# Produce a fixed-camera time-lapse of a STEP file's geometry building up.
#   ./headless/gensnapshots.sh /absolute/path/to/model.step
# Extra flags pass straight through, e.g.:
#   ./headless/gensnapshots.sh model.step --interval 10 --views iso,front
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if (( $# < 1 )); then
    echo "usage: $0 <FULL_PATH_TO_STEP> [extra steposcope snapshots flags]" >&2
    exit 2
fi

INPUT="$1"
shift

if [[ ! -f "$INPUT" ]]; then
    echo "gensnapshots: no such file: $INPUT" >&2
    exit 2
fi

export UV_CACHE_DIR="${UV_CACHE_DIR:-$PROJECT_DIR/.cache/uv}"

# Cadence/size/output default to headless/snapshots.json when present
# (see headless/snapshots.example.json). This runs fully offscreen: no X/Wayland.
exec uv run python -m step_explorer snapshots "$INPUT" "$@"
