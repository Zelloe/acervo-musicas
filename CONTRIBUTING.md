# Contributing

**English** · [Português](CONTRIBUTING.pt-BR.md)

Thanks for your interest in Acervo. This guide covers what you need to know
before opening a contribution. Issues and pull requests are welcome in English
or Portuguese.

## Core principle

Acervo handles other people's music collections. Every change must keep these
guarantees:

- Nothing is written to the collection without a preview and explicit approval.
- Every write has a full, verified backup first, and restoring brings back the
  original file byte for byte.
- Nothing is deleted, renamed, moved or re-encoded automatically.
- Cover art, unapproved tags, legitimate versions (live, demo, remix, bonus) and
  the audio stream are preserved.
- Ambiguous identifications go to review, with their source recorded.
- Using Acervo does not require Spotify Premium or any paid service.

A contribution that weakens any of these guarantees will not be accepted, even
if it makes the program faster or simpler.

## Environment

Requirements: Python 3.10 or newer and `ffmpeg` (the tests use it to generate
temporary audio).

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

The interface is plain HTML, CSS and JavaScript, with no npm and no build step.
To check the scripts' syntax:

```bash
for f in web/*.js; do node --check "$f"; done
```

## Languages

Code, comments and the source text of every message are in English. Acervo
ships in English and Brazilian Portuguese:

- **Python:** write messages as `_("Text with {name}", name=value)` (from
  `messages.py`, never an f-string inside `_()`) and add the Portuguese to the
  module's `register({...})` catalogue. Library code always produces English;
  only entry points choose the language (the server per request, the command
  line from `LANG`).
- **Web interface:** user-facing strings go through `tr()` and need an entry in
  `web/i18n.js`. Titles, artists, albums, paths and lyrics are data and are
  never translated.
- **Stored data:** keys in files Acervo writes (manifests, plans, protections,
  batches) keep their existing names, many in Portuguese, so older backups stay
  restorable. `api_contract.py` maps them to the English HTTP API.

## Tests

- **Never use your real collection in tests.** Generate temporary files with
  ffmpeg, as the existing suite does in `tests/test_library.py`.
- Every change to writing, importing or restoring needs tests that show what
  was not approved is preserved and that the original can be restored.
- External services (MusicBrainz, LRCLIB, spotDL, yt-dlp) must be mocked in
  tests. CI does not reach the network to look up music.

## Code layout

| File | Responsibility |
| --- | --- |
| `acervo.py` | Command line: `analyze`, `apply`, `restore`, `serve`, `playlist`, `protect`, `unprotect`, `protections` |
| `library_scan.py` | Reading and inventory, never writes |
| `library_plan.py` | Rules, proposals and the approval plan |
| `library_core.py` | Validated writes, backups and restore |
| `library_import.py` | Transactional import and undo |
| `staged_downloads.py` | Advanced download flow (review area) |
| `downloader.py`, `download_menu.py`, `spotdl_search.py` | Direct spotDL downloads (core, terminal menu and adapter) |
| `download_picker.py` | Server side of the Baixar page |
| `search.py` | Library ranking and global suggestions |
| `versions.py` | Studio/live/acoustic/demo/remix classification |
| `lyrics.py` | Lyrics from the file and LRCLIB; saving with backup |
| `protections.py` | Protected fields |
| `playlists.py` | M3U8 playlists |
| `messages.py` | Message catalogue and language selection |
| `api_contract.py` | Maps stored data keys to the English HTTP API |
| `musicbrainz_client.py` | Optional MusicBrainz client, with cache and limits |
| `server.py` and `web/` | Local server and interface |

Planned features are listed in [ROADMAP.md](ROADMAP.md). Only mark
an item as done once it meets the criteria described there.

## Genre and album rules

`genre_rules.csv` and `album_rules.csv` are locally curated rules.
When proposing new rules:

- Prefer specific, useful genres (for example `Thrash Metal` rather than
  `Rock`).
- Use per-album rules when an artist's style changes, or when another band
  shares the same name.
- Record the source of the rule, and do not present a local rule as a confirmed
  identification of the recording.

## Pull requests

- One change per pull request, describing the problem and how it was tested.
- Do not include audio files, extracted cover art, reports, backups, cookies,
  credentials or paths from your machine.
- Short commit messages in the `type: description` style (`feat`, `fix`,
  `docs`, `test`, `refactor`).

## License

By contributing, you agree to license your contribution under the project's
[MIT license](LICENSE).
