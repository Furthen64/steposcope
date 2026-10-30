#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

export STEPOSCOPE_PROFILE=1
exec ./launch.sh corpus/TOUGH_stresstests/AL39.STEP
