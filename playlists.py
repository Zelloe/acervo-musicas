"""Playlists M3U8 portáteis, com caminhos relativos ao arquivo da playlist."""

from __future__ import annotations

import os
from pathlib import Path
import re

from diagnostico_biblioteca import TEMP_PREFIXES, read_track


def _text(values) -> str:
    return " / ".join(v.strip() for v in values or [] if v.strip())


def _number(values) -> int:
    match = re.match(r"\s*(\d+)", (values or [""])[0])
    return int(match.group(1)) if match else 0


def collect(root: Path, progress=None) -> tuple[list[dict], list[dict]]:
    """Lê a coleção sem gravar nada; segue as mesmas exclusões do inventário."""
    paths = []
    for folder, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(folder) / d).is_symlink()]
        for name in names:
            path = Path(folder) / name
            if path.suffix.casefold() in (".mp3", ".flac") and not name.startswith(TEMP_PREFIXES) and not path.is_symlink():
                paths.append(path)
    tracks, errors = [], []
    for index, path in enumerate(sorted(paths)):
        if progress:
            progress(index + 1, len(paths), path.name)
        try:
            tracks.append(read_track(path))
        except Exception as error:
            errors.append({"arquivo": str(path), "erro": f"{type(error).__name__}: {error}"})
    return tracks, errors


def album_order(track: dict) -> tuple:
    artist = _text(track.get("artista_album")) or _text(track.get("artista"))
    return (artist.casefold(), _text(track.get("album")).casefold(), _number(track.get("disco")), _number(track.get("faixa")), track["arquivo"])


def matches(track: dict, field: str, wanted: str | None) -> bool:
    if not wanted:
        return True
    return any(value.strip().casefold() == wanted.strip().casefold() for value in track.get(field) or [])


def render(playlist: Path, tracks: list[dict], name: str | None = None) -> str:
    """Gera o conteúdo; a pasta da playlist é a referência dos caminhos."""
    base = playlist.parent.resolve()
    lines = ["#EXTM3U"]
    if name:
        lines.append("#PLAYLIST:" + " ".join(name.split()))
    for track in tracks:
        path = Path(track["arquivo"]).resolve()
        relative = os.path.relpath(path, base).replace(os.sep, "/")
        if any(c in relative for c in "\r\n"):
            raise ValueError(f"Caminho com quebra de linha não cabe em M3U8: {path}")
        if relative.startswith("#"):
            relative = "./" + relative  # linhas iniciadas por # são comentários
        label = " - ".join(part for part in (_text(track.get("artista")), _text(track.get("titulo"))) if part) or path.stem
        seconds = round(track["duracao_segundos"]) if track.get("duracao_segundos") else -1
        lines.append(f"#EXTINF:{seconds},{' '.join(label.split())}")
        lines.append(relative)
    return "\n".join(lines) + "\n"


def write_m3u8(playlist: Path, tracks: list[dict], name: str | None = None, overwrite: bool = False) -> dict:
    if playlist.suffix.casefold() != ".m3u8":
        raise ValueError("A playlist deve ter a extensão .m3u8")
    if not tracks:
        raise ValueError("Nenhuma música selecionada para a playlist")
    content = render(playlist, tracks, name)
    playlist.parent.mkdir(parents=True, exist_ok=True)
    # "x" nunca substitui uma playlist existente sem pedido explícito.
    with playlist.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    return {"playlist": str(playlist), "faixas": len(tracks), **validate_m3u8(playlist)}


def validate_m3u8(playlist: Path) -> dict:
    """Confere se cada entrada resolve para um arquivo a partir da pasta da playlist."""
    base = playlist.parent
    entries, missing = 0, []
    for line in playlist.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries += 1
        if not (base / line).is_file():
            missing.append(line)
    return {"entradas": entries, "encontradas": entries - len(missing), "ausentes": missing}
