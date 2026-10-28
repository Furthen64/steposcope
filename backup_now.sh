#!/usr/bin/env bash
# Smart backup of the steposcope source tree into backups/<datetime>.zip.
# Excludes regenerable/generated artifacts (.venv, caches, temp, output, backups).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

mkdir -p backups
STAMP="$(date +'%Y%m%d-%H%M%S')"
OUT="backups/steposcope_${STAMP}.zip"

zip -r "$OUT" . \
  -x ".git/*" ".git" \
     ".venv/*" "venv/*" "env/*" \
     "__pycache__/*" "*/__pycache__/*" \
     ".pytest_cache/*" "*/.pytest_cache/*" \
     ".cache/*" "*/.cache/*" \
     "output/*" "temp/*" \
     "backups/*" \
     "*.pyc" "*.pyo" "*.egg-info/*" \
     "*.so" "*.dylib"

echo "Created: $OUT"
du -h "$OUT"
