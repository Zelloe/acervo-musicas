"""Song lyrics: those already in the file and those from LRCLIB (open, no key).

Looked-up lyrics stay in a local cache. Writing into collection tracks only
happens through save_lyrics()/save_lyrics_batch(), after the user reviews them,
with a full backup and undo; new downloads get the lyrics before entering the
folder.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, USLT

from messages import _, register
from search import normalize

register({
    "The lyrics service responded {code}; try again later": "O serviço de letras respondeu {code}; tente mais tarde",
    "No connection to the lyrics service": "Sem conexão com o serviço de letras",
    "Empty or invalid lyrics": "Letra vazia ou inválida",
    "Select at least one track": "Selecione ao menos uma faixa",
    "The selected tracks already have exactly these lyrics": "As faixas selecionadas já têm exatamente essas letras",
    "Looking up lyrics {current} of {total}": "Buscando letras {current} de {total}",
    "Lyrics lookup finished": "Busca de letras concluída",
    "The track already has exactly these lyrics": "A faixa já tem exatamente essa letra",
    "Invalid preview hash": "Hash da prévia inválido",
})

API = "https://lrclib.net/api"
USER_AGENT = "Acervo (https://github.com/Zelloe/acervo-musicas)"
STAMP = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
# Credits that some sources put in as if they were verses (lyricist, composer…).
CREDIT = re.compile(r"^\s*(作词|作曲|编曲|制作人?|监制|混音|和声|录音|出品|lyrics by|written by|composed by)\s*[:：]", re.I)
NOT_FOUND_DAYS = 7


def parse_lrc(text: str) -> list[dict]:
    """"[01:02.50]verse" becomes {"t": 62.5, "linha": "verse"}, in time order."""
    lines = []
    for raw in (text or "").splitlines():
        stamps = list(STAMP.finditer(raw))
        if not stamps or stamps[0].start() != 0:
            continue
        verse = raw[stamps[-1].end():].strip()
        if CREDIT.match(verse):
            continue
        for stamp in stamps:
            seconds = int(stamp.group(1)) * 60 + float(stamp.group(2).replace(":", "."))
            lines.append({"t": round(seconds, 3), "linha": verse})
    return sorted(lines, key=lambda line: line["t"])


def _clean_plain(text: str) -> str:
    return "\n".join(line for line in (text or "").strip().splitlines() if not CREDIT.match(line)).strip()


def _result(synced: str = "", plain: str = "", instrumental: bool = False, source: str = "", url: str = "") -> dict:
    lines = parse_lrc(synced)
    plain = _clean_plain(plain) or "\n".join(line["linha"] for line in lines).strip()
    return {"encontrada": bool(lines or plain or instrumental), "sincronizada": lines, "texto": plain,
            "instrumental": bool(instrumental), "fonte": source, "url": url}


def read_embedded(path: Path) -> dict | None:
    """Lyrics already present: a file tag or a .lrc with the same name next to it."""
    path = Path(path)
    texts = []
    try:
        if path.suffix.casefold() == ".mp3":
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = None
            if tags is not None:
                texts += [frame.text for frame in tags.getall("USLT") if frame.text.strip()]
                for frame in tags.getall("SYLT"):
                    texts.append("\n".join(f"[{int(ms) // 60000:02d}:{int(ms) % 60000 / 1000:05.2f}]{line}" for line, ms in frame.text) if frame.format == 2 else "")
        elif path.suffix.casefold() == ".flac":
            audio = FLAC(path)
            for key in ("LYRICS", "UNSYNCEDLYRICS", "SYNCEDLYRICS"):
                texts += [value for value in audio.get(key, []) if value.strip()]
    except Exception:
        texts = []
    sidecar = path.with_suffix(".lrc")
    if sidecar.is_file() and not sidecar.is_symlink():
        try:
            texts.append(sidecar.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            pass
    for text in texts:
        if parse_lrc(text):
            return _result(synced=text, source="arquivo")
    for text in texts:
        if text and text.strip():
            return _result(plain=text, source="arquivo")
    return None


def default_cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "acervo" / "letras"


class LyricsClient:
    """LRCLIB client with an on-disk cache and one request at a time."""

    def __init__(self, cache_dir: Path | None = None, opener=None, min_interval: float = 0.5, retries: int = 3, sleep=time.sleep):
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.opener = opener or (lambda request, timeout: urllib.request.urlopen(request, timeout=timeout))
        self.min_interval = min_interval
        self.retries = retries
        self.sleep = sleep
        self.lock = threading.Lock()
        self.last = 0.0

    def _key(self, artist, title, album, duration) -> Path:
        ident = "\x1f".join([normalize(artist), normalize(title), normalize(album or ""), str(round(duration or 0))])
        return self.cache_dir / (hashlib.sha256(ident.encode()).hexdigest()[:32] + ".json")

    def _get(self, endpoint: str, params: dict):
        url = f"{API}/{endpoint}?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        delay = 1.0
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self.last)
            if wait > 0:
                self.sleep(wait)
            self.last = time.monotonic()
            try:
                with self.opener(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=15) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                error.close()
                if error.code == 404:
                    return None
                if error.code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise RuntimeError(_("The lyrics service responded {code}; try again later", code=error.code)) from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt == self.retries:
                    raise RuntimeError(_("No connection to the lyrics service")) from error
            self.sleep(delay)
            delay *= 2

    def _lookup(self, artist, title, album, duration) -> dict:
        exact = self._get("get", {"artist_name": artist, "track_name": title, "album_name": album, "duration": round(duration) if duration else None})
        if exact is None:
            # No exact match: search by name and require a close duration.
            found = self._get("search", {"artist_name": artist, "track_name": title}) or []
            candidates = [r for r in found if isinstance(r, dict)
                          and normalize(r.get("trackName", "")) == normalize(title)
                          and normalize(r.get("artistName", "")) == normalize(artist)
                          and (not duration or abs((r.get("duration") or 0) - duration) <= 3)]
            candidates.sort(key=lambda r: (not r.get("syncedLyrics"), abs((r.get("duration") or 0) - (duration or 0))))
            exact = candidates[0] if candidates else None
        if not exact:
            return {"encontrada": False}
        return _result(exact.get("syncedLyrics") or "", exact.get("plainLyrics") or "", exact.get("instrumental"),
                       "lrclib", f"https://lrclib.net/api/get/{exact['id']}" if exact.get("id") else "")

    def lookup(self, artist: str, title: str, album: str = "", duration: float | None = None) -> dict:
        if not (artist or "").strip() or not (title or "").strip():
            return {"encontrada": False}
        path = self._key(artist, title, album, duration)
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            age = time.time() - cached.get("_salvo", 0)
            if cached.get("encontrada") or age < NOT_FOUND_DAYS * 86400:
                return {k: v for k, v in cached.items() if not k.startswith("_")}
        except (OSError, ValueError, AttributeError):
            pass
        with self.lock:
            result = self._lookup(artist, title, album, duration)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps({**result, "_salvo": time.time()}, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            pass  # without the cache, the lyrics are still shown
        return result


def lyrics_for_track(path: Path, artist: str, title: str, album: str, duration: float | None, client: LyricsClient) -> dict:
    """The file's lyrics take priority; otherwise LRCLIB's (without writing anything)."""
    return read_embedded(path) or client.lookup(artist, title, album, duration)


def embed_plain(path: Path, letra: dict) -> bool:
    """For NEW files: writes the plain lyrics (USLT) if the MP3 doesn't have any yet."""
    if path.suffix.casefold() != ".mp3" or not letra.get("encontrada") or letra.get("instrumental") or not letra.get("texto"):
        return False
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    if tags.getall("USLT"):
        return False
    tags.add(USLT(encoding=3, lang="XXX", desc="", text=letra["texto"]))
    tags.save(path, v2_version=tags.version[1] if tags.version[1] in (3, 4) else 4)
    return True


