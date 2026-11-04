#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
source ./setup_env.sh
acervo_preparar
PYTHONPATH=src exec .venv/bin/python -m acervo serve --open "$@"
