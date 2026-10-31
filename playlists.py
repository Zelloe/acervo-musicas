"""Portable M3U8 playlists, with paths relative to the playlist file."""

from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile

from library_scan import TEMP_PREFIXES, read_track
from messages import _, register

register({
    "A path with a line break does not fit in M3U8: {path}": "Caminho com quebra de linha não cabe em M3U8: {path}",
    "The playlist must have the .m3u8 extension": "A playlist deve ter a extensão .m3u8",
    "No songs selected for the playlist": "Nenhuma música selecionada para a playlist",
    "The playlist already exists: {playlist}": "A playlist já existe: {playlist}",
    "The saved playlist does not match the preview: {playlist}": "A playlist gravada não confere com a prévia: {playlist}",
})


def _text(values) -> str:
    return " / ".join(v.strip() for v in values or [] if v.strip())


def _number(values) -> int:
    match = re.match(r"\s*(\d+)", (values or [""])[0])
    return int(match.group(1)) if match else 0


def collect(root: Path, progress=None) -> tuple[list[dict], list[dict]]:
    """Reads the collection without writing anything; follows the same exclusions as the inventory."""
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
    """Builds the content; the playlist's folder is the base for the paths."""
    base = playlist.parent.resolve()
    lines = ["#EXTM3U"]
    if name:
        lines.append("#PLAYLIST:" + " ".join(name.split()))
    for track in tracks:
        path = Path(track["arquivo"]).resolve()
        relative = os.path.relpath(path, base).replace(os.sep, "/")
        if any(c in relative for c in "\r\n"):
            raise ValueError(_("A path with a line break does not fit in M3U8: {path}", path=path))
        if relative.startswith("#"):
            relative = "./" + relative  # lines starting with # are comments
        label = " - ".join(part for part in (_text(track.get("artista")), _text(track.get("titulo"))) if part) or path.stem
        seconds = round(track["duracao_segundos"]) if track.get("duracao_segundos") else -1
        lines.append(f"#EXTINF:{seconds},{' '.join(label.split())}")
        lines.append(relative)
    return "\n".join(lines) + "\n"


def _sync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish_exclusive(temp: Path, playlist: Path, data: bytes) -> None:
    """Publishes without ever replacing a file that has appeared at the path."""
    try:
        os.link(temp, playlist)  # atomic and exclusive
        return
    except FileExistsError:
        raise
    except OSError:
        pass  # exFAT/FAT and some network shares lack hard links
    fd = os.open(playlist, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        playlist.unlink(missing_ok=True)
        raise


def write_m3u8(playlist: Path, tracks: list[dict], name: str | None = None, overwrite: bool = False) -> dict:
    if playlist.suffix.casefold() != ".m3u8":
        raise ValueError(_("The playlist must have the .m3u8 extension"))
    if not tracks:
        raise ValueError(_("No songs selected for the playlist"))
    data = render(playlist, tracks, name).encode("utf-8")
    playlist.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and os.path.lexists(playlist):
        raise FileExistsError(_("The playlist already exists: {playlist}", playlist=playlist))
    fd, temp_name = tempfile.mkstemp(prefix=".acervo-playlist-", suffix=".tmp", dir=playlist.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temp, playlist)  # atomic: the previous one stays intact until the swap
        else:
            _publish_exclusive(temp, playlist, data)
        _sync_dir(playlist.parent)
    finally:
        temp.unlink(missing_ok=True)
    if playlist.read_bytes() != data:
        raise ValueError(_("The saved playlist does not match the preview: {playlist}", playlist=playlist))
    return {"playlist": str(playlist), "faixas": len(tracks), **validate_m3u8(playlist)}


def validate_m3u8(playlist: Path) -> dict:
    """Checks that each entry resolves to a file from the playlist's folder."""
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
