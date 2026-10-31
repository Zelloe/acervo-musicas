"""Adapter run on spotDL's Python: searches to pick what to download.

Usage: python spotdl_search.py <command> <output.json> [arguments...]
Commands: albuns <artist> | edicoes <artist> <album> | musicas <text> | faixas <url> | sugestoes <text>
Or: python spotdl_search.py servir  (stays open; one JSON request per line on stdin)
Does not use the official Spotify API and does not download audio.
"""

import json
from pathlib import Path
import sys

NOISE = ("karaoke", "in the style of", "tribute", "made famous", "instrumental covers", "as made famous")


_ytmusic = None


def ytmusic():
    global _ytmusic
    if _ytmusic is None:
        from ytmusicapi import YTMusic
        _ytmusic = YTMusic()
    return _ytmusic


def client():
    from spotdl.utils.spotify import SpotifyClient
    SpotifyClient.init(client_id="", client_secret="", headless=True, max_retries=1, use_official_api=False)
    return SpotifyClient()


def picture(images, prefer: int = 300) -> str:
    """URL of the image closest to the requested size (covers and photos)."""
    images = [i for i in images or [] if isinstance(i, dict) and i.get("url")]
    if not images:
        return ""
    return min(images, key=lambda i: abs((i.get("width") or prefer) - prefer))["url"]


def same(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def albums(artist: str) -> list[dict]:
    # Album search without the official API comes back empty on Spotify; YouTube Music lists them well.
    found, seen = [], set()
    for item in ytmusic().search(artist, filter="albums", limit=40):
        names = [a.get("name", "") for a in item.get("artists") or []]
        title = item.get("title") or ""
        if not any(same(n, artist) for n in names) or any(word in title.casefold() for word in NOISE):
            continue
        key = title.casefold()
        if key not in seen:
            seen.add(key)
            found.append({"titulo": title, "artista": artist, "ano": item.get("year") or "", "tipo": item.get("type") or "", "capa": picture(item.get("thumbnails"))})
    return found


def editions(artist: str, album: str) -> list[dict]:
    query = f'artist:"{artist}" album:"{album}"'
    found = {}
    for track in client().search(query, type="track", limit=50)["tracks"]["items"]:
        item = track["album"]
        if not item["name"].casefold().startswith(album.casefold()):
            continue
        if not any(same(a["name"], artist) for a in item["artists"]):
            continue
        found.setdefault(item["id"], {"titulo": item["name"], "artista": ", ".join(a["name"] for a in item["artists"]), "tipo": item.get("album_type") or "", "url": item["external_urls"]["spotify"], "capa": picture(item.get("images"))})
    # The edition with the exact name comes first; deluxe/remaster after.
    return sorted(found.values(), key=lambda e: (not same(e["titulo"], album), len(e["titulo"])))


def songs(text: str) -> list[dict]:
    result = []
    for track in client().search(text, type="track", limit=25)["tracks"]["items"]:
        year = (track["album"].get("release_date") or "")[:4]
        result.append({
            "titulo": track["name"], "artista": ", ".join(a["name"] for a in track["artists"]),
            "album": track["album"]["name"], "ano": "" if year == "0000" else year,
            "duracao": round(track.get("duration_ms", 0) / 1000), "numero": track.get("track_number"),
            "url": track["external_urls"]["spotify"], "tipo_album": track["album"].get("album_type") or "",
            "capa": picture(track["album"].get("images")),
        })
    return result


def tracks(url: str) -> list[dict]:
    from spotdl.utils.search import get_simple_songs
    client()
    found = get_simple_songs([url])
    if not found or len(found) > 300:
        raise ValueError("The source must contain between 1 and 300 tracks")
    return [{
        "titulo": s.name, "artista": s.artist, "artistas": s.artists, "album": s.album_name,
        "artista_album": s.album_artist, "ano": str(s.year or ""), "data": s.date or "",
        "numero": s.track_number, "total": s.tracks_count, "disco": s.disc_number,
        "duracao": s.duration, "url": s.url, "capa": s.cover_url or "", "tipo_album": s.album_type or "",
    } for s in found]


def suggestions(text: str) -> dict:
    """Fast enough to run while typing: YouTube Music only."""
    terms = [t for t in ytmusic().get_search_suggestions(text) if isinstance(t, str)][:6]
    artists, seen = [], set()
    for item in ytmusic().search(text, filter="artists", limit=6):
        name = item.get("artist") or ""
        if name and name.casefold() not in seen and not any(word in name.casefold() for word in NOISE):
            seen.add(name.casefold())
            artists.append({"nome": name, "foto": picture(item.get("thumbnails"), 120)})
    return {"termos": terms, "artistas": artists[:4]}


HANDLERS = {"albuns": albums, "edicoes": editions, "musicas": songs, "faixas": tracks, "sugestoes": suggestions}


def serve():
    # Library warnings must not mix with the JSON responses.
    out, sys.stdout = sys.stdout, sys.stderr
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            handler = HANDLERS[request["cmd"]]
            answer = {"id": request.get("id"), "ok": True, "dados": handler(*request.get("args", []))}
        except Exception as error:
            answer = {"id": request.get("id") if isinstance(request, dict) else None, "ok": False, "erro": f"{type(error).__name__}: {error}"[:300]}
        out.write(json.dumps(answer, ensure_ascii=False) + "\n")
        out.flush()


def main():
    if sys.argv[1:] == ["servir"]:
        return serve()
    command, output, *args = sys.argv[1:]
    if command not in HANDLERS:
        raise SystemExit(f"Unknown command: {command}")
    Path(output).write_text(json.dumps(HANDLERS[command](*args), ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
