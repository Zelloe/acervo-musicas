"""Letras de músicas: as que já estão no arquivo e as do LRCLIB (aberto, sem chave).

Nada é gravado na coleção: letras buscadas ficam num cache local. Só arquivos
novos baixados pelo Acervo recebem a letra antes de entrar na pasta.
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

from search import normalize

API = "https://lrclib.net/api"
USER_AGENT = "Acervo (https://github.com/Zelloe/acervo-musicas)"
STAMP = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
# Créditos que algumas fontes colocam como se fossem versos (letrista, compositor…).
CREDIT = re.compile(r"^\s*(作词|作曲|编曲|制作人?|监制|混音|和声|录音|出品|lyrics by|written by|composed by)\s*[:：]", re.I)
NOT_FOUND_DAYS = 7


def parse_lrc(text: str) -> list[dict]:
    """"[01:02.50]verso" vira {"t": 62.5, "linha": "verso"}, em ordem de tempo."""
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
    """Letra já presente: tag do arquivo ou um .lrc com o mesmo nome ao lado."""
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
    """Cliente do LRCLIB com cache em disco e uma requisição por vez."""

    def __init__(self, cache_dir: Path | None = None, opener=None, min_interval: float = 0.5, retries: int = 3, sleep=time.sleep):
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.opener = opener or (lambda request, timeout: urllib.request.urlopen(request, timeout=timeout))
        self.min_interval = min_interval
        self.retries = retries
        self.sleep = sleep
        self.lock = threading.Lock()
        self.last = 0.0

    def _key(self, artista, titulo, album, duracao) -> Path:
        ident = "\x1f".join([normalize(artista), normalize(titulo), normalize(album or ""), str(round(duracao or 0))])
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
                    raise RuntimeError(f"O serviço de letras respondeu {error.code}; tente mais tarde") from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt == self.retries:
                    raise RuntimeError("Sem conexão com o serviço de letras") from error
            self.sleep(delay)
            delay *= 2

    def _lookup(self, artista, titulo, album, duracao) -> dict:
        exact = self._get("get", {"artist_name": artista, "track_name": titulo, "album_name": album, "duration": round(duracao) if duracao else None})
        if exact is None:
            # Sem correspondência exata: procura pelo nome e exige duração próxima.
            found = self._get("search", {"artist_name": artista, "track_name": titulo}) or []
            candidates = [r for r in found if isinstance(r, dict)
                          and normalize(r.get("trackName", "")) == normalize(titulo)
                          and normalize(r.get("artistName", "")) == normalize(artista)
                          and (not duracao or abs((r.get("duration") or 0) - duracao) <= 3)]
            candidates.sort(key=lambda r: (not r.get("syncedLyrics"), abs((r.get("duration") or 0) - (duracao or 0))))
            exact = candidates[0] if candidates else None
        if not exact:
            return {"encontrada": False}
        return _result(exact.get("syncedLyrics") or "", exact.get("plainLyrics") or "", exact.get("instrumental"),
                       "lrclib", f"https://lrclib.net/api/get/{exact['id']}" if exact.get("id") else "")

    def lookup(self, artista: str, titulo: str, album: str = "", duracao: float | None = None) -> dict:
        if not (artista or "").strip() or not (titulo or "").strip():
            return {"encontrada": False}
        path = self._key(artista, titulo, album, duracao)
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            age = time.time() - cached.get("_salvo", 0)
            if cached.get("encontrada") or age < NOT_FOUND_DAYS * 86400:
                return {k: v for k, v in cached.items() if not k.startswith("_")}
        except (OSError, ValueError, AttributeError):
            pass
        with self.lock:
            result = self._lookup(artista, titulo, album, duracao)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps({**result, "_salvo": time.time()}, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            pass  # sem cache, a letra continua sendo mostrada
        return result

    buscar = lookup  # temporary alias (step B)


def lyrics_for_track(path: Path, artista: str, titulo: str, album: str, duracao: float | None, cliente: LyricsClient) -> dict:
    """A do arquivo tem prioridade; senão, a do LRCLIB (sem gravar nada)."""
    # Temporary: calls the old name so server tests that patch it keep working during step B.
    return read_embedded(path) or cliente.buscar(artista, titulo, album, duracao)


def embed_plain(path: Path, letra: dict) -> bool:
    """Para arquivos NOVOS: grava a letra simples (USLT) se o MP3 ainda não tiver."""
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
    """Letras simples já gravadas no arquivo, no formato que write_validated confere."""
    path = Path(path)
    if path.suffix.casefold() == ".mp3":
        try:
            return [frame.text for frame in ID3(path).getall("USLT")]
        except ID3NoHeaderError:
            return []
    return list(FLAC(path).get("lyrics", []))


def save_lyrics(root: Path, storage: Path, arquivo: str, texto: str, anterior_sha256: str | None = None) -> dict:
    """Grava a letra numa faixa da coleção com backup, conferência e desfazer pelo Histórico.

    Com anterior_sha256 (o hash visto na prévia), a gravação é recusada se a
    faixa mudou desde então; a conferência acontece sob o library_lock.
    """
    from library_core import secure_file, sha256
    from library_import import import_reviewed

    texto = (texto or "").replace("\r\n", "\n").strip()
    if not texto or "\x00" in texto or len(texto) > 100_000:
        raise ValueError("Letra vazia ou inválida")
    if parse_lrc(texto):
        raise ValueError("Grave a letra sem marcações de tempo; a versão sincronizada fica no cache do Acervo")
    root = Path(root).resolve(strict=True)
    path = secure_file(root, arquivo)
    current = current_lyrics(path)
    if current == [texto]:
        raise ValueError("A faixa já tem exatamente essa letra")
    if anterior_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", str(anterior_sha256)):
        raise ValueError("Hash da prévia inválido")
    operation = {"acao": "metadados", "arquivo": str(path), "antes_sha256": anterior_sha256 or sha256(path),
                 "alteracoes": {"letra": {"atual": current, "proposto": [texto]}}}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return import_reviewed(root, Path(storage) / "backups", [operation], f"letra-{stamp}", Path(storage))


# Temporary aliases for server.py during the English rename (step B); remove when server.py is updated.
Letras = LyricsClient
letra_da_faixa = lyrics_for_track
letra_atual = current_lyrics
gravar_letra = save_lyrics
