"""Search with suggestions: in the local library (offline) and in the spotDL global catalog."""

from __future__ import annotations

from collections import OrderedDict
import json
from pathlib import Path
import re
import queue
import subprocess
import threading
import time
import unicodedata

FIELDS = {"titulo": 3.0, "artista": 2.5, "album": 2.0, "genero": 1.0}
LABELS = {"titulo": "título", "artista": "artista", "album": "álbum", "genero": "gênero"}


def normalize(texto: str) -> str:
    """Lowercase, without accents or punctuation: "Ænima!" and "aenima" match."""
    texto = unicodedata.normalize("NFKD", str(texto)).replace("æ", "ae").replace("Æ", "ae").replace("ø", "o").replace("Ø", "o")
    texto = "".join(c for c in texto if not unicodedata.combining(c)).casefold()
    texto = texto.replace("&", " e ").replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[^\w]+", " ", texto).replace("_", " ").split())


def _distance(a: str, b: str, limit: int) -> int:
    """Levenshtein with a cutoff: returns limit + 1 as soon as the limit is exceeded."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _token_score(query: str, token: str) -> float:
    if token == query:
        return 1.0
    if token.startswith(query):
        return 0.8  # the user is still typing the word
    if len(query) >= 3 and query in token:
        return 0.6
    if len(query) >= 4:
        limit = 2 if len(query) >= 8 else 1
        # Also compare with the start of the word, for typos made mid-typing.
        if min(_distance(query, token, limit), _distance(query, token[:len(query)], limit) if len(token) > len(query) else limit + 1) <= limit:
            return 0.35
    return 0.0


class _Matcher:
    """Scores entities (tracks, artists, albums) described by text fields."""

    def __init__(self, entities: list[dict[str, str]]):
        self.entities = entities
        self.texts = [{field: normalize(text) for field, text in entity.items()} for entity in entities]
        self.vocabulary: dict[str, list[tuple[int, str]]] = {}
        for index, texts in enumerate(self.texts):
            for field, text in texts.items():
                tokens = set(text.split())
                if 1 < len(text.split()) <= 4:
                    tokens.add(text.replace(" ", ""))  # "acdc" finds "AC/DC", "pinkfloyd" finds "Pink Floyd"
                for token in tokens:
                    self.vocabulary.setdefault(token, []).append((index, field))

    def rank(self, raw_query: str, limit: int) -> list[tuple[float, int, str]]:
        query = normalize(raw_query)
        terms = list(dict.fromkeys(query.split()))
        if not terms:
            return []
        best: dict[int, list[tuple[float, str]]] = {}
        for position, term in enumerate(terms):
            for token, places in self.vocabulary.items():
                score = _token_score(term, token)
                if not score:
                    continue
                for index, field in places:
                    slots = best.setdefault(index, [(0.0, "")] * len(terms))
                    value = score * FIELDS[field]
                    if value > slots[position][0]:
                        slots[position] = (value, field)
        ranked = []
        for index, slots in best.items():
            if any(value == 0 for value, _ in slots):
                continue  # every term must appear
            total = sum(value for value, _ in slots)
            for field, text in self.texts[index].items():
                if len(terms) > 1 and query in text:
                    total += FIELDS[field]  # the whole phrase, in order
                if text == query:
                    total += FIELDS[field] * 1.5
            fields = [field for _, field in slots]
            ranked.append((round(total, 3), index, max(set(fields), key=fields.count)))
        ranked.sort(key=lambda item: (-item[0], self.texts[item[1]].get("titulo", "")))
        return ranked[:limit]


def _join(values) -> str:
    return " / ".join(v for v in values or [] if v.strip())


class LibraryIndex:
    """In-memory index of the tracks already read by the server; rebuild it when they change."""

    def __init__(self, tracks: list[dict]):
        self.tracks = tracks
        self.songs = _Matcher([{
            "titulo": _join(t.get("titulo")),
            "artista": " ".join([_join(t.get("artista")), _join(t.get("artista_album"))]),
            "album": _join(t.get("album")),
            "genero": _join(t.get("genero")),
        } for t in tracks])
        artists, albums = OrderedDict(), OrderedDict()
        for track in tracks:
            names = {}
            for name in list(track.get("artista") or []) + list(track.get("artista_album") or []):
                if name.strip():
                    names.setdefault(normalize(name), name)
            for key, name in names.items():  # an identical artist and album artist count once
                entry = artists.setdefault(key, {"nome": name, "faixas": 0})
                entry["faixas"] += 1
            artist = _join(track.get("artista_album")) or _join(track.get("artista"))
            album = _join(track.get("album"))
            if album:
                entry = albums.setdefault((normalize(artist), normalize(album)), {"titulo": album, "artista": artist, "track_id": track.get("id"), "faixas": 0})
                entry["faixas"] += 1
        self.artists = list(artists.values())
        self.albums = list(albums.values())
        self.artist_matcher = _Matcher([{"artista": a["nome"]} for a in self.artists])
        self.album_matcher = _Matcher([{"album": a["titulo"], "artista": a["artista"]} for a in self.albums])

    def search(self, consulta: str, limite: int = 50) -> list[dict]:
        return [{"id": self.tracks[index].get("id"), "pontuacao": score, "motivo": LABELS[field]}
                for score, index, field in self.songs.rank(consulta, limite)]

    def suggest(self, consulta: str, limite: int = 8) -> dict:
        artists = [self.artists[i] for _, i, _ in self.artist_matcher.rank(consulta, limite)]
        albums = [self.albums[i] for _, i, _ in self.album_matcher.rank(consulta, limite)]
        songs = []
        for score, index, _ in self.songs.rank(consulta, limite):
            track = self.tracks[index]
            songs.append({"id": track.get("id"), "titulo": _join(track.get("titulo")), "artista": _join(track.get("artista"))})
        return {"artistas": artists[:4], "albuns": albums[:4], "musicas": songs[:limite]}

    buscar = search  # temporary aliases (step B)
    sugerir = suggest

class GlobalSearch:
    """Global catalog suggestions from a spotDL process that stays open.

    Starting spotDL's Python takes ~2 s; with the process open, a suggestion takes ~1 s.
    """

    def __init__(self, command_factory=None, timeout: float = 15.0, cache_size: int = 200):
        if command_factory is None:
            import downloader
            command_factory = lambda: [*downloader.spotdl_python(), "-I", str(Path(downloader.ADAPTER)), "servir"]
        self.command_factory = command_factory
        self.timeout = timeout
        self.cache: OrderedDict[str, dict] = OrderedDict()
        self.cache_size = cache_size
        self.lock = threading.Lock()
        self.process = None
        self.counter = 0

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        self.process = subprocess.Popen(self.command_factory(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        # A thread reads the lines; several answers may arrive together in one chunk.
        self.lines = queue.Queue()
        threading.Thread(target=self._read, args=(self.process, self.lines), daemon=True).start()

    @staticmethod
    def _read(process, lines):
        try:
            for line in process.stdout:
                lines.put(line)
        except (OSError, ValueError):
            pass  # close() ended the process and its pipes
        lines.put(None)

    def _call(self, command: str, *args: str) -> object:
        self._start()
        # Local copies: close() may be called by another thread while we wait.
        process, lines = self.process, self.lines
        self.counter += 1
        try:
            process.stdin.write(json.dumps({"id": self.counter, "cmd": command, "args": list(args)}) + "\n")
            process.stdin.flush()
        except (OSError, ValueError):
            self.close()
            raise RuntimeError("A busca global foi encerrada inesperadamente") from None
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                self.close()  # a stuck process cannot answer the next query
                raise RuntimeError("A busca global demorou demais; tente de novo") from None
            if line is None:
                self.close()
                raise RuntimeError("A busca global foi encerrada inesperadamente")
            try:
                answer = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(answer, dict) or answer.get("id") != self.counter:
                continue  # a stray warning or a late answer to an earlier query
            if not answer.get("ok"):
                raise RuntimeError(f"A busca global falhou: {answer.get('erro', 'sem detalhes')}")
            return answer.get("dados")

    def suggest(self, consulta: str) -> dict:
        key = normalize(consulta)
        if len(key) < 2:
            return {"termos": [], "artistas": []}
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            result = self._call("sugestoes", consulta.strip()[:200])
            from downloader import safe_photo
            for artist in result.get("artistas", []):
                artist["foto"] = safe_photo(artist.get("foto"))
            self.cache[key] = result
            while len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
            return result

    def close(self):
        """Ends the process; a query in progress fails with RuntimeError."""
        process, self.process = self.process, None
        if not process:
            return
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for stream in (process.stdin, process.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


    sugerir = suggest  # temporary aliases (step B)
    fechar = close


# Temporary aliases for server.py during the English rename (step B); remove when server.py is updated.
normalizar = normalize
IndiceBiblioteca = LibraryIndex
BuscaGlobal = GlobalSearch