def current_lyrics(path: Path) -> list[str]:
    """Plain lyrics already written to the file, in the format write_validated checks."""
    path = Path(path)
    if path.suffix.casefold() == ".mp3":
        try:
            return [frame.text for frame in ID3(path).getall("USLT")]
        except ID3NoHeaderError:
            return []
    return list(FLAC(path).get("lyrics", []))


def format_lrc(lines: list[dict]) -> str:
    """{"t": 62.5, "linha": "verse"} becomes "[01:02.50]verse" (the common LRC form)."""
    stamped = []
    for line in lines:
        hundredths = int(round(line["t"] * 100))  # round first, so 59.999 s becomes 01:00.00 every time
        minutes, rest = divmod(hundredths, 6000)
        stamped.append(f"[{minutes:02d}:{rest // 100:02d}.{rest % 100:02d}]{line['linha']}")
    return "\n".join(stamped)


def prepare_text(text: str) -> str:
    """Canonical text to store: tidy LRC when it has timestamps, otherwise plain lyrics."""
    text = (text or "").replace("\r\n", "\n").strip()
    if not text or "\x00" in text or len(text) > 100_000:
        raise ValueError(_("Empty or invalid lyrics"))
    lines = parse_lrc(text)
    if lines:
        return format_lrc(lines)
    plain = _clean_plain(text)
    if not plain:
        raise ValueError(_("Empty or invalid lyrics"))
    return plain


