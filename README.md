# Acervo

**English** · [Português](README.pt-BR.md)

A local music library manager. Acervo reads your MP3 and FLAC collection,
suggests metadata fixes (genre, album name and other fields), shows everything
in a browser interface and only writes what you approve, always with a full
backup and an exact restore.

Organisation lives **inside the files**, in the tags any player can read.
Acervo does not create genre or album folders, and it never renames, moves or
deletes your music.

> **Project status:** under active development. See the [ROADMAP](ROADMAP.md)
> (in Portuguese).
>
> The web interface is available in English and Portuguese (switch at the top;
> it follows your browser language by default). Some error messages from the
> server and the command line are still in Portuguese, and commands and options
> keep their Portuguese names (for example `--pasta` means "folder").

## What it does

- **Inventory:** reads MP3 (ID3v2.3/v2.4) and FLAC (Vorbis comments)
  recursively, with tags, cover art, technical details and hashes.
- **Genre suggestions:** from editable local rules per artist and per album
  (`genre_rules.csv`) and, optionally, MusicBrainz. Existing genres are
  kept by default.
- **Album unification:** conservative rules (`album_rules.csv`) that require
  matching artist, edition, disc, track number and title. The previous name is
  kept in its own tag. Live versions, demos and bonus tracks are never merged
  just because their names look alike.
- **Per-change approval:** every change shows the current and the suggested
  value, and only approved rows are written.
- **Backup and restore:** before writing, the whole original file is copied and
  verified. Restoring brings back the original byte for byte and refuses to
  overwrite later edits made by another program.
- **Local interface:** library with search and filters, playback, proposal
  review, albums, history and restore. It only listens on `127.0.0.1`.
- **Downloads:** pick an artist, album, edition and tracks and download them
  with spotDL straight into a folder, from the browser or the terminal.
- **Search:** in your library or in the global catalogue, with suggestions as
  you type, recording-type filters (studio, live, acoustic…), cover art and
  artist photos.
- **Lyrics:** shows lyrics (synced when available) and lets you save them into
  the file, with backup and undo.

## Installation

Requirements:

- Linux (tested on Arch/EndeavourOS and with the Ubuntu/Mint setup).
- Python 3.10 or newer with the `venv` module. On Ubuntu/Mint:
  `sudo apt install python3 python3-venv`.
