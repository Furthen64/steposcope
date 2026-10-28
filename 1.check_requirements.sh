#!/usr/bin/env bash
# Read-only checks for a new Steposcope checkout.

cd -- "$(dirname -- "${BASH_SOURCE[0]}")" || exit 1

printf 'Steposcope requirements check\n\n'
ready=true

if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    printf 'OK: %s\n' "$(python3 --version)"
else
    printf 'Note: Python 3.12 or newer was not found as python3.\n'
    printf 'uv can find or download a suitable Python when you run uv sync.\n'
fi

if command -v uv >/dev/null 2>&1; then
    printf 'OK: %s\n' "$(uv --version)"
else
    printf 'Missing: uv package manager.\n'
    printf 'Installation instructions: https://docs.astral.sh/uv/getting-started/installation/\n'
    ready=false
fi

printf '\n'
if [ "$ready" = false ]; then
    printf 'Install uv, then run ./1.check_requirements.sh again.\n'
    exit 1
fi

printf 'Next, from this directory, run:\n\n'
printf '  uv sync\n\n'
printf 'This downloads dependencies and creates or updates .venv in this directory.\n'
printf 'uv may also download Python and use its shared cache outside this directory.\n'
printf 'Allow approximately 1.5 GB of disk space.\n\n'
printf 'Then start Steposcope with:\n\n'
printf '  ./launch.sh\n\n'
printf 'To open a file directly: ./launch.sh path/to/model.step\n'
printf 'See LINUX_README.md for Linux setup and troubleshooting.\n'
