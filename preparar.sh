# Usado por iniciar.sh e baixar.sh: prepara a .venv na primeira execução.
acervo_preparar() {
  if [[ -x .venv/bin/python ]] && .venv/bin/python -c "import mutagen" 2>/dev/null; then
    return 0
  fi
  local py
  py=$(command -v python3 || command -v python || true)
  if [[ -z $py ]]; then
    echo "Python 3 não encontrado. No Ubuntu/Mint: sudo apt install python3 python3-venv" >&2
    return 1
  fi
  if ! "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "O Acervo precisa do Python 3.10 ou mais novo (encontrado: $("$py" --version 2>&1))." >&2
    return 1
  fi
  echo "Preparando o ambiente na primeira execução…"
  if ! "$py" -m venv .venv; then
    rm -rf .venv
    echo "Não foi possível criar o ambiente Python. No Ubuntu/Mint: sudo apt install python3-venv" >&2
    return 1
  fi
  if ! .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt; then
    echo "Falha ao instalar as dependências. Confira a conexão com a internet e tente de novo." >&2
    return 1
  fi
}
