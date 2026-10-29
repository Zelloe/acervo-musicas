"""Pasta de músicas padrão do sistema (ex.: ~/Música no Linux em português)."""

from __future__ import annotations

import os
from pathlib import Path
import shlex


def pasta_musicas_padrao() -> Path:
    """Lê XDG_MUSIC_DIR como o xdg-user-dir; sem configuração, usa ~/Music."""
    home = Path.home()
    if os.environ.get("XDG_MUSIC_DIR"):
        return Path(os.environ["XDG_MUSIC_DIR"]).expanduser()
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "user-dirs.dirs"
    try:
        lines = config.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    for line in lines:
        name, _, value = line.strip().partition("=")
        if name == "XDG_MUSIC_DIR" and value:
            try:
                value = shlex.split(value)[0]
            except (ValueError, IndexError):
                continue
            value = value.replace("$HOME", str(home))
            if value.startswith("/") and value.rstrip("/") != str(home):
                return Path(value)
    return home / "Music"


def exigir_pasta(pasta: Path, opcao: str = "--pasta") -> Path:
    pasta = pasta.expanduser()
    if not pasta.is_dir():
        raise ValueError(f'A pasta de músicas não existe: {pasta}\nInforme onde estão suas músicas, por exemplo: {opcao} "$HOME/Música"')
    return pasta.resolve()
