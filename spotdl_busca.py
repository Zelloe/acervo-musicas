"""Adaptador executado no Python do spotDL: buscas para escolher o que baixar.

Uso: python spotdl_busca.py <comando> <saida.json> [argumentos...]
Comandos: albuns <artista> | edicoes <artista> <album> | musicas <texto> | faixas <url>
Não usa a API oficial do Spotify nem baixa áudio.
"""

import json
from pathlib import Path
import sys

NOISE = ("karaoke", "in the style of", "tribute", "made famous", "instrumental covers", "as made famous")


def client():
    from spotdl.utils.spotify import SpotifyClient
    SpotifyClient.init(client_id="", client_secret="", headless=True, max_retries=1, use_official_api=False)
    return SpotifyClient()


def same(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def albums(artist: str) -> list[dict]:
    # A busca de álbuns sem API oficial volta vazia no Spotify; o YouTube Music lista bem.
    from ytmusicapi import YTMusic
    found, seen = [], set()
    for item in YTMusic().search(artist, filter="albums", limit=40):
        names = [a.get("name", "") for a in item.get("artists") or []]
        title = item.get("title") or ""
        if not any(same(n, artist) for n in names) or any(word in title.casefold() for word in NOISE):
            continue
        key = title.casefold()
        if key not in seen:
            seen.add(key)
            found.append({"titulo": title, "artista": artist, "ano": item.get("year") or "", "tipo": item.get("type") or ""})
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
        found.setdefault(item["id"], {"titulo": item["name"], "artista": ", ".join(a["name"] for a in item["artists"]), "tipo": item.get("album_type") or "", "url": item["external_urls"]["spotify"]})
    # A edição com o nome exato aparece primeiro; deluxe/remaster depois.
    return sorted(found.values(), key=lambda e: (not same(e["titulo"], album), len(e["titulo"])))


def songs(text: str) -> list[dict]:
    result = []
    for track in client().search(text, type="track", limit=25)["tracks"]["items"]:
        year = (track["album"].get("release_date") or "")[:4]
        result.append({
            "titulo": track["name"], "artista": ", ".join(a["name"] for a in track["artists"]),
            "album": track["album"]["name"], "ano": "" if year == "0000" else year,
            "duracao": round(track.get("duration_ms", 0) / 1000), "numero": track.get("track_number"),
            "url": track["external_urls"]["spotify"],
        })
    return result


def tracks(url: str) -> list[dict]:
    from spotdl.utils.search import get_simple_songs
    client()
    found = get_simple_songs([url])
    if not found or len(found) > 300:
        raise ValueError("A fonte deve conter entre 1 e 300 faixas")
    return [{
        "titulo": s.name, "artista": s.artist, "artistas": s.artists, "album": s.album_name,
        "artista_album": s.album_artist, "ano": str(s.year or ""), "data": s.date or "",
        "numero": s.track_number, "total": s.tracks_count, "disco": s.disc_number,
        "duracao": s.duration, "url": s.url, "capa": s.cover_url or "",
    } for s in found]


def main():
    command, output, *args = sys.argv[1:]
    handlers = {"albuns": albums, "edicoes": editions, "musicas": songs, "faixas": tracks}
    if command not in handlers:
        raise SystemExit(f"Comando desconhecido: {command}")
    Path(output).write_text(json.dumps(handlers[command](*args), ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
