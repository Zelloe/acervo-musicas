#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
acervo_python=python
if [[ -x .venv/bin/python ]]; then
  acervo_python=.venv/bin/python
fi
exec "$acervo_python" corrigir_tags.py interface --abrir "$@"
