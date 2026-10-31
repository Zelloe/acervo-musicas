"""Download songs with spotDL straight into a folder, picking artist, album, edition and tracks."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, ID3NoHeaderError, TCON

from library_plan import BASE, rules
from library_scan import TEMP_PREFIXES, album_base, normalized
from lyrics import LyricsClient, embed_lyrics
from messages import _, register
from versions import classify

register({
    "spotDL not found. Install it with: pipx install spotdl": "spotDL não encontrado. Instale com: pipx install spotdl",
    "Could not find spotDL's Python in {executable}": "Não foi possível descobrir o Python do spotDL em {executable}",
    "The spotDL search failed: {detail}": "A busca no spotDL falhou: {detail}",
    "no details": "sem detalhes",
    "Enter the artist name": "Informe o nome do artista",
    "Enter the album name": "Informe o nome do álbum",
    "Enter what to search for": "Informe o que procurar",
    "Enter a Spotify link": "Informe um link do Spotify",
    "Use a Spotify track, album or playlist link": "Use um link do Spotify de música, álbum ou playlist",
    "ffmpeg not found; it is required by spotDL and to validate the audio": "ffmpeg não encontrado; ele é necessário para o spotDL e para validar o áudio",
    "Invalid track link": "Link de faixa inválido",
    "Timed out": "Tempo esgotado",
    "spotDL exited with code {code}": "spotDL terminou com código {code}",
    "spotDL produced more than one file": "O spotDL gerou mais de um arquivo",
    "The downloaded audio is corrupted or incomplete": "O áudio baixado está corrompido ou incompleto",
    "A file with this name already exists in the folder": "Já existe um arquivo com esse nome na pasta",
    "The destination folder does not exist: {folder}": "A pasta de destino não existe: {folder}",
    "Invalid bitrate": "Bitrate inválido",
    "Downloading {current} of {total} · {label}": "Baixando {current} de {total} · {label}",
    "Stopped after the previous error": "Interrompido após o erro anterior",
    "Downloads finished": "Downloads concluídos",
})


ADAPTER = BASE / "spotdl_search.py"
TEMPLATE = "{artist} - {title}.{output-ext}"
REMASTER = re.compile(r"\s*(?:-\s*|\(|\[)\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?(?:\s+version)?\s*[\])]?\s*$", re.I)


def spotdl_executable() -> str:
    # When launched from the system menu, PATH may not include ~/.local/bin (pipx).
    found = shutil.which("spotdl") or str(Path.home() / ".local/bin/spotdl")
    if not Path(found).is_file():
        raise RuntimeError(_("spotDL not found. Install it with: pipx install spotdl"))
    return found


def spotdl_python() -> list[str]:
    """The adapter must run on the Python that spotDL is installed in."""
    executable = Path(spotdl_executable()).resolve()
    try:
        first = executable.open(encoding="utf-8").readline().strip()
    except (UnicodeError, OSError):
        first = ""
    command = shlex.split(first[2:]) if first.startswith("#!") else []
    if not command or not any("python" in Path(part).name for part in command):
        raise RuntimeError(_("Could not find spotDL's Python in {executable}", executable=executable))
    return command


def _adapter(command: str, *args: str, timeout: int = 120) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="acervo-busca-") as folder:
        output = Path(folder) / "resultado.json"
        process = subprocess.run([*spotdl_python(), "-I", str(ADAPTER), command, str(output), *args], capture_output=True, text=True, timeout=timeout)
        if process.returncode or not output.is_file():
            detail = (process.stderr or process.stdout).strip().splitlines()
            raise RuntimeError(_("The spotDL search failed: {detail}", detail=detail[-1] if detail else _("no details")))
        return json.loads(output.read_text(encoding="utf-8"))


def _text(value: str, message: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 300 or value.strip().startswith("-"):
        raise ValueError(message)
    return value.strip()


# Only images from cover CDNs; no arbitrary URLs coming from the search.
PHOTO_HOSTS = re.compile(r"https://(?:[a-z0-9-]+\.googleusercontent\.com|i\.scdn\.co|mosaic\.scdn\.co|image-cdn-[a-z0-9-]+\.spotifycdn\.com)/[^\s\"'<>]+")


def safe_photo(url) -> str:
    return url if isinstance(url, str) and PHOTO_HOSTS.fullmatch(url) else ""


def _enrich(items: list[dict], title_key: str | None, album_key: str, kind_key: str) -> list[dict]:
    for item in items:
        item["versao"] = classify(item.get(title_key, "") if title_key else "", item.get(album_key, ""), item.get(kind_key, ""))
        for key in ("capa", "foto"):
            if key in item:
                item[key] = safe_photo(item[key])
    return items


def search_albums(artist: str) -> list[dict]:
    return _enrich(_adapter("albuns", _text(artist, _("Enter the artist name"))), None, "titulo", "tipo")


def editions(artist: str, album: str) -> list[dict]:
    return _enrich(_adapter("edicoes", _text(artist, _("Enter the artist name")), _text(album, _("Enter the album name"))), None, "titulo", "tipo")


def search_songs(text: str) -> list[dict]:
    return _enrich(_adapter("musicas", _text(text, _("Enter what to search for"))), "titulo", "album", "tipo_album")


def tracks(url: str) -> list[dict]:
    url = _text(url, _("Enter a Spotify link"))
    if not re.fullmatch(r"https://open\.spotify\.com/(?:intl-[a-z-]+/)?(track|album|playlist)/[A-Za-z0-9]+(?:\?\S*)?", url):
        raise ValueError(_("Use a Spotify track, album or playlist link"))
    return _enrich(_adapter("faixas", url, timeout=300), "titulo", "album", "tipo_album")


def _key(artist: str, title: str) -> str:
    return normalized(artist) + "\x1f" + normalized(REMASTER.sub("", title))


def local_index(root_folder: Path) -> set[str]:
    """Artist + title of everything already in the folder, to mark "already owned"."""
    keys = set()
    for folder, dirs, names in os.walk(root_folder, followlinks=False):
        for name in names:
            path = Path(folder) / name
            if path.suffix.casefold() not in (".mp3", ".flac") or name.startswith(TEMP_PREFIXES):
                continue
            try:
                tags = MutagenFile(path, easy=True)
            except Exception:
                tags = None
            titles = list((tags or {}).get("title", []))
            artists = list((tags or {}).get("artist", [])) + list((tags or {}).get("albumartist", []))
            for artist in artists:
                for title in titles:
                    keys.add(_key(artist, title))
            if " - " in path.stem:
                artist, title = path.stem.split(" - ", 1)
                keys.add(_key(artist, title))
    return keys


def mark_owned(items: list[dict], indice: set[str]) -> list[dict]:
    for item in items:
        artists = [item.get("artista", "")] + list(item.get("artistas") or [])
        item["ja_tenho"] = any(_key(a, item.get("titulo", "")) in indice for a in artists if a)
    return items


def genre_from_rules(artist: str, album: str, rule_rows: list[dict] | None = None) -> str:
    """Same priority as the analysis: album rule, then artist rule; only if it is unique."""
    rule_rows = rules(BASE / "genre_rules.csv") if rule_rows is None else rule_rows
    by_artist = [r for r in rule_rows if normalized(r["artista"]) == normalized(artist)]
    specific = [r for r in by_artist if r.get("album") and normalized(r["album"]) == normalized(album_base(album))]
    chosen = specific or [r for r in by_artist if not r.get("album")]
    options = {r["genero"].strip() for r in chosen if r.get("genero", "").strip()}
    return next(iter(options)) if len(options) == 1 else ""


def _fill_genre(path: Path, genre: str) -> bool:
    if not genre or path.suffix.casefold() != ".mp3":
        return False
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    if any(str(v).strip() for f in tags.getall("TCON") for v in f.text):
        return False
    tags.add(TCON(encoding=3, text=[genre]))
    tags.save(path, v2_version=tags.version[1] if tags.version[1] in (3, 4) else 4)
    return True


def _valid_audio(path: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(_("ffmpeg not found; it is required by spotDL and to validate the audio"))
    # Without -xerror ffmpeg exits with 0 even on decoding errors.
    process = subprocess.run([ffmpeg, "-v", "error", "-xerror", "-nostdin", "-i", str(path), "-map", "0:a:0", "-f", "null", "-"], capture_output=True, timeout=180)
    return process.returncode == 0


def _publish(source: Path, final: Path) -> None:
    """Put the finished file at the destination without ever replacing an existing one."""
    fd, name = tempfile.mkstemp(prefix=".acervo-baixando-", suffix=final.suffix, dir=final.parent)
    temp = Path(name)
    try:
        os.fchmod(fd, 0o644)  # mkstemp creates 0600; players and other programs need to read it
        with os.fdopen(fd, "wb") as handle, source.open("rb") as origin:
            shutil.copyfileobj(origin, handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, final)  # atomic and exclusive
        except FileExistsError:
            raise
        except OSError:
            # exFAT/FAT and some network shares have no hard links.
            out = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                with os.fdopen(out, "wb") as handle, source.open("rb") as origin:
                    shutil.copyfileobj(origin, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                final.unlink(missing_ok=True)
                raise
    finally:
        temp.unlink(missing_ok=True)


def _download_one(item: dict, folder: Path, executable: str, genre_rules: list[dict], bitrate: str, lyrics_client: LyricsClient | None = None) -> dict:
    url = item.get("url", "")
    if not re.fullmatch(r"https://open\.spotify\.com/track/[A-Za-z0-9]+", url.split("?")[0]):
        return {**item, "estado": "erro", "mensagem": _("Invalid track link")}
    with tempfile.TemporaryDirectory(prefix="acervo-download-") as work:
        work = Path(work)
        # Relative template with cwd in the folder: spotDL sanitizes absolute paths.
        command = [executable, "download", url, "--output", TEMPLATE, "--format", "mp3", "--bitrate", bitrate,
                   "--overwrite", "skip", "--threads", "1", "--simple-tui", "--print-errors"]
        try:
            process = subprocess.run(command, cwd=work, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            return {**item, "estado": "erro", "mensagem": _("Timed out")}
        produced = [p for p in work.iterdir() if p.suffix.casefold() == ".mp3" and p.is_file() and not p.is_symlink()]
        lines = [line for line in (process.stdout + process.stderr).splitlines() if line.strip()]
        if process.returncode or len(produced) != 1:
            reason = lines[-1][:300] if lines else _("spotDL exited with code {code}", code=process.returncode)
            return {**item, "estado": "erro", "mensagem": reason if process.returncode or not produced else _("spotDL produced more than one file")}
        audio = produced[0]
        if not _valid_audio(audio):
            return {**item, "estado": "erro", "mensagem": _("The downloaded audio is corrupted or incomplete")}
        genre = genre_from_rules(item.get("artista_album") or item.get("artista", ""), item.get("album", ""), genre_rules)
        filled = _fill_genre(audio, genre)
        embedded = False
        if lyrics_client:
            try:
                embedded = embed_lyrics(audio, lyrics_client.lookup(item.get("artista", ""), item.get("titulo", ""), item.get("album", ""), item.get("duracao")))
            except RuntimeError:
                embedded = False  # without lyrics the download still counts
        final = folder / audio.name
        try:
            _publish(audio, final)
        except FileExistsError:
            return {**item, "estado": "ja_existia", "arquivo": str(final), "mensagem": _("A file with this name already exists in the folder")}
        return {**item, "estado": "baixado", "arquivo": str(final), "genero_preenchido": genre if filled else "", "letra": embedded}


def download(items: list[dict], folder: Path, progress=None, bitrate: str = "320k", lyrics: LyricsClient | bool | None = True) -> list[dict]:
    """Download each track straight into `pasta`, never replacing an existing file.

    spotDL works in a temporary folder outside the collection; the file only shows
    up at the destination after spotDL finishes successfully and the audio is validated.
    Errors are recorded per track and the partial result is always returned.
    """
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        raise ValueError(_("The destination folder does not exist: {folder}", folder=folder))
    if bitrate not in ("128k", "192k", "256k", "320k"):
        raise ValueError(_("Invalid bitrate"))
    progress = progress or (lambda *args: None)
    genre_rules = rules(BASE / "genre_rules.csv")
    executable = spotdl_executable()
    lyrics = LyricsClient() if lyrics is True else (lyrics or None)
    results = []
    for index, item in enumerate(items):
        label = f"{item.get('artista', '')} - {item.get('titulo', '')}"
        progress(_("Downloading {current} of {total} · {label}", current=index + 1, total=len(items), label=label), round(index / len(items) * 100))
        try:
            results.append(_download_one(item, folder, executable, genre_rules, bitrate, lyrics))
        except Exception as error:
            results.append({**item, "estado": "erro", "mensagem": f"{type(error).__name__}: {error}"[:300]})
            # Without ffmpeg, or without space/write access at the destination, the next ones would fail the same way.
            if isinstance(error, RuntimeError) or (isinstance(error, OSError) and error.errno in (errno.ENOSPC, errno.EROFS, errno.EDQUOT, errno.EACCES)):
                results.extend({**rest, "estado": "nao_baixado", "mensagem": _("Stopped after the previous error")} for rest in items[index + 1:])
                break
    progress(_("Downloads finished"), 100)
    return results
