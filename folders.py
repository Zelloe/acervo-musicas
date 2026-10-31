"""The system's default music folder (e.g. ~/Música on a Portuguese-language Linux)."""

from __future__ import annotations

import os
from pathlib import Path
import shlex


def default_music_folder() -> Path:
    """Reads XDG_MUSIC_DIR the way xdg-user-dir does; without configuration, uses ~/Music."""
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


def require_folder(folder: Path, option: str = "--pasta") -> Path:
    folder = folder.expanduser()
    if not folder.is_dir():
        raise ValueError(f'A pasta de músicas não existe: {folder}\nInforme onde estão suas músicas, por exemplo: {option} "$HOME/Música"')
    return folder.resolve()
