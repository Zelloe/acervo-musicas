#!/usr/bin/env bash
# Atalho antigo, mantido por compatibilidade: use ./download.sh
exec "$(dirname -- "${BASH_SOURCE[0]}")/download.sh" "$@"
