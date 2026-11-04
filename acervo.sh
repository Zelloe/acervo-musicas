#!/usr/bin/env bash
# Command line: ./acervo.sh analyze, apply, restore, playlist... Relative paths follow your folder.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="$here/src" exec "$here/.venv/bin/python" -m acervo "$@"
