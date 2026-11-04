"""Adapter run on spotDL's Python; queries the catalog without fetching audio/lyrics."""

import json
import sys
from pathlib import Path


def main():
    from spotdl.utils.search import get_simple_songs
    from spotdl.utils.spotify import SpotifyClient
    SpotifyClient.init(client_id="", client_secret="", headless=True, max_retries=1, use_official_api=False)
    songs = get_simple_songs([sys.argv[1]])
    if not songs or len(songs) > 200:
        raise ValueError("The catalog must contain between 1 and 200 tracks")
    Path(sys.argv[2]).write_text(json.dumps([s.json for s in songs], ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
