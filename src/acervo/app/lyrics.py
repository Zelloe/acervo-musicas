"""Reading, previewing and saving lyrics, for one track or in bulk."""

import secrets
import threading
import time

from acervo.app.common import readable_music
from acervo.library import protections
from acervo.library.core import (
    secure_file,
    sha256,
)
from acervo.lyrics import (
    bulk_lookup,
    current_lyrics,
    lyrics_for_track,
    parse_lrc,
    prepare_text,
    save_lyrics,
    save_lyrics_batch,
)
from acervo.messages import _


class LyricsOperations:
    def lyrics(self, ident):
        with self.lock:
            track = self.by_id.get(ident)
            if track is None:
                raise ValueError(_("Song not found"))
            path = readable_music(self.root, track["arquivo"])
            artist = " / ".join(track["artista"] or track["artista_album"])
            title = " / ".join(track["titulo"])
            album = " / ".join(track["album"])
            seconds = track["duracao_segundos"]
        return lyrics_for_track(path, artist, title, album, seconds, self.lyrics_client)

    def preview_lyrics(self, data):
        if set(data) != {"id", "texto"} or not isinstance(data.get("id"), str):
            raise ValueError(_("Select a track by its ID and review the lyrics"))
        text = data.get("texto")
        if not isinstance(text, str) or not text.strip() or len(text) > 100_000 or "\x00" in text:
            raise ValueError(_("Empty or invalid lyrics"))
        text = prepare_text(text)
        with self.lock:
            if self.occupied():
                raise ValueError(_("Wait for the current operation to finish"))
            protections.load(self.storage, self.root)
            track = self.by_id.get(data["id"])
            if track is None:
                raise ValueError(_("Song not found"))
            path = secure_file(self.root, track["arquivo"])
            before = sha256(path)
            current = current_lyrics(path)
            if current == [text]:
                raise ValueError(_("The track already has exactly these lyrics"))
            if sha256(path) != before:
                raise ValueError(_("The file changed during preview; review it again"))
            ident = secrets.token_hex(20)
            self.lyrics_previews[ident] = {
                "id": data["id"],
                "arquivo": str(path),
                "texto": text,
                "sha256": before,
                "criado": time.monotonic(),
            }
            while len(self.lyrics_previews) > 20:
                self.lyrics_previews.pop(next(iter(self.lyrics_previews)))
        return {
            "previa": ident,
            "titulo": track["titulo"],
            "artista": track["artista"],
            "atual": current,
            "texto": text,
            "arquivo": str(path),
            "format": "synced" if parse_lrc(text) else "plain",
        }

    def save_lyrics(self, data):
        if set(data) != {"previa"} or not isinstance(data.get("previa"), str):
            raise ValueError(_("Review the lyrics preview before saving"))
        with self.lock:
            draft = self.lyrics_previews.get(data["previa"])
            if draft is None or time.monotonic() - draft["criado"] > 900:
                raise ValueError(_("The preview expired; review the lyrics again"))

        def save():
            with self.lock:
                self.lyrics_previews.pop(data["previa"], None)
                track = self.by_id.get(draft["id"])
                if track is None or track["arquivo"] != draft["arquivo"]:
                    raise ValueError(_("The track changed in the inventory; review it again"))
            result = save_lyrics(self.root, self.storage, draft["arquivo"], draft["texto"], draft["sha256"])
            notices = self.refresh_downloaded([{"estado": "baixado", "arquivo": draft["arquivo"]}])
            return {**result, "letra_track_id": draft["id"], "avisos": notices}

        self.start("import", save)

    @property
    def bulk_lyrics(self):
        return self._bulk_lyrics

    @bulk_lyrics.setter
    def bulk_lyrics(self, value):
        # Every lookup change replaces this value, so pages can tell without loading the rows.
        self._bulk_lyrics = value
        self.bulk_lyrics_revision = secrets.token_hex(8)

    def bulk_lyrics_info(self, ident=None):
        with self.lock:
            current = self.bulk_lyrics
            if ident is not None and (not isinstance(ident, str) or not current or ident != current["id"]):
                raise ValueError(_("The lyrics lookup expired; start another lookup"))
            return {"lookup": current, "revisao_letras_lote": self.bulk_lyrics_revision}

    def start_bulk_lyrics(self, data):
        if data:
            raise ValueError(_("Start a lyrics lookup using the library inventory"))
        with self.lock:
            if self.occupied():
                raise ValueError(_("Wait for the current operation to finish"))
            tracks = [dict(track) for track in self.tracks]
            if not tracks or len(tracks) > 20_000:
                raise ValueError(_("Choose a library with 1 to 20,000 tracks for this lookup"))
            ident = secrets.token_hex(16)
            cancel = threading.Event()
            self.bulk_lyrics = {"id": ident, "rows": [], "complete": False, "cancelled": False}
            self.bulk_lyrics_hashes = {}
            self.bulk_lyrics_previews.clear()
            self.bulk_lyrics_cancel = cancel

            def lookup():
                hashes, failures, valid = {}, {}, []
                try:
                    for index, track in enumerate(tracks):
                        if cancel.is_set():
                            break
                        self.download_progress(
                            _("Checking tracks {current} of {total}", current=index + 1, total=len(tracks)),
                            round(index / len(tracks) * 10),
                        )
                        try:
                            path = readable_music(self.root, track["arquivo"])
                            hashes[track["id"]] = sha256(path)
                            valid.append(track)
                        except (OSError, ValueError) as error:
                            failures[track["id"]] = str(error)

                    def progress(message, percent):
                        self.download_progress(message, round(10 + (percent or 0) * 0.8))

                    found = {
                        row["id"]: row
                        for row in bulk_lookup(valid, self.lyrics_client, progress=progress, cancel=cancel)
                    }
                    rows = []
                    for index, track in enumerate(tracks):
                        base = {
                            "id": track["id"],
                            "file": track["arquivo"],
                            "title": list(track["titulo"]),
                            "artist": list(track["artista"]),
                            "album": list(track["album"]),
                            "text": "",
                            "current": "none",
                            "status": "not_checked",
                        }
                        row = {**base, **found.get(track["id"], {})}
                        if track["id"] in failures:
                            row.update(status="error", error=failures[track["id"]])
                        if row["status"] in ("synced", "plain"):
                            try:
                                path = secure_file(self.root, track["arquivo"])
                                if row["file"] != str(path) or sha256(path) != hashes.get(track["id"]):
                                    raise ValueError(
                                        _("The track changed during lookup; refresh the library and try again")
                                    )
                                row["text"] = prepare_text(row["text"])
                                row["status"] = "synced" if parse_lrc(row["text"]) else "plain"
                            except (OSError, ValueError) as error:
                                row.update(status="error", text="", error=str(error))
                        rows.append(row)
                        self.download_progress(
                            _("Checking lyrics results {current} of {total}", current=index + 1, total=len(tracks)),
                            round(90 + (index + 1) / len(tracks) * 10),
                        )
                    with self.lock:
                        self.bulk_lyrics_hashes = hashes
                        self.bulk_lyrics = {"id": ident, "rows": rows, "complete": True, "cancelled": cancel.is_set()}
                    return {
                        "bulk_lyrics_id": ident,
                        "cancelled": cancel.is_set(),
                        "ready": sum(row["status"] in ("synced", "plain") for row in rows),
                    }
                except Exception as error:
                    with self.lock:
                        self.bulk_lyrics = {
                            "id": ident,
                            "rows": [],
                            "complete": True,
                            "cancelled": cancel.is_set(),
                            "error": str(error),
                        }
                    raise

            self.start("lyrics_lookup", lookup)

    def cancel_bulk_lyrics(self, data):
        with self.lock:
            if (
                data
                or not self.job
                or self.job["type"] != "lyrics_lookup"
                or self.job["status"] != "running"
                or self.bulk_lyrics_cancel is None
            ):
                raise ValueError(_("No lyrics lookup is running"))
            self.bulk_lyrics_cancel.set()
        return {"cancellation_requested": True}

    def preview_bulk_lyrics(self, data):
        if set(data) != {"lookup", "ids"} or not isinstance(data["lookup"], str):
            raise ValueError(_("Select lyrics from the lookup results"))
        ids = data["ids"]
        if (
            not isinstance(ids, list)
            or not ids
            or len(ids) > 20_000
            or any(not isinstance(ident, str) for ident in ids)
            or len(ids) != len(set(ids))
        ):
            raise ValueError(_("Select lyrics from the lookup results"))
        with self.lock:
            if self.occupied():
                raise ValueError(_("Wait for the current operation to finish"))
            protections.load(self.storage, self.root)
            lookup = self.bulk_lyrics
            if not lookup or lookup["id"] != data["lookup"] or not lookup["complete"]:
                raise ValueError(_("The lyrics lookup expired; start another lookup"))
            by_id = {row["id"]: row for row in lookup["rows"]}
            if any(
                ident not in by_id or (by_id[ident]["status"] not in ("synced", "plain") or by_id[ident].get("saved"))
                for ident in ids
            ):
                raise ValueError(_("Select only lyrics ready to save"))
            chosen = [(dict(by_id[ident]), self.by_id.get(ident), self.bulk_lyrics_hashes.get(ident)) for ident in ids]
            self.lyrics_busy = True
        try:
            items, sample, skipped, size = [], [], [], 0
            for row, track, expected in chosen:
                if track is None or track["arquivo"] != row["file"]:
                    raise ValueError(_("The track changed in the inventory; review it again"))
                path = secure_file(self.root, row["file"])
                before = sha256(path)
                if not expected or before != expected:
                    raise ValueError(_("The track changed after lookup: {file}. Start another lookup", file=path.name))
                text = prepare_text(row["text"])
                current = current_lyrics(path)
                if sha256(path) != before:
                    raise ValueError(_("The file changed during preview; review it again"))
                if current == [text]:
                    skipped.append(row["id"])
                    continue
                items.append({"id": row["id"], "file": str(path), "text": text, "previous_sha256": before})
                size += path.stat().st_size
                if len(sample) < 12:
                    sample.append(
                        {"id": row["id"], "title": row["title"], "artist": row["artist"], "format": row["status"]}
                    )
            if not items:
                raise ValueError(_("The selected tracks already have exactly these lyrics"))
            ident = secrets.token_hex(20)
            with self.lock:
                self.bulk_lyrics_previews[ident] = {"items": items, "created": time.monotonic()}
                while len(self.bulk_lyrics_previews) > 5:
                    self.bulk_lyrics_previews.pop(next(iter(self.bulk_lyrics_previews)))
            return {
                "preview": ident,
                "count": len(items),
                "skipped": len(skipped),
                "size_bytes": size,
                "sample": sample,
            }
        finally:
            with self.lock:
                self.lyrics_busy = False
                self.pump_downloads()

    def save_bulk_lyrics(self, data):
        if set(data) != {"previa"} or not isinstance(data["previa"], str):
            raise ValueError(_("Review the lyrics preview before saving"))
        with self.lock:
            draft = self.bulk_lyrics_previews.get(data["previa"])
            if draft is None or time.monotonic() - draft["created"] > 900:
                raise ValueError(_("The preview expired; review the lyrics again"))

            def save():
                with self.lock:
                    self.bulk_lyrics_previews.pop(data["previa"], None)
                    for item in draft["items"]:
                        track = self.by_id.get(item["id"])
                        if track is None or track["arquivo"] != item["file"]:
                            raise ValueError(_("The track changed in the inventory; review it again"))
                result = save_lyrics_batch(
                    self.root,
                    self.storage,
                    [{key: value for key, value in item.items() if key != "id"} for item in draft["items"]],
                )
                skipped = set(result.get("skipped", []))
                changed = [item for item in draft["items"] if item["file"] not in skipped]
                notices = self.refresh_downloaded([{"estado": "baixado", "arquivo": item["file"]} for item in changed])
                saved_ids = {item["id"] for item in draft["items"]}
                with self.lock:
                    if self.bulk_lyrics:
                        self.bulk_lyrics = {
                            **self.bulk_lyrics,
                            "rows": [
                                {**row, "saved": True, "current": row["status"]} if row["id"] in saved_ids else row
                                for row in self.bulk_lyrics["rows"]
                            ],
                        }
                return {**result, "lyrics_track_ids": [item["id"] for item in changed], "warnings": notices}

            self.start("import", save)
