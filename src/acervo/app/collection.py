"""Proposals, protected fields, search, playlists and history."""

import json
import re
import secrets
from datetime import datetime, timezone

from acervo.app.common import readable_music
from acervo.library import protections
from acervo.library.core import (
    durable_copy,
    library_lock,
    sha256,
)
from acervo.library.playlists import album_order, write_m3u8
from acervo.library.playlists import render as render_m3u8
from acervo.library.query import album_key, track_version, version_matches
from acervo.messages import _
from acervo.search import LibraryIndex, normalize


class CollectionOperations:
    def read_protections(self):
        try:
            return protections.load(self.storage, self.root), None
        except ValueError as error:
            return None, str(error)

    def proposals(self, policy=None):
        tracks = {t["arquivo"]: t for t in self.tracks}
        if policy is None:
            policy, error = self.read_protections()
            if error:
                return []
        rows = []
        for entry in (self.plan or {}).get("arquivos", []):
            if entry["arquivo"] in self.incremental_paths:
                continue
            track = tracks[entry["arquivo"]]
            protected = protections.protected(policy, self.root, entry["arquivo"])
            for change in entry["alteracoes"]:
                if change["campo"] in protected:
                    continue
                rows.append(
                    {
                        **change,
                        "arquivo": entry["arquivo"],
                        "track_id": track["id"],
                        "titulo": " / ".join(track["titulo"]),
                        "artista": " / ".join(track["artista"]),
                        "album": " / ".join(track["album"]),
                        "tamanho_bytes": entry["tamanho_bytes"],
                    }
                )
        return rows

    def protection_info(self, album_id=""):
        policy, error = self.read_protections()
        if error:
            return {"campos": list(protections.FIELDS), "protecoes": [], "erro_protecoes": error}
        with self.lock:
            tracks = list(self.tracks)
            by_id = dict(self.by_id)
        by_file = {t["arquivo"]: t for t in tracks}
        rows = []
        for row in protections.listing(policy):
            track = by_file.get(row["arquivo"], {})
            rows.append(
                {**row, "id": track.get("id"), "titulo": track.get("titulo", []), "artista": track.get("artista", [])}
            )
        result = {"campos": list(protections.FIELDS), "protecoes": rows}
        if album_id:
            track = by_id.get(album_id)
            if track is None:
                raise ValueError(_("Album not found; refresh the library"))
            members = [t for t in tracks if album_key(t) == album_key(track)]
            result["album"] = {
                "id": album_id,
                "titulo": track["album"],
                "artista": track["artista_album"] or track["artista"],
                "faixas": len(members),
                "campos": {
                    f: sum(f in protections.protected(policy, self.root, t["arquivo"]) for t in members)
                    for f in protections.FIELDS
                },
            }
        return result

    def change_protections(self, data):
        if "arquivos" in data or "arquivo" in data:
            raise ValueError(_("Select tracks by their library IDs"))
        fields, enabled = data.get("campos"), data.get("proteger")
        if (
            not isinstance(enabled, bool)
            or not isinstance(fields, list)
            or not fields
            or any(not isinstance(f, str) or f not in protections.FIELDS for f in fields)
        ):
            raise ValueError(_("Choose valid fields and whether to protect or unlock them"))
        ids, album_id = data.get("ids", []), data.get("album_id", "")
        if (
            not isinstance(ids, list)
            or len(ids) > 10000
            or any(not isinstance(i, str) for i in ids)
            or not isinstance(album_id, str)
            or bool(ids) == bool(album_id)
        ):
            raise ValueError(_("Select tracks or an album from the library"))
        with self.lock:
            if self.occupied():
                raise ValueError(_("Wait for the current operation to finish"))
            if album_id:
                track = self.by_id.get(album_id)
                if track is None:
                    raise ValueError(_("Album not found; refresh the library"))
                chosen = [t for t in self.tracks if album_key(t) == album_key(track)]
            else:
                if len(ids) != len(set(ids)) or any(i not in self.by_id for i in ids):
                    raise ValueError(_("Invalid track selection; refresh the library"))
                chosen = [self.by_id[i] for i in ids]
            self.policy_busy = True
        try:
            operation = protections.protect if enabled else protections.unprotect
            return operation(self.storage, self.root, [t["arquivo"] for t in chosen], fields)
        finally:
            with self.lock:
                self.policy_busy = False
                self.pump_downloads()

    @staticmethod
    def search_query(needle, scope="biblioteca"):
        if not isinstance(needle, str) or len(needle) > 200 or any(ord(c) < 32 for c in needle):
            raise ValueError(_("Use up to 200 characters in the search"))
        if scope not in ("biblioteca", "global"):
            raise ValueError(_("Choose to search the library or the global catalog"))
        return needle.strip()

    def library_index(self, tracks=None):
        with self.lock:
            if tracks is not None and tracks is not self.tracks:
                return LibraryIndex(tracks)
            if self.search_tracks is not self.tracks:
                self.search_index = LibraryIndex(self.tracks)
                self.search_tracks = self.tracks
            return self.search_index

    def suggestions(self, needle, scope="biblioteca"):
        needle = self.search_query(needle, scope)
        if scope == "global":
            if len(normalize(needle)) < 2:
                return {"termos": [], "artistas": []}
            try:
                return self.global_search.suggest(needle)
            except (ValueError, OSError) as error:
                raise RuntimeError(str(error)) from error
        return self.library_index().suggest(needle)

    def library(self, needle="", kind="todas", tracks=None, artist="", album_id="", version="todas"):
        needle = self.search_query(needle)
        if kind not in ("todas", "sem-genero", "flac", "versoes"):
            raise ValueError(_("Invalid library filter"))
        if version not in ("todas", "estudio", "ao_vivo", "acustico", "demo-remix", "coletanea"):
            raise ValueError(_("Invalid version filter"))
        if not isinstance(artist, str) or len(artist) > 200 or not isinstance(album_id, str):
            raise ValueError(_("Invalid artist or album filter"))
        with self.lock:
            rows = self.tracks if tracks is None else tracks
            index = self.library_index(rows)
        if album_id:
            selected = next((t for t in rows if t["id"] == album_id), None)
            if selected is None:
                raise ValueError(_("Album not found; refresh the library"))
            rows = [t for t in rows if album_key(t) == album_key(selected)]
        if artist:
            rows = [t for t in rows if normalize(artist) in {normalize(a) for a in t["artista"] + t["artista_album"]}]
        if needle:
            by_id = {t["id"]: t for t in rows}
            rows = [by_id[r["id"]] for r in index.search(needle, len(index.tracks)) if r["id"] in by_id]
        if kind == "sem-genero":
            rows = [t for t in rows if "genero" in t["campos_ausentes"]]
        elif kind == "flac":
            rows = [t for t in rows if t["formato"] == "FLAC"]
        elif kind == "versoes":
            rows = [t for t in rows if set(track_version(t)["marcas"]) - {"estudio"}]
        if version != "todas":
            rows = [t for t in rows if version_matches(track_version(t), version)]
        return rows

    def playlist(self, data, write=False):
        with self.lock:
            if self.occupied():
                raise ValueError(_("Wait for the current operation to finish"))
            self.playlist_busy = True
            tracks, by_id = list(self.tracks), dict(self.by_id)
        try:
            with library_lock(self.root):
                return self._playlist(data, tracks, by_id, write)
        except BlockingIOError as error:
            raise ValueError(_("The library is busy with another write; wait and try again")) from error
        finally:
            with self.lock:
                self.playlist_busy = False
                self.pump_downloads()

    def _playlist(self, data, tracks, by_id, write):
        name = data.get("nome", "")
        if not isinstance(name, str):
            raise ValueError(_("Enter a playlist name"))
        name = name.strip()
        if name.casefold().endswith(".m3u8"):
            name = name[:-5].strip()
        if (
            not name
            or name in (".", "..")
            or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', name)
            or len(name.encode("utf-8")) > 240
        ):
            raise ValueError(_("Use a simple name without slashes, special characters or line breaks"))
        album_id = data.get("album_id", "")
        if not isinstance(album_id, str):
            raise ValueError(_("Invalid album"))
        rows = self.library(
            data.get("q", ""),
            data.get("filter", "todas"),
            tracks,
            data.get("artista", ""),
            album_id,
            data.get("versao", "todas"),
        )
        if album_id:
            track = by_id.get(album_id)
            if track is None:
                raise ValueError(_("Album not found; refresh the library"))
            rows = [t for t in rows if album_key(t) == album_key(track)]
        rows = sorted(rows, key=album_order)
        if not rows:
            raise ValueError(_("No songs selected for the playlist"))
        for track in rows:
            readable_music(self.root, track["arquivo"])
        folder = self.root / "Playlists"
        path = folder / (name + ".m3u8")
        if folder.resolve() != folder or path.is_symlink() or path.resolve() != path:
            raise ValueError(_("The Playlists folder and playlist cannot be symbolic links"))
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise ValueError(_("The existing playlist must be a regular file without hard links"))
        # Validate all paths before creating even the Playlists directory.
        render_m3u8(path, rows, name)
        exists = path.exists()
        previous = sha256(path) if exists else None
        preview = {
            "playlist": str(path),
            "faixas": len(rows),
            "existe": exists,
            "anterior_sha256": previous,
            "amostra": [{"titulo": t["titulo"], "artista": t["artista"]} for t in rows[:10]],
        }
        if not write:
            return preview
        overwrite = data.get("sobrescrever", False)
        if not isinstance(overwrite, bool):
            raise ValueError(_("Replacing a playlist requires an explicit choice"))
        if exists and not overwrite:
            raise ValueError(_("The playlist already exists. Choose another name or approve replacement"))
        if overwrite and (not exists or data.get("anterior_sha256") != previous):
            raise ValueError(_("The playlist changed since preview; review it again"))
        backup = None
        if overwrite:
            backup_folder = self.storage / "backups/playlists"
            if backup_folder.resolve() != backup_folder:
                raise ValueError(_("The playlist backup folder cannot be a symbolic link"))
            backup_folder.mkdir(parents=True, exist_ok=True)
            short_name = name.encode("utf-8")[:120].decode("utf-8", errors="ignore")
            backup = backup_folder / (
                datetime.now().strftime("%Y%m%d_%H%M%S_") + short_name + "_" + secrets.token_hex(8) + ".m3u8"
            )
            durable_copy(path, backup)
            if sha256(backup) != previous or sha256(path) != previous:
                raise ValueError(_("The playlist changed during backup; review it again"))
        return {**write_m3u8(path, rows, name, overwrite=overwrite), "backup": str(backup) if backup else None}

    def history(self):
        rows = []
        for path in (self.storage / "backups").glob("*/manifesto.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if manifest["biblioteca"] == str(self.root):
                    rows.append(
                        {
                            "id": path.parent.name,
                            "tipo": manifest.get("tipo", "metadados"),
                            "categoria": "letra" if str(manifest.get("lote", "")).startswith("letra-") else "",
                            "estado": manifest["estado"],
                            "data": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                            "arquivos": len(manifest["arquivos"]),
                            "erro": manifest.get("erro"),
                        }
                    )
            except (OSError, ValueError, KeyError):
                continue
        return sorted(rows, key=lambda r: r["data"], reverse=True)
