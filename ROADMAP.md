# Acervo roadmap

**English** · [Português](ROADMAP.pt-BR.md)

The 15 features below are part of the project's planned scope.
The checkboxes indicate that each item has been fully delivered and validated;
existing partial support should be reused and extended.

**Current priority (2026-10-29):** after completing items 6 and 13, pause new
items on this roadmap. The user prioritised a simple music picker using spotDL,
in the terminal and browser, saving directly to the chosen folder.
Metadata management for existing files remains available.

## Rules for every stage

- Keep the library at its current paths, without creating genre or album folders.
- Store music information in file metadata, compatible with music players.
  Filters, preferences and tasks may use the app's local database.
- Show a preview and require approval before modifying the collection.
- Create a verifiable backup and allow changes to be restored.
- Preserve cover art, valid tags, distinct tracks and legitimate versions.
- Never delete, rename or re-encode files automatically. Downloads and
  replacements must be chosen explicitly.
- Send ambiguous identifications for review and record their sources.
- Keep paths configurable and allow use without Spotify Premium.

## Features

- [ ] **1. Incomplete albums**
  Compare the collection with the track list of the chosen edition, showing
  present tracks, missing tracks and uncertain matches. Allow users to select
  and download only missing tracks, without replacing existing files by default.

- [ ] **2. Edition comparison**
  Compare original, remastered, deluxe and live editions by discs, tracks,
  duration and available identifiers. Show extra tracks and differences before
  merging metadata, preserving the identity of both the edition and recording.
  Sharing a Release Group is not enough to treat recordings as identical.

- [ ] **3. Audio identification**
  Use acoustic fingerprints as additional evidence for tracks with incorrect
  names or tags. Show candidates, sources and confidence; ambiguous results
  require review. Indicate when a query sends the fingerprint to an external
  service.

- [ ] **4. Possible duplicates**
  Distinguish identical files by hash, possible encodings of the same recording
  and alternative versions. Offer comparison and playback, without deleting
  automatically or inferring duplication from a title or album alone.

- [ ] **5. Information provenance**
  Record each field's current value, suggestion, source, query date and
  identification evidence. Distinguish local rules, manual choices and external
  data; explain confidence without inventing numerical precision.

- [x] **6. Protected fields**
  Allow users to protect fields for a track or a set of tracks, such as genre
  and album edition. Persist the choice and respect it during analysis,
  enrichment and replacement until the user unlocks the field.
  Available in the CLI, track details and batch actions for an album's current
  tracks. The local policy is separate for each library and revalidated on write
  or restore, including for old plans. Empty fields can also be protected.
  Files are not automatically reassociated when their paths change.
  Python/HTTP tests and Chromium checks on desktop and mobile validate
  persistence, unlocking and library reading even with an unreadable policy.

- [ ] **7. Custom genres**
  Offer rule editing by artist, album and track, with multiple genres. Document
  rule precedence, respect protected fields and validate TCON and GENRE reading
  in the players considered compatible.

- [ ] **8. Smart collections**
  Save combined filters for genre, period, artist, format and pending tasks,
  including incomplete albums and tracks without cover art. Update results as
  the library changes, without moving files.

- [ ] **9. Persistent download queue**
  Save tasks and their states to recover the queue after closing the program.
  Allow pausing, cancelling and retrying, with clear errors. When the tool cannot
  resume transferred bytes, indicate that the attempt will restart.
  Keep downloads outside the collection until import is approved.

- [ ] **10. Comparison before replacement**
  Allow users to listen to the current file and its candidate, comparing
  duration, format, bitrate, sample rate, channels, tags and cover art.
  Do not present higher bitrate as proof of better quality. Require an explicit
  choice and a backup before replacing a file, preserving originals until
  approval.

- [ ] **11. Visual history**
  Show changes by run, track and field, with previous and new values. Allow
  undoing a track or batch after checking integrity and possible later edits;
  also record imports and replacements.

- [ ] **12. Discovery mode**
  Explore rarely played artists, forgotten albums and genres of interest.
  Explain the reason for each suggestion. Use playback history only when
  available and enabled, without confusing missing data with infrequent listening.

- [x] **13. Portable playlists**
  Export selections and collections as M3U8 with paths relative to the playlist,
  UTF-8 encoding and accented names. Validate path resolution after moving the
  playlist together with its corresponding collection.
  Available through the CLI and the **Export M3U8** button in Library/Albums.
  The interface shows a preview, saves to `Playlists/` and requires confirmation
  before replacing a playlist, first keeping a verifiable copy outside the
  collection. Validated with accented names, multiple pages, albums sharing
  names, copying to another drive and navigation on desktop and mobile.
  Restoring the previous playlist is manual, using the backup path shown after
  replacement.

- [ ] **14. Extension system**
  Define documented interfaces for metadata sources, download tools and rules.
  Allow optional extensions without changing the core, keeping approval,
  protected fields, validation and backup in the core. Indicate that extensions
  execute code and must come from trusted sources.

- [ ] **15. Demo mode**
  Provide a fictional library with original or appropriately licensed audio,
  credits and examples of incomplete metadata, editions and duplicates.
  Allow users to test the interface and contribute without accessing their
  personal collection, with isolated demo data and backups and an option to
  reset the demonstration.

## Proposed delivery order

| Stage | Items | Expected outcome |
| --- | --- | --- |
| 1 — Decisions and traceability | 5, 6, 7 and 11 | Understand suggestions, preserve choices and review or undo changes. |
| 2 — Identification and albums | 3, 4, 2 and 1 | Compare recordings and editions to identify present and missing tracks. |
| 3 — Downloads and portability | 9, 10 and 13 | Recover tasks, review replacements and export playlists. |
| 4 — Exploration and contributions | 8, 12, 14 and 15 | Explore the collection and support extensions and public demonstrations. |

This order guides priorities without setting dates. Independent features may
be delivered earlier. Edition comparison must precede the final classification
of missing tracks; review and backup must be available before any replacement.

## Completion criteria

Mark an item complete only after checking its behaviour, documenting its use
and limitations, and validating relevant cases in a test library. Workflows
that write files must demonstrate preservation of data not approved for change
and restoration of the originals. Demo mode must not include personal files,
credentials or reports.
