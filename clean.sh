#!/usr/bin/env bash
# Delete everything this project can regenerate, to preview a fresh checkout.
# Supersedes the old clearoutputs.sh — this also removes the headless
# snapshot output (./output, ./temp) plus any custom output root given with
# --output.
#   ./clean.sh              # removes caches, build artifacts, outputs and .venv;
#                           # keeps the uv wheel cache (.cache/uv) so the next
#                           # `uv sync` rebuilds offline and fast
#   ./clean.sh --output DIR # also removes DIR (a custom HEADLESS_OUT root);
#                           # other flags can combine with it
#   ./clean.sh --all        # also removes .cache/uv (fully pristine, needs
#                           # network or a global uv cache for the next sync)
#   ./clean.sh --yes        # skip the confirmation gate (for scripts)
# The gate lists what exists and waits for the word 'yes'; Enter aborts.
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

ALL=0
ASSUME_YES=0
EXTRA_OUTPUT=""
while (( $# )); do
    case "$1" in
        --all) ALL=1 ;;
        --yes) ASSUME_YES=1 ;;
        --output) shift; [[ $# -gt 0 ]] || { echo "usage: $0 [--output DIR] [--all] [--yes]" >&2; exit 2; }; EXTRA_OUTPUT="$1" ;;
        --output=*) EXTRA_OUTPUT="${1#--output=}" ;;
        *) echo "usage: $0 [--output DIR] [--all] [--yes]" >&2; exit 2 ;;
    esac
    shift
done

targets=(
    .venv
    .pytest_cache
    .coverage
    steposcope.egg-info
    build
    dist
    temp
    output
)
if [[ -n "$EXTRA_OUTPUT" ]]; then
    [[ -e "$EXTRA_OUTPUT" ]] || { echo "clean: nothing to remove ($EXTRA_OUTPUT does not exist)"; exit 0; }
    targets+=("$EXTRA_OUTPUT")
fi
(( ALL )) && targets+=(.cache)

existing=()
for target in "${targets[@]}"; do
    [[ -e "$target" ]] && existing+=("$target")
done
pycache_count="$(find . -type d -name __pycache__ -not -path './.venv/*' | wc -l)"
(( pycache_count > 0 )) && existing+=("$pycache_count __pycache__ dir(s)")

if (( ${#existing[@]} == 0 )); then
    echo "clean: nothing to remove"
    exit 0
fi

echo "clean: this will remove:"
for item in "${existing[@]}"; do
    echo "  - $item"
done
if (( ASSUME_YES )); then
    REPLY=yes
    echo "clean: --yes given, proceeding"
else
    read -r -p "write 'yes' to confirm or press Enter to abort: " REPLY || REPLY=""
fi
if [[ "$REPLY" != "yes" ]]; then
    echo "clean: aborted"
    exit 0
fi

find . -type d -name __pycache__ -prune -exec rm -rf -- {} +
for target in "${existing[@]}"; do
    [[ -e "$target" ]] || continue
    rm -rf -- "$target"
    echo "  removed $target"
done

if (( ALL == 0 )); then
    [[ -e .cache ]] && echo "  kept .cache/uv (use --all to remove it too)"
fi

echo
echo "Fresh start: recreate the environment and run the suite with"
echo "  uv sync && uv run pytest"
