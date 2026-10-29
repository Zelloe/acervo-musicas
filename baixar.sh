#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
source ./preparar.sh
acervo_preparar
exec .venv/bin/python baixar.py "$@"
