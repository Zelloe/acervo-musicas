"""Adapter run on spotDL's Python; queries the catalog without fetching audio/lyrics."""

import json
from pathlib import Path
import sys


def main():
    from spotdl.utils.spotify import SpotifyClient
    from spotdl.utils.search import get_simple_songs
    SpotifyClient.init(client_id="", client_secret="", headless=True, max_retries=1, use_official_api=False)
    songs = get_simple_songs([sys.argv[1]])
    if not songs or len(songs) > 200:
        raise ValueError("O catálogo deve conter entre 1 e 200 faixas")
    Path(sys.argv[2]).write_text(json.dumps([s.json for s in songs], ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
