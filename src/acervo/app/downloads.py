"""Download queue and the tracks that downloads add to the library."""

import hashlib
import json
import secrets
from pathlib import Path

from acervo.app.common import readable_music
from acervo.library.core import (
    atomic_json,
)
from acervo.library.scan import read_track, signature
from acervo.messages import _


class DownloadQueue:
    def queue_download(self, data):
        prepared = self.download_picker.download_options(data)
        catalog, rows = prepared[:2]
        entry = {
            "id": secrets.token_hex(8),
            "titulo": catalog["titulo"],
            "artista": catalog["artista"],
            "estado": "aguardando",
            "total": len(rows),
            "baixadas": 0,
            "erros": 0,
            "mensagem": "",
            "avisos": [],
            "preparado": prepared,
        }
        with self.lock:
            if self.closed:
                raise ValueError(_("Wait for the current operation to finish"))
            self.download_queue.append(entry)
            self.pump_downloads()
        return entry["id"]

    def remove_download(self, data):
        with self.lock:
            entry = next(
                (e for e in self.download_queue if e["id"] == data.get("id") and e["estado"] == "aguardando"), None
            )
            if entry is None:
                raise ValueError(_("Only waiting downloads can be removed"))
            self.download_queue.remove(entry)

    def pump_downloads(self):
        with self.lock:
            if self.job_unseen or self.occupied():
                return
            entry = next((e for e in self.download_queue if e["estado"] == "aguardando"), None)
            if entry is None:
                return
            entry["estado"] = "baixando"
            prepared = entry.pop("preparado")

            def operation():
                try:
                    result = self.download_picker.download_prepared(prepared)
                except Exception as error:
                    with self.lock:
                        entry.update(estado="erro", mensagem=str(error))
                    raise
                with self.lock:
                    states = [row.get("estado") for row in result["resultado"]]
                    entry.update(
                        estado="concluido",
                        baixadas=states.count("baixado"),
                        erros=states.count("erro") + states.count("nao_baixado"),
                        avisos=result.get("avisos") or [],
                    )
                return result

            try:
                self.start("direct_download", operation)
            except Exception as error:
                entry.update(estado="erro", mensagem=str(error))
            finished = [e for e in self.download_queue if e["estado"] in ("concluido", "erro")]
            for old in finished[:-20]:
                self.download_queue.remove(old)

    def download_queue_view(self):
        rows = [{k: v for k, v in entry.items() if k != "preparado"} for entry in self.download_queue]
        for row in rows:
            if row["estado"] == "baixando" and self.job:
                row["mensagem"] = self.job.get("message", "")
        return rows

    def loaded_is_current(self, track):
        try:
            return (
                bool(track)
                and track["arquivo_estavel_durante_leitura"]
                and signature(readable_music(self.root, track["arquivo"])) == track["assinatura_antes"]
            )
        except (KeyError, OSError, ValueError):
            return False

    def load_download_cache(self):
        try:
            data = json.loads(self.download_cache.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or data.get("biblioteca") != str(self.root)
                or not isinstance(data.get("arquivos"), list)
                or any(not isinstance(p, str) for p in data["arquivos"])
            ):
                raise ValueError(_("Incompatible cache of new tracks"))
            present = [p for p in data["arquivos"] if Path(p).is_file()]
            # Tracks already loaded and unchanged since their last read need no decoding.
            loaded = {t["arquivo"]: t for t in self.tracks}
            unchanged = [p for p in present if self.loaded_is_current(loaded.get(p))]
            self.incremental_paths.update(unchanged)
            stale = set(present) - set(unchanged)
            notices = self.refresh_downloaded(
                [{"estado": "baixado", "arquivo": p} for p in present if p in stale], persist=False
            )
            # A file moved after an earlier session is not a failed download: say so once and forget it.
            if len(present) != len(data["arquivos"]):
                notices.append(
                    _(
                        "{count} downloaded tracks were moved or removed; refresh the library to find them",
                        count=len(data["arquivos"]) - len(present),
                    )
                )
                atomic_json(self.download_cache, {"biblioteca": str(self.root), "arquivos": present})
            if notices:
                self.error = " ".join(notices)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as error:
            self.error = _("Could not load new tracks; refresh the library: {error}", error=error)

    def refresh_downloaded(self, results, persist=True):
        updates, paths, notices = {}, set(), []
        for row in results:
            if row.get("estado") != "baixado":
                continue
            path = Path(row.get("arquivo", ""))
            if not path.is_absolute() or not path.is_relative_to(self.root):
                continue
            paths.add(str(path))
            try:
                readable_music(self.root, str(path))
                track = read_track(path)
                if not track["arquivo_estavel_durante_leitura"]:
                    raise ValueError(_("The file changed while being read"))
                track["id"] = hashlib.sha256(str(path).encode()).hexdigest()[:24]
                updates[track["id"]] = track
            except Exception as error:
                notices.append(
                    _(
                        "Download saved, but the track was not added to the inventory: {file_name}. {error}",
                        file_name=path.name,
                        error=error,
                    )
                )
        if not paths:
            return notices
        with self.lock:
            self.incremental_paths.update(paths)
            self.tracks = [t for t in self.tracks if t["id"] not in updates] + list(updates.values())
            self.by_id.update(updates)
            self.library_revision = secrets.token_hex(8)
            cached = sorted(self.incremental_paths)
        if persist:
            try:
                atomic_json(self.download_cache, {"biblioteca": str(self.root), "arquivos": cached})
            except OSError as error:
                notices.append(_("Downloads saved; refresh the library after restarting: {error}", error=error))
        return notices