def _operation(root: Path, file: str, text: str, previous_sha256: str | None) -> dict | None:
    from library_core import secure_file, sha256

    path = secure_file(root, file)
    text = prepare_text(text)
    current = current_lyrics(path)
    if current == [text]:
        return None
    if previous_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", str(previous_sha256)):
        raise ValueError(_("Invalid preview hash"))
    return {"acao": "metadados", "arquivo": str(path), "antes_sha256": previous_sha256 or sha256(path),
            "alteracoes": {"letra": {"atual": current, "proposto": [text]}}}


def save_lyrics(root: Path, storage: Path, file: str, text: str, previous_sha256: str | None = None) -> dict:
    """Writes lyrics (plain or synced LRC) into a collection track with backup, verification and undo.

    With previous_sha256 (the hash seen in the preview), the write is refused if the
    track has changed since then; the check happens under library_lock.
    """
    from library_import import import_reviewed

    root = Path(root).resolve(strict=True)
    operation = _operation(root, file, text, previous_sha256)
    if operation is None:
        raise ValueError(_("The track already has exactly these lyrics"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return import_reviewed(root, Path(storage) / "backups", [operation], f"letra-{stamp}", Path(storage))


def save_lyrics_batch(root: Path, storage: Path, items: list[dict]) -> dict:
    """Writes lyrics into many tracks in one reviewed transaction: one backup, one undo.

    items: [{"file": path, "text": lyrics, "previous_sha256": optional hash seen in the review}].
    Tracks that already have exactly that text are skipped and reported.
    """
    from library_import import import_reviewed

    if not isinstance(items, list) or not items or len(items) > 20_000:
        raise ValueError(_("Select at least one track"))
    root = Path(root).resolve(strict=True)
    operations, skipped = [], []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(_("Empty or invalid lyrics"))
        operation = _operation(root, item.get("file", ""), item.get("text", ""), item.get("previous_sha256"))
        if operation is None:
            skipped.append(item.get("file"))
        else:
            operations.append(operation)
    if not operations:
        raise ValueError(_("The selected tracks already have exactly these lyrics"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    result = import_reviewed(root, Path(storage) / "backups", operations, f"letra-lote-{stamp}", Path(storage))
    return {**result, "skipped": skipped}


def bulk_lookup(tracks: list[dict], client: LyricsClient, progress=None, cancel=None, max_failures: int = 5) -> list[dict]:
    """Looks up lyrics for many tracks without writing anything.

    Each result: {"id", "file", "status", "text", "current"} where status is
    "synced", "plain" or "instrumental" (found), "not_found", "already_synced"
    (timed lyrics already inside the file's tag) or "error"/"not_checked" when LRCLIB
    is unavailable. A file with plain lyrics is offered the synced version if found,
    and timed lyrics that only exist in a .lrc next to the track are offered for
    storing inside the file ("source": "lrc_file").
    After max_failures consecutive service errors the rest is marked not_checked.
    """
    progress = progress or (lambda *args: None)
    results, failures = [], 0
    for index, track in enumerate(tracks):
        file = track.get("arquivo", "")
        base = {"id": track.get("id"), "file": file, "text": "", "current": "none"}
        if (cancel is not None and cancel.is_set()) or failures >= max_failures:
            results.append({**base, "status": "not_checked"})
            continue
        progress(_("Looking up lyrics {current} of {total}", current=index + 1, total=len(tracks)), round(index / max(len(tracks), 1) * 100))
        path = Path(file) if file else None
        in_tag = [text for text in (current_lyrics(path) if path and path.is_file() else []) if text.strip()]
        if any(parse_lrc(text) for text in in_tag):
            results.append({**base, "current": "synced", "status": "already_synced"})
            continue
        base["current"] = "plain" if in_tag else "none"
        embedded = read_embedded(path) if path else None
        if embedded and embedded["sincronizada"]:
            # Timed lyrics only in a .lrc next to the track: offer to store them inside the file.
            results.append({**base, "status": "synced", "text": format_lrc(embedded["sincronizada"]), "source": "lrc_file"})
            continue
        artist = " / ".join(v for v in track.get("artista") or [] if v.strip())
        title = " / ".join(v for v in track.get("titulo") or [] if v.strip())
        album = " / ".join(v for v in track.get("album") or [] if v.strip())
        try:
            found = client.lookup(artist, title, album, track.get("duracao_segundos"))
            failures = 0
        except RuntimeError as error:
            failures += 1
            results.append({**base, "status": "error", "error": str(error)})
            continue
        if not found.get("encontrada"):
            results.append({**base, "status": "not_found"})
        elif found.get("instrumental"):
            results.append({**base, "status": "instrumental"})
        elif found.get("sincronizada"):
            results.append({**base, "status": "synced", "text": format_lrc(found["sincronizada"])})
        elif base["current"] == "plain":
            results.append({**base, "status": "not_found"})  # nothing better than what the file has
        else:
            results.append({**base, "status": "plain", "text": found.get("texto", "")})
    progress(_("Lyrics lookup finished"), 100)
    return results