- Optional, to download music: `ffmpeg` (`sudo apt install ffmpeg`) and
  [spotDL](https://github.com/spotDL/spotify-downloader) (`pipx install spotdl`).

```bash
git clone https://github.com/Zelloe/acervo-musicas.git
cd acervo-musicas
./start.sh
```

On first run, `start.sh` and `download.sh` create the `.venv` and install the
dependencies on their own (internet required). There are no JavaScript
dependencies and no build step.

Without `--pasta`, Acervo uses the system music folder, the same one your file
manager shows (for example `~/Music`, or `~/Música` on a Portuguese system). If
it does not exist, the program says so and asks for `--pasta`.

## Downloading music

Requires [spotDL](https://github.com/spotDL/spotify-downloader)
(`pipx install spotdl`) and `ffmpeg`. No Spotify developer account and no
Premium subscription are needed. There are two ways to use it, with the same
behaviour.

**In the browser:** open the interface (`./start.sh`) and go to **Baixar**
("Download"). Pick the Artist, Song or Link tab, check the destination folder
and tick the tracks. Missing tracks come pre-selected; the ones you already
have are left unticked. When the destination is inside the open library, new
songs show up in the Library right after the download, without a new analysis.

**In the terminal:**

```bash
./download.sh --pasta "$HOME/Music"
```

The menu offers three paths:

1. **Search an artist:** lists albums with their year; you pick the album, the
   edition (standard, deluxe…) when there is more than one, and the tracks.
2. **Search a song:** lists the versions found, each with its album.
3. **Paste a Spotify link:** to a song, album or playlist.

In the track list, songs you already have in the folder are marked
`[já tenho]` ("already have"). Press `enter` to download only the missing ones,
type `todas` ("all"), or numbers such as `1-3,5`.

Each track becomes `Artist - Title.mp3` directly in the chosen folder, with
title, artist, album, year, track number and cover art from spotDL. Since
spotDL without the official API does not provide genres, the genre is filled in
from `genre_rules.csv` when there is a single matching rule for the artist
or album. Lyrics are embedded when they are found.

spotDL works in a temporary folder outside your collection. The file only
appears in the destination after the download finishes successfully and the
audio passes an `ffmpeg` decode check. An existing file with the same name is
never replaced.

spotDL takes metadata from Spotify and finds the audio on YouTube; `320k` is
the output bitrate, not the quality of the source. Only download what you have
the right to download in your country.

## Search, filters and lyrics

**Search:** the top bar has a **Your library | Global** switch. In your library,
suggestions (artists, albums and songs) appear as you type, ignoring accents,
case and punctuation and tolerating small typos (`metalica`, `acdc`). Enter
lists the songs by relevance. In Global mode, suggestions come from spotDL's
catalogue, and choosing an artist opens the **Baixar** page; nothing is
downloaded until you choose.

**Recording filters:** the Library and Baixar show a label on every song or
album, plus **Studio, Live, Acoustic, Demo/Remix and Compilation** filters.
Classification only looks at title qualifiers (`- Live`, `(Acoustic Version)`)
and the album name (`Holy Live`, `Live in São Paulo`), so "Live and Learn" and
"To Live Is to Die" stay studio recordings. A live album whose name does not
say so shows up as studio.

**Lyrics:** the player's lyrics panel uses, in this order, lyrics already saved
in the file (or a `.lrc` file with the same name) and lyrics from
[LRCLIB](https://lrclib.net), an open database that needs no account. To query
LRCLIB, Acervo sends the artist, title, album and duration. Synced lyrics
highlight the current line. After checking (and, if you want, correcting) the
text, **Salvar letra no arquivo** ("save lyrics to file") writes the plain
lyrics with a full backup; History can undo it. Looked-up lyrics are cached
locally, outside your collection.

## Using the interface

```bash
./start.sh --pasta "/path/to/your/music"
```

The browser opens at <http://127.0.0.1:8765>. Keep the terminal open while you
use the interface; Ctrl+C stops the server. After updating Acervo, restart the
server (the page warns you when that is needed).

Without opening the browser automatically, or on another port:

```bash
.venv/bin/python acervo.py interface \
  --pasta "/path/to/your/music" \
  --porta 8765 \
  --dados "/path/to/acervo-data"
```

`--dados` ("data") sets where reports, backups and review downloads are kept.
It must be outside the collection. The default is the program folder.

Opening the interface, playing music and running analyses **does not modify**
the collection. The first analysis reads every file and computes hashes, which
can take a few minutes on large collections.

## Command line

1. Create a preview (writes nothing):

   ```bash
   .venv/bin/python acervo.py analisar \
     --pasta "/path/to/your/music" \
     --normalizar-albuns \
     --saida relatorios/my_preview
   ```

2. Open `relatorios/my_preview/alteracoes.csv` and set the `aprovar`
   ("approve") column to `sim` ("yes") on the rows you want. Do not edit other
   columns: the plan is checked before it is applied.

3. Apply:

   ```bash
   .venv/bin/python acervo.py aplicar \
     --plano relatorios/my_preview/plano.json \
     --aprovacoes relatorios/my_preview/alteracoes.csv
   ```

4. Undo if needed, with the manifest printed when applying:

   ```bash
   .venv/bin/python acervo.py restaurar \
     --manifesto backups/RUN_ID/manifesto.json
   ```

Analysis options:

| Option | Effect |
| --- | --- |
| `--generos FILE` | Use another genre rules CSV |
| `--albuns FILE` | Use another album rules CSV |
| `--musicbrainz --contato "email or URL"` | Query MusicBrainz for tracks without a local rule |
| `--max-consultas N` | MusicBrainz query limit (default 200) |

### Protected fields

A protected field is never suggested or written by Acervo, even when it is
empty and even if a rule, MusicBrainz or a download proposes another value:

```bash
.venv/bin/python acervo.py proteger \
  --pasta "/path/to/your/music" \
  --arquivo "/path/to/your/music/Artist/track.mp3" \
  --campos genero,album
```

Accepted fields: `titulo` (title), `artista` (artist), `artista_album` (album
artist), `album`, `genero` (genre), `ano` (year), `faixa` (track) and `disco`
(disc). Use `desproteger` with the same arguments to unlock, and
`protecoes --pasta ...` to list. In the interface, protect fields from the
track details or for a whole album.

Protection is checked again at write time. A plan or import prepared before
the protection is refused if it tries to change the field. A restore that would
change a protected field back is also refused until the field is unlocked,
because restoring brings back the whole original file.

Protections live in `<dados>/.biblioteca/protecoes/`, one file per collection,
keyed by the track path relative to the collection folder. If another program
moves or renames a track, the protection does not follow it. If that file is
corrupted or belongs to another collection, Acervo refuses every write instead
of ignoring the protections.

### M3U8 playlists

In the interface, use **Exportar M3U8** in the Library (exports every track of
the current search and filter, including other pages) or on an album card. A
preview shows the destination and the first tracks before saving. Playlists go
to the collection's `Playlists/` folder, the only place where the interface
creates a new file inside the collection; songs are not modified.

Replacing an existing playlist must be ticked in the preview. Before the
replacement, the previous version is copied and verified in
`backups/playlists/`, outside the collection, and that path is shown in the
confirmation.

From the command line, exporting only reads the collection:

```bash
.venv/bin/python acervo.py playlist \
  --pasta "/path/to/your/music" \
  --genero "Progressive Rock" \
  --nome "Progressive" \
  --saida "/path/to/your/music/Playlists/Progressive.m3u8"
```

`--genero`, `--artista` and `--album` match the whole value, case-insensitive.
Without filters the playlist contains the whole collection, ordered by album
artist, album, disc and track. Files are UTF-8 without BOM, with paths relative
to the playlist folder, so they keep working when the collection and its
playlists are copied together to another disk. An existing playlist is not
replaced without `--sobrescrever` ("overwrite"). To check that every path still
resolves:

```bash
.venv/bin/python acervo.py playlist --validar --saida Progressive.m3u8
```

## How writes are protected

Each file is edited in a temporary copy. Before replacing the original at the
same path, Acervo checks:

- that the compressed audio stream is identical (no re-encoding);
- that cover art, unapproved tags, unknown frames and FLAC blocks are still
  there;
- that the ID3v2 version and any existing ID3v1 tag were kept;
- that the written tags are exactly the approved ones.

Applying refuses files that changed since the preview. Files that are symlinks
or have hard links are left for manual review. An interrupted run can be
restored from its manifest. Backups take as much space as the changed files and
are never deleted automatically.

## MusicBrainz

Lookups are optional and off by default. Only artist and album names are sent,
never audio. A real contact is required to identify the client, as the
[usage policy](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting) asks;
the minimum interval between requests is respected and results are cached for
30 days. A genre is only suggested when the album itself is matched, so a band
that merely shares a name never lends its genre.

Names, tags and duration are partial evidence. Acervo does not yet identify
recordings by audio fingerprint.

## Advanced downloads

The **Baixar** menu uses the direct flow described in
[Downloading music](#downloading-music). If you prefer to review before any
file enters the collection, an advanced flow lives under **Preferências →
Downloads avançados** (Preferences → Advanced downloads):

1. Enter a link or search and see the source catalogue.
2. Compare with local files and choose tracks.
3. Download into a review area outside the collection.
4. Listen, review tags and choose per track: keep the collection, apply only
   the tags, add as a new version, or replace a local MP3 (with backup).

Acervo refuses to replace FLAC with MP3.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests generate small temporary files with `ffmpeg`, mock every network service
and never touch a real collection.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [ROADMAP](ROADMAP.md).

## License

[MIT](LICENSE).
