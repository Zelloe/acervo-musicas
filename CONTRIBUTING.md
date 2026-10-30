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

The code, the interface and the command line are written in Portuguese
(identifiers, messages and option names). Please follow the surrounding style;
new user-facing text should be added in a way that can be translated.

## Tests

- **Never use your real collection in tests.** Generate temporary files with
  ffmpeg, as the existing suite does in `tests/test_biblioteca.py`.
- Every change to writing, importing or restoring needs tests that show what
  was not approved is preserved and that the original can be restored.
- External services (MusicBrainz, LRCLIB, spotDL, yt-dlp) must be mocked in
  tests. CI does not reach the network to look up music.

## Code layout

| File | Responsibility |
| --- | --- |
| `corrigir_tags.py` | Command line: `analisar`, `aplicar`, `restaurar`, `interface`, `playlist`, `proteger`, `desproteger`, `protecoes` |
| `diagnostico_biblioteca.py` | Reading and inventory, never writes |
| `biblioteca_plano.py` | Rules, proposals and the approval plan |
| `biblioteca_core.py` | Validated writes, backups and restore |
| `biblioteca_importacao.py` | Transactional import and undo |
| `biblioteca_downloads.py` | Advanced download flow (review area) |
| `baixador.py`, `baixar.py`, `spotdl_busca.py` | Direct spotDL downloads (core, terminal menu and adapter) |
| `seletor_downloads.py` | Server side of the Baixar page |
| `busca.py` | Library ranking and global suggestions |
| `versoes.py` | Studio/live/acoustic/demo/remix classification |
| `letras.py` | Lyrics from the file and LRCLIB; saving with backup |
| `protecoes.py` | Protected fields |
| `playlists.py` | M3U8 playlists |
| `musicbrainz_local.py` | Optional MusicBrainz client, with cache and limits |
| `servidor.py` and `web/` | Local server and interface |

Planned features are listed in [ROADMAP.md](ROADMAP.md) (Portuguese). Only mark
an item as done once it meets the criteria described there.

## Genre and album rules

`generos_artistas.csv` and `albuns_excecoes.csv` are locally curated rules.
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
