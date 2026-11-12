"""Reviewable manual track edits, missing artwork and recoverable removal."""

import secrets
import time
from pathlib import Path

from mutagen import MutagenError

from acervo.library import protections
from acervo.library.artwork import cover_summary
from acervo.library.core import TAG_FIELDS, atomic_json, metadata_values, secure_file, sha256
from acervo.library.importer import import_reviewed
from acervo.library.manual import remove_track
from acervo.library.query import album_key
from acervo.library.scan import read_track, signature
from acervo.messages import _, register

register(
    {
        "Select a track from the current library": "Selecione uma faixa da biblioteca atual",
        "The manual preview expired; review the track again": "A prévia manual expirou; confira a faixa novamente",
        "Choose valid metadata fields": "Escolha campos de metadados válidos",
        "No metadata changes to save": "Não há alterações de metadados para salvar",
        "This track has no album or artist; add its cover individually": "Esta faixa não tem álbum ou artista; adicione a capa individualmente",
        "The selected files already have artwork": "Os arquivos selecionados já têm capa",
        "Select up to 500 tracks for one cover": "Selecione até 500 faixas para uma capa",
    }
)


def live_track(path):
    try:
        return read_track(path)
    except MutagenError as error:
        raise ValueError(_("File not recognized as MP3/FLAC")) from error


