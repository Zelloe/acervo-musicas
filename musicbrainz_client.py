"""Optional MusicBrainz client, with caching, a request limit and conservative review."""

from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from library_scan import album_base, normalized
from library_core import atomic_json


def literal(value: str) -> str:
    return '"' + re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r'\\\1', value) + '"'


class MusicBrainz:
    def __init__(self, cache: Path, contact: str, max_requests: int = 200):
        if not contact.strip() or "\n" in contact or "\r" in contact:
            raise ValueError("Informe um contato real em --contato para identificar o cliente MusicBrainz")
        self.cache = cache
        cache.mkdir(parents=True, exist_ok=True)
        self.user_agent = f"BibliotecaMusicalLocal/0.1 ({contact})"
        self.max_requests = max_requests
        self.requests = 0
        self.disabled = False
        self.memo = {}

    def get(self, endpoint: str, **params) -> dict:
        params["fmt"] = "json"
        url = "https://musicbrainz.org/ws/2/" + endpoint + "?" + urlencode(sorted(params.items()))
        key = hashlib.sha256(url.encode()).hexdigest()
        cached = self.cache / (key + ".json")
        if cached.exists():
            try:
                data = json.loads(cached.read_text())
                if time.time() - data["timestamp"] < 30 * 86400:
                    return data["response"]
            except (ValueError, KeyError, OSError):
                pass
        if self.disabled or self.requests >= self.max_requests:
            raise ValueError("Consultas online suspensas ou limite atingido; revisão pendente")
        # The lock and the persisted clock share the limit across processes using this cache.
        with (self.cache / ".rate.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for attempt in range(3):
                clock = self.cache / ".last_request"
                try:
                    last = float(clock.read_text())
                except (ValueError, OSError):
                    last = 0
                time.sleep(max(0, 1.1 - (time.time() - last)))
                if self.requests >= self.max_requests:
                    raise ValueError("Limite de consultas online atingido")
                clock.write_text(str(time.time()))
                self.requests += 1
                try:
                    request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
                    with urlopen(request, timeout=12) as response:
                        data = json.load(response)
                    atomic_json(cached, {"timestamp": time.time(), "url": url, "response": data})
                    return data
                except HTTPError as error:
                    if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                        self.disabled = True
                        raise ValueError(f"MusicBrainz indisponível (HTTP {error.code}); consultas suspensas") from error
                    time.sleep(2 ** attempt)
                except (URLError, TimeoutError, OSError, ValueError) as error:
                    self.disabled = True
                    raise ValueError("MusicBrainz indisponível; consultas suspensas nesta análise") from error
        raise ValueError("Sem resposta MusicBrainz")

    def artist(self, name: str) -> dict | None:
        result = self.get("artist", query="artist:" + literal(name), limit=25)
        candidates = [a for a in result.get("artists", []) if normalized(a.get("name", "")) == normalized(name) and int(a.get("score", 0)) >= 95]
        # Never picks by popularity among artists with the same name.
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def choose_genre(data: dict) -> str | None:
        genres = [g for g in data.get("genres", []) if int(g.get("count", 0)) > 0]
        specific = [g for g in genres if normalized(g["name"]) not in ("rock", "metal", "pop")]
        ranked = sorted(specific or genres, key=lambda g: (-int(g["count"]), g["name"]))
        return ranked[0]["name"].title() if ranked else None

    def genre(self, artist: str, album: str) -> dict:
        key = (normalized(artist), normalized(album_base(album)))
        if key in self.memo:
            return self.memo[key]
        try:
            result = self._genre(artist, album)
        except ValueError as error:
            result = {"revisao": str(error)}
        self.memo[key] = result
        return result

    def _genre(self, name: str, album: str) -> dict:
        artist = self.artist(name)
        if artist is None:
            return {"revisao": "Artista MusicBrainz ausente ou ambíguo"}
        ident = artist["id"]
        groups = self.get("release-group", query=f"releasegroup:{literal(album_base(album))} AND arid:{ident}", limit=25)
        exact = [g for g in groups.get("release-groups", []) if normalized(g.get("title", "")) == normalized(album_base(album)) and int(g.get("score", 0)) >= 95 and any(c.get("artist", {}).get("id") == ident for c in g.get("artist-credit", []) if isinstance(c, dict))]
        if len(exact) != 1:
            return {"revisao": "Álbum MusicBrainz ausente ou ambíguo; o nome do artista sozinho não confirma sua identidade"}
        group = self.get("release-group/" + exact[0]["id"], inc="genres")
        genre = self.choose_genre(group)
        if genre:
            return {"genero": genre, "fonte": "https://musicbrainz.org/release-group/" + group["id"], "criterio": "Gênero do Release Group; gravação local não identificada"}
        artist_data = self.get("artist/" + ident, inc="genres")
        genre = self.choose_genre(artist_data)
        if genre:
            return {"genero": genre, "fonte": "https://musicbrainz.org/artist/" + ident, "criterio": "Gênero do artista após correspondência de álbum sem gênero; gravação local não identificada"}
        return {"revisao": "MusicBrainz sem gênero votado para este artista/álbum"}

    def recordings(self, artist: str, title: str, seconds: float) -> list[dict]:
        result = self.get("recording", query=f"recording:{literal(title)} AND artist:{literal(artist)}", limit=25)
        candidates = []
        for row in result.get("recordings", []):
            names = [c.get("artist", {}).get("name", "") for c in row.get("artist-credit", []) if isinstance(c, dict)]
            if normalized(row.get("title", "")) != normalized(title) or not any(normalized(n) == normalized(artist) for n in names):
                continue
            length = row.get("length")
            delta = abs(length / 1000 - seconds) if length else None
            candidates.append({"id": row["id"], "titulo": row["title"], "duracao_ms": length, "diferenca_segundos": delta, "duracao_compativel": delta is not None and delta <= 2, "desambiguacao": row.get("disambiguation", ""), "fonte": "https://musicbrainz.org/recording/" + row["id"], "status": "revisão obrigatória; não confirma áudio"})
        return candidates
