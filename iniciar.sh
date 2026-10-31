#!/usr/bin/env bash
# Atalho antigo, mantido por compatibilidade: use ./start.sh
exec "$(dirname -- "${BASH_SOURCE[0]}")/start.sh" "$@"