class ManualOperations:
    def _manual_track(self, ident):
        with self.lock:
            if self.occupied():
                raise ValueError(_("Wait for the current operation to finish"))
            track = self.by_id.get(ident) if isinstance(ident, str) else None
            if track is None:
                raise ValueError(_("Select a track from the current library"))
            return dict(track)

    def manual_editor(self, ident):
        track = self._manual_track(ident)
        path = secure_file(self.root, track["arquivo"])
        before = signature(path)
        digest = sha256(path)
        live_track(path)
        fields = metadata_values(path)
        policy = protections.load(self.storage, self.root)
        if signature(path) != before:
            raise ValueError(_("The file changed since the preview; review it again"))
        return {
            "id": track["id"],
            "arquivo": str(path),
            "campos": fields,
            "anterior_sha256": digest,
            "campos_protegidos": sorted(protections.protected(policy, self.root, str(path))),
        }

    def _manual_preview(self, kind, operations, sample, **extra):
        token = secrets.token_hex(20)
        preview = {
            "kind": kind,
            "operations": operations,
            "created": time.monotonic(),
            "ids": [row["id"] for row in sample],
            "root": str(self.root),
        }
        with self.lock:
            if self.occupied():
                raise ValueError(_("Wait for the current operation to finish"))
            self.manual_previews[token] = preview
            while len(self.manual_previews) > 20:
                self.manual_previews.pop(next(iter(self.manual_previews)))
        return {
            "previa": token,
            "kind": kind,
            "count": len(operations),
            "amostra": sample[:20],
            "tamanho_bytes": sum(Path(op["arquivo"]).stat().st_size for op in operations),
            **extra,
        }

    def preview_manual_edit(self, data):
        if (
            set(data) != {"id", "campos", "anterior_sha256"}
            or not isinstance(data["campos"], dict)
            or not data["campos"]
            or set(data["campos"]) - TAG_FIELDS.keys()
        ):
            raise ValueError(_("Choose valid metadata fields"))
        editor = self.manual_editor(data["id"])
        if editor["anterior_sha256"] != data["anterior_sha256"]:
            raise ValueError(_("The file changed since the preview; review it again"))
        changes = {}
        for field, values in data["campos"].items():
            if (
                not isinstance(values, list)
                or len(values) > 50
                or any(not isinstance(v, str) or not v.strip() or len(v) > 4096 or "\x00" in v for v in values)
            ):
                raise ValueError(_("Choose valid metadata fields"))
            if values != editor["campos"][field]:
                changes[field] = {"atual": editor["campos"][field], "proposto": values}
        if not changes:
            raise ValueError(_("No metadata changes to save"))
        policy = protections.load(self.storage, self.root)
        protections.require_unprotected(policy, self.root, editor["arquivo"], changes)
        operation = {
            "acao": "metadados",
            "arquivo": editor["arquivo"],
            "antes_sha256": editor["anterior_sha256"],
            "alteracoes": changes,
        }
        sample = [{"id": data["id"], "arquivo": editor["arquivo"], "titulo": editor["campos"]["titulo"]}]
        return self._manual_preview(
            "edit", [operation], sample, alteracoes=[{"campo": field, **change} for field, change in changes.items()]
        )

    def preview_manual_cover(self, data):
        if (
            set(data) - {"id", "album", "image"}
            or not {"id", "image"} <= set(data)
            or not isinstance(data.get("album", False), bool)
        ):
            raise ValueError(_("Select a track from the current library"))
        # Decode before caching; only a bounded JPEG/PNG reaches the tag writer.
        image = cover_summary(data["image"])
        track = self._manual_track(data["id"])
        if data.get("album"):
            if not any(v.strip() for v in track["album"]) or not any(
                v.strip() for v in track["artista_album"] + track["artista"]
            ):
                raise ValueError(_("This track has no album or artist; add its cover individually"))
            with self.lock:
                tracks = [dict(t) for t in self.tracks if album_key(t) == album_key(track)]
        else:
            tracks = [track]
        if len(tracks) > 500:
            raise ValueError(_("Select up to 500 tracks for one cover"))
        operations, sample, skipped = [], [], 0
        for member in tracks:
            path = secure_file(self.root, member["arquivo"])
            before = signature(path)
            current = live_track(path)
            if data.get("album") and album_key(current) != album_key(track):
                raise ValueError(_("The file changed since the preview; review it again"))
            if current["capas"]:
                skipped += 1
                continue
            digest = sha256(path)
            if signature(path) != before:
                raise ValueError(_("The file changed since the preview; review it again"))
            operations.append(
                {
                    "acao": "metadados",
                    "arquivo": str(path),
                    "antes_sha256": digest,
                    "alteracoes": {"capa": {"atual": [], "proposto": [data["image"]]}},
                }
            )
            sample.append({"id": member["id"], "arquivo": str(path), "titulo": current["titulo"]})
        if not operations:
            raise ValueError(_("The selected files already have artwork"))
        return self._manual_preview("cover", operations, sample, cover=image, skipped=skipped)

    def preview_manual_delete(self, data):
        if set(data) != {"id"}:
            raise ValueError(_("Select a track from the current library"))
        track = self._manual_track(data["id"])
        path = secure_file(self.root, track["arquivo"])
        before = signature(path)
        digest = sha256(path)
        if signature(path) != before:
            raise ValueError(_("The file changed since the preview; review it again"))
        return self._manual_preview("delete", [{"arquivo": str(path), "antes_sha256": digest}], [track])

    def save_manual(self, data):
        if set(data) != {"previa"} or not isinstance(data["previa"], str):
            raise ValueError(_("The manual preview expired; review the track again"))
        with self.lock:
            preview = self.manual_previews.get(data["previa"])
            if not preview or time.monotonic() - preview["created"] > 900 or preview["root"] != str(self.root):
                raise ValueError(_("The manual preview expired; review the track again"))

        def save():
            with self.lock:
                self.manual_previews.pop(data["previa"], None)
            operations = preview["operations"]
            if preview["kind"] == "delete":
                row = operations[0]
                result = remove_track(self.root, self.storage, row["arquivo"], row["antes_sha256"])
                with self.lock:
                    self.tracks = [t for t in self.tracks if t["arquivo"] != row["arquivo"]]
                    self.by_id = {t["id"]: t for t in self.tracks}
                    self.incremental_paths.add(row["arquivo"])
                    self.library_revision = secrets.token_hex(8)
                    cached = sorted(p for p in self.incremental_paths if Path(p).is_file())
                atomic_json(self.download_cache, {"biblioteca": str(self.root), "arquivos": cached})
            else:
                result = import_reviewed(
                    self.root, self.storage / "backups", operations, "manual-" + preview["kind"], self.storage
                )
                result["avisos"] = self.refresh_downloaded(
                    [{"estado": "baixado", "arquivo": row["arquivo"]} for row in operations]
                )
            with self.lock:
                self.bulk_lyrics = None
                self.bulk_lyrics_hashes.clear()
                self.bulk_lyrics_previews.clear()
            return {**result, "manual_kind": preview["kind"], "manual_track_ids": preview["ids"]}

        self.start("import", save)
