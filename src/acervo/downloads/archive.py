"""Internet Archive: FLAC recordings found with its public search and metadata APIs.

Only archive.org is contacted. Every file is checked against the size and MD5 the
Archive publishes, then validated with ffmpeg before it reaches the music folder.
Rights vary per item; the license and collection are shown so the user can decide.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from http.client import HTTPException
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from mutagen.flac import FLAC

from acervo.messages import _, register

register(
    {
        "The Internet Archive did not answer: {error}": "O Internet Archive não respondeu: {error}",
        "Invalid Internet Archive item": "Item do Internet Archive inválido",
        "This item has no FLAC files": "Este item não tem arquivos FLAC",
        "The download did not come from archive.org": "O download não veio do archive.org",
        "The file is larger than the Internet Archive listed": "O arquivo é maior do que o Internet Archive informou",
        "The file differs from the one the Internet Archive listed (size or MD5)": "O arquivo difere do informado pelo Internet Archive (tamanho ou MD5)",
        "Not enough space in the destination folder": "Não há espaço suficiente na pasta de destino",
    }
)

API = "https://archive.org"
PROVIDER = "internet_archive"
USER_AGENT = "Acervo (https://github.com/Zelloe/acervo-musicas)"
FLAC_FORMATS = ("Flac", "24bit Flac")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
# Lucene operators and syntax; the user's words are searched as plain text.
SYNTAX = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')
MAX_FILE_BYTES = 4 * 1024**3
CHUNK = 1024 * 1024
RETRIES = 5


def _host_ok(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (host == "archive.org" or host.endswith(".archive.org"))


def _open(url: str, timeout: int = 30, start: int = 0):
    headers = {"User-Agent": USER_AGENT, **({"Range": f"bytes={start}-"} if start else {})}
    response = urlopen(Request(url, headers=headers), timeout=timeout)
    # Downloads are redirected to a storage server; it must still be the Archive.
    if not _host_ok(response.geturl()):
        response.close()
        raise ValueError(_("The download did not come from archive.org"))
    return response


def _json(url: str) -> dict:
    try:
        with _open(url) as response:
            data = json.loads(response.read(20 * 1024 * 1024))
    except (OSError, ValueError) as error:
        raise RuntimeError(_("The Internet Archive did not answer: {error}", error=error)) from error
    return data if isinstance(data, dict) else {}


def _text(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if str(v).strip())
    return str(value or "").strip()


def _year(metadata: dict) -> str:
    match = re.match(r"\d{4}", _text(metadata.get("year")) or _text(metadata.get("date")))
    return match.group() if match else ""


def _collection(value) -> str:
    # "fav-<user>" collections are personal favourite lists, not where the item comes from.
    names = value if isinstance(value, list) else [value]
    return next((str(n) for n in names if n and not str(n).startswith("fav-")), "")


def search(text: str) -> list[dict]:
    words = " ".join(SYNTAX.sub(" ", text).split())
    if not words:
        return []
    params = [
        ("q", f"({words}) AND mediatype:(audio) AND format:(flac)"),
        *(("fl[]", f) for f in ("identifier", "title", "creator", "year", "date", "collection", "licenseurl")),
        ("sort[]", "downloads desc"),
        ("rows", "100"),
        ("output", "json"),
    ]
    docs = _json(f"{API}/advancedsearch.php?{urlencode(params)}").get("response", {}).get("docs", [])
    items = []
    for doc in docs if isinstance(docs, list) else []:
        identifier = _text(doc.get("identifier"))
        if not IDENTIFIER.fullmatch(identifier) or not _text(doc.get("title")):
            continue
        items.append(
            {
                "titulo": _text(doc.get("title")),
                "artista": _text(doc.get("creator")),
                "ano": _year(doc),
                "tipo": "Album",
                "capa": f"{API}/services/img/{identifier}",
                "provedor": PROVIDER,
                "ia_item": identifier,
                "licenca": _text(doc.get("licenseurl")),
                "colecao": _collection(doc.get("collection")),
            }
        )
    return items


def _seconds(value) -> float | None:
    text = _text(value)
    try:
        if ":" in text:
            total = 0.0
            for part in text.split(":"):
                total = total * 60 + float(part)
            return total
        return float(text) if text else None
    except ValueError:
        return None


def _number(value, default: int) -> int:
    match = re.match(r"\d+", _text(value))
    return int(match.group()) if match else default


def _title_from_name(name: str) -> str:
    """'disc1/01. Minglewood Blues.flac' -> 'Minglewood Blues'."""
    stem = Path(name).stem.replace("_", " ")
    # "01. ", "1-02 ", "d1t01 " and "t03 " are track numbers; "99 Luftballons" keeps its number.
    numbering = r"^\s*(?:(?:(?:cd|d|disc)\s?\d{1,2}\s?)?t(?:rack)?\s?\d{1,3}|0\d{1,2}|\d{1,3}(?=\s?[-.)]))(?:[-.]\d{1,3})?[\s.)-]+"
    return re.sub(numbering, "", stem, flags=re.I).strip() or stem


def tracks(identifier: str) -> list[dict]:
    if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
        raise ValueError(_("Invalid Internet Archive item"))
    data = _json(f"{API}/metadata/{identifier}")
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    files = [
        f
        for f in data.get("files") or []
        if isinstance(f, dict)
        and f.get("format") in FLAC_FORMATS
        and str(f.get("name", "")).casefold().endswith(".flac")
        and ".." not in str(f.get("name", "")).split("/")
        and re.fullmatch(r"[0-9a-f]{32}", str(f.get("md5", "")))
        and 0 < _number(f.get("size"), 0) <= MAX_FILE_BYTES
    ]
    # An item can hold the uploaded FLAC and FLAC derived from it; the original is the source.
    originals = [f for f in files if f.get("source") == "original"]
    files = originals or files
    if not files:
        raise ValueError(_("This item has no FLAC files"))
    album = _text(metadata.get("title"))
    artist = _text(metadata.get("creator"))
    license_url, collection = _text(metadata.get("licenseurl")), _collection(metadata.get("collection"))
    files.sort(key=lambda f: (_number(f.get("track"), 10**6), str(f["name"]).casefold()))
    return [
        {
            "titulo": _text(f.get("title")) or _title_from_name(f["name"]),
            "artista": _text(f.get("creator")) or _text(f.get("artist")) or artist,
            "album": _text(f.get("album")) or album,
            "ano": _year(metadata),
            "numero": _number(f.get("track"), index),
            "duracao": _seconds(f.get("length")),
            "tipo_album": "Album",
            "capa": f"{API}/services/img/{identifier}",
            "url": f"{API}/download/{identifier}/{quote(f['name'])}",
            "provedor": PROVIDER,
            "ia_item": identifier,
            "ia_arquivo": f["name"],
            "tamanho_bytes": _number(f.get("size"), 0),
            "md5": f["md5"],
            "licenca": license_url,
            "colecao": collection,
        }
        for index, f in enumerate(files, 1)
    ]


def safe_name(item: dict) -> str:
    """'Artist - Album - 03 - Title.flac': the same song from two concerts gets two names."""
    number = f"{item['numero']:02d}" if isinstance(item.get("numero"), int) else ""
    parts = [item.get("artista", ""), item.get("album", ""), number, item.get("titulo", "")]
    name = " - ".join(str(p).strip() for p in parts if str(p).strip())
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", name).strip().lstrip(".") or "track"
    while len(name.encode("utf-8")) > 200:
        name = name[:-1]
    return name + ".flac"


def fetch(item: dict, target: Path) -> None:
    """Download one listed file into `target`, checking size and MD5 against the listing."""
    expected = int(item["tamanho_bytes"])
    if shutil.disk_usage(target.parent).free < expected * 2:
        raise OSError(28, _("Not enough space in the destination folder"))
    digest, written, failures = hashlib.md5(usedforsecurity=False), 0, 0
    with target.open("wb") as handle:
        # Archive servers often drop slow connections; continue from the last byte instead of failing.
        while written < expected:
            before = written
            try:
                with _open(item["url"], timeout=60, start=written) as response:
                    if written and response.status != 206:  # the server ignored the range: start over
                        handle.seek(0)
                        handle.truncate()
                        digest, written = hashlib.md5(usedforsecurity=False), 0
                    while chunk := response.read(CHUNK):
                        written += len(chunk)
                        if written > expected:
                            raise ValueError(_("The file is larger than the Internet Archive listed"))
                        digest.update(chunk)
                        handle.write(chunk)
            except (OSError, HTTPException):
                if written == before:
                    failures += 1
                    if failures >= RETRIES:
                        raise
                continue
            if written == before:
                break  # the server ended the file without sending anything new
            failures = 0
    if written != expected or digest.hexdigest() != item["md5"]:
        raise ValueError(_("The file differs from the one the Internet Archive listed (size or MD5)"))


def fill_tags(path: Path, item: dict, genre: str = "") -> str:
    """Fill only empty Vorbis comments; returns the genre when it was added."""
    audio = FLAC(path)
    if audio.tags is None:
        audio.add_tags()
    values = {
        "title": item.get("titulo"),
        "artist": item.get("artista"),
        "album": item.get("album"),
        "date": item.get("ano"),
        "tracknumber": str(item["numero"]) if item.get("numero") else "",
        "genre": genre,
    }
    added = {key: value for key, value in values.items() if value and not any(v.strip() for v in audio.get(key, []))}
    for key, value in added.items():
        audio[key] = [value]
    if added:
        audio.save()
    return genre if "genre" in added else ""


def work_file(folder: Path) -> Path:
    """Temporary file beside the destination: big FLAC files should not fill a RAM-backed /tmp."""
    handle = tempfile.NamedTemporaryFile(prefix=".acervo-baixando-", suffix=".flac", dir=folder, delete=False)
    handle.close()
    return Path(handle.name)
