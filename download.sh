#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
source ./setup_env.sh
acervo_preparar
exec .venv/bin/python download_menu.py "$@"
