"""The open library: its tracks, state and running jobs."""

import hashlib
import json
import secrets
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

from acervo.app.collection import CollectionOperations
from acervo.app.common import readable_music
from acervo.app.downloads import DownloadQueue
from acervo.app.lyrics import LyricsOperations
from acervo.downloads.picker import DownloadPicker
from acervo.downloads.staged import Downloads
from acervo.library import protections
from acervo.library.core import (
    load_plan,
)
from acervo.library.plan import create_plan
from acervo.library.scan import read_track, signature
from acervo.lyrics import (
    LyricsClient,
)
from acervo.messages import _, current_language, language
from acervo.paths import DATA, PACKAGE
from acervo.search import GlobalSearch

WRITING_JOBS = {"import", "apply", "restore"}

FINISHED_JOB_SECONDS = 5


def code_fingerprint():
    return sorted((str(path), (info := path.stat()).st_mtime_ns, info.st_size) for path in PACKAGE.rglob("*.py"))


LOADED_CODE = code_fingerprint()


def restart_required():
    try:
        return code_fingerprint() != LOADED_CODE
    except OSError:
        return True


class App(DownloadQueue, LyricsOperations, CollectionOperations):
    def __init__(self, root: Path, storage: Path | None = None):
        self.root = root.expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError(_("The library must be a folder"))
        self.storage = (storage or DATA).expanduser().resolve()
        if self.storage == self.root or self.root in self.storage.parents:
            raise ValueError(_("Application data must stay outside the music library"))
        self.state_dir = self.storage / ".biblioteca"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.download_queue = []
        self.job_unseen = False
        self.fixed_folder = False
        self.closed = False
        self.token = secrets.token_urlsafe(32)
        self.job = None
        self.worker = None
        self.playlist_busy = False
        self.policy_busy = False
        self.lyrics_busy = False
        self.library_revision = secrets.token_hex(8)
        self.incremental_paths = set()
        root_key = hashlib.sha256(str(self.root).encode()).hexdigest()[:16]
        self.download_cache = self.state_dir / ("adicoes_" + root_key + ".json")
        self.tracks = []
        self.by_id = {}
        self.search_tracks = None
        self.search_index = None
        self.global_search = GlobalSearch()
        self.lyrics_client = LyricsClient(self.state_dir / "letras")
        self.lyrics_previews = {}
        self.bulk_lyrics = None
        self.bulk_lyrics_hashes = {}
        self.bulk_lyrics_previews = {}
        self.bulk_lyrics_cancel = None
        self.plan = None
        self.plan_path = None
        self.error = None
        self.downloads = Downloads(self.root, self.storage, self.download_progress)
        self.download_picker = DownloadPicker(
            self.root, self.download_progress, self.inventory_snapshot, self.refresh_downloaded
        )
        for path in sorted(
            (self.storage / "relatorios").glob("*/plano.json"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            try:
                plan = load_plan(path)
                if plan["biblioteca"] == str(self.root):
                    self.load(path)
                    break
            except (OSError, ValueError, KeyError):
                continue
        self.load_download_cache()

    def load(self, path: Path):
        plan = load_plan(path)
        tracks = json.loads((path.parent / "inventario.json").read_text(encoding="utf-8"))["musicas"]
        root = Path(plan["biblioteca"])
        refreshed, changed, notices = [], set(), []
        # Saved inventories may predate edits made with a different library root.
        # Refresh changed files and withhold their old write proposals until a scan.
        for track in tracks:
            file = track["arquivo"]
            try:
                music = readable_music(root, file)
                if signature(music) != track.get("assinatura_antes"):
                    changed.add(file)
                    current = read_track(music)
                    if (
                        not current["arquivo_estavel_durante_leitura"]
                        or signature(music) != current["assinatura_antes"]
                    ):
                        raise ValueError(_("The file changed while being read"))
                    track = {**current, "id": track["id"]}
                refreshed.append(track)
            except Exception as error:
                changed.add(file)
                notices.append(
                    _(
                        "Could not refresh track {file_name}; scan the library again: {error}",
                        file_name=Path(file).name,
                        error=error,
                    )
                )
        with self.lock:
            self.plan = plan
            self.plan_path = path
            self.tracks = refreshed
            self.by_id = {t["id"]: t for t in refreshed}
            self.root = root
            self.bulk_lyrics = None
            self.bulk_lyrics_hashes.clear()
            self.bulk_lyrics_previews.clear()
            self.library_revision = secrets.token_hex(8)
            self.incremental_paths = changed
            self.error = " ".join(notices) if notices else None

    def start(self, kind, operation):
        with self.lock:
            if self.occupied():
                raise ValueError(_("Wait for the current operation to finish"))
            if kind in WRITING_JOBS or kind == "direct_download":
                protections.load(self.storage, self.root)
            self.error = None
            self.job = {
                "id": secrets.token_hex(8),
                "type": kind,
                "status": "running",
                "message": _("Preparing…"),
                "progress": None,
            }
        job_language = current_language()

        def run():
            with language(job_language):
                try:
                    result = operation()
                    with self.lock:
                        self.job.update(
                            status="completed", message=_("Operation completed"), progress=100, result=result
                        )
                        self.finish_job()
                except Exception as error:
                    with self.lock:
                        self.error = str(error)
                        self.job.update(status="error", message=str(error), progress=None)
                        self.finish_job()

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def occupied(self):
        return (
            self.closed
            or self.playlist_busy
            or self.policy_busy
            or self.lyrics_busy
            or bool(self.job and self.job["status"] == "running")
        )

    def finish_job(self):
        # The interface reacts to a finished job, so the next batch must not replace it unseen.
        with self.lock:
            if not any(e["estado"] == "aguardando" for e in self.download_queue):
                return
            self.job_unseen = True
            finished = self.job["id"]

        def expire():
            with self.lock:
                if self.job and self.job["id"] == finished:
                    self.job_unseen = False
            self.pump_downloads()

        timer = threading.Timer(FINISHED_JOB_SECONDS, expire)
        timer.daemon = True
        timer.start()

    def writing(self):
        """Whether a job is writing to the collection and must finish before exit."""
        with self.lock:
            job = self.job
        return bool(job and job["status"] == "running" and job["type"] in WRITING_JOBS and self.worker)

    def download_progress(self, message, percent):
        with self.lock:
            if self.job:
                self.job.update(message=message, progress=percent)

    def analyze(self, online=None):
        output = self.storage / "relatorios" / datetime.now().strftime("previa_%Y%m%d_%H%M%S_%f")

        def progress(done, total, name):
            with self.lock:
                self.job.update(
                    message=_("{done} of {total} · {name}", done=done, total=total, name=name),
                    progress=round(done / total * 100) if total else 0,
                )

        result = create_plan(
            self.root, output, normalize_albums=True, online=online, progress=progress, storage=self.storage
        )
        self.load(Path(result["plano"]))
        try:
            self.download_cache.unlink(missing_ok=True)
        except OSError as error:
            result["avisos"] = [_("Could not clear the new-track cache: {error}", error=error)]
        return result

    def inventory_snapshot(self):
        with self.lock:
            return list(self.tracks)

    def snapshot(self):
        with self.lock:
            self.pump_downloads()
            self.job_unseen = False
            policy, policy_error = self.read_protections()
            proposals = self.proposals(policy) if policy is not None else []
            summary = {
                **(self.plan or {}).get("resumo", {}),
                "alteracoes_propostas": len(proposals),
                "lidos": len(self.tracks),
                "formatos": dict(Counter(t["formato"] for t in self.tracks)),
                "campos_ausentes": {f: sum(f in t["campos_ausentes"] for t in self.tracks) for f in protections.FIELDS},
                "com_capa": sum(bool(t["capas"]) for t in self.tracks),
                "arquivos_com_propostas": len({p["arquivo"] for p in proposals}),
                "propostas_por_campo": dict(Counter(p["campo"] for p in proposals)),
            }
            revision = (
                hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest() if policy is not None else None
            )
            return {
                "biblioteca": str(self.root),
                "biblioteca_revisao": self.library_revision,
                "analisado_em": (self.plan or {}).get("criado_em"),
                "resumo": summary,
                "job": self.job,
                "pasta_fixa": self.fixed_folder,
                "revisao_letras_lote": self.bulk_lyrics_revision,
                "fila_download": self.download_queue_view(),
                "erro": self.error,
                "erro_protecoes": policy_error,
                "protecoes_revisao": revision,
                "total_albuns": len(
                    {(tuple(t["artista_album"] or t["artista"]), tuple(t["album"])) for t in self.tracks}
                ),
                "total_artistas": len({tuple(t["artista_album"] or t["artista"]) for t in self.tracks}),
                "duracao_segundos": sum(t["duracao_segundos"] for t in self.tracks),
                "propostas": len(proposals),
                "lyrics_tools": True,
                "lyrics_busy": self.lyrics_busy,
                "bulk_lyrics_id": self.bulk_lyrics["id"] if self.bulk_lyrics else None,
                "reinicio_necessario": restart_required(),
            }
