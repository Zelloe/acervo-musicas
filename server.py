"""Local Acervo interface, listening exclusively on the loopback address."""

from messages import _, current_language, language, normalize_language, register

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

from api_contract import from_api, to_api

from mutagen.flac import FLAC
from mutagen.id3 import ID3

from library_core import apply_plan, atomic_json, durable_copy, library_lock, load_plan, restore, secure_file, sha256
from staged_downloads import Downloads, clean_log, tool_status
from library_plan import BASE, create_plan
from library_scan import read_track
from musicbrainz_client import MusicBrainz
from lyrics import LyricsClient, save_lyrics, current_lyrics, lyrics_for_track, parse_lrc
from search import GlobalSearch, LibraryIndex, normalize
from playlists import album_order, render as render_m3u8, write_m3u8
from download_picker import DownloadPicker
import protections
from versions import classify


register({
    "Library: {folder}": "Biblioteca: {folder}",
    "Invalid or symbolic music path": "Caminho de música inválido ou simbólico",
    "Missing or invalid file; refresh the library": "Arquivo ausente ou inválido; atualize a biblioteca",
    "The library must be a folder": "A biblioteca deve ser uma pasta",
    "Application data must stay outside the music library": "Os dados do programa devem ficar fora da biblioteca musical",
    "Preparing…": "Preparando…",
    "Select a track by its ID and review the lyrics": "Selecione uma faixa pelo identificador e confira a letra",
    "Empty or invalid lyrics": "Letra vazia ou inválida",
    "Use plain lyrics without timestamps": "Use a letra simples, sem marcações de tempo",
    "Review the lyrics preview before saving": "Confira a prévia da letra antes de salvar",
    "Could not load new tracks; refresh the library: {error}": "Não foi possível carregar novas faixas; atualize a biblioteca: {error}",
    "Select tracks by their library IDs": "Selecione faixas pelos identificadores da biblioteca",
    "Choose valid fields and whether to protect or unlock them": "Escolha campos válidos e se deseja protegê-los ou desbloqueá-los",
    "Select tracks or an album from the library": "Selecione faixas ou um álbum da biblioteca",
    "Use up to 200 characters in the search": "Use até 200 caracteres na busca",
    "Choose to search the library or the global catalog": "Escolha buscar na biblioteca ou no catálogo global",
    "Invalid library filter": "Filtro de biblioteca inválido",
    "Invalid version filter": "Filtro de versão inválido",
    "Invalid artist or album filter": "Filtro de artista ou álbum inválido",
    "Enter a playlist name": "Informe um nome para a playlist",
    "Use a simple name without slashes, special characters or line breaks": "Use um nome simples, sem barras, caracteres especiais ou quebras de linha",
    "Invalid album": "Álbum inválido",
    "No songs selected for the playlist": "Nenhuma música selecionada para a playlist",
    "The Playlists folder and playlist cannot be symbolic links": "A pasta Playlists e a playlist não podem ser links simbólicos",
    "The existing playlist must be a regular file without hard links": "A playlist existente deve ser um arquivo comum, sem hard links",
    "Replacing a playlist requires an explicit choice": "A substituição da playlist exige uma escolha explícita",
    "The playlist already exists. Choose another name or approve replacement": "A playlist já existe. Escolha outro nome ou aprove a substituição",
    "The playlist changed since preview; review it again": "A playlist mudou desde a prévia; revise novamente",
    "\nInterface closed.": "\nInterface encerrada.",
    "Wait for the current operation to finish": "Aguarde a operação atual terminar",
    "Could not clear the new-track cache: {error}": "Não foi possível limpar o cache de novas faixas: {error}",
    "Song not found": "Música não encontrada",
    "The track already has exactly these lyrics": "A faixa já tem exatamente essa letra",
    "The file changed during preview; review it again": "O arquivo mudou durante a prévia; confira novamente",
    "The preview expired; review the lyrics again": "A prévia expirou; confira a letra novamente",
    "Incompatible cache of new tracks": "Cache de novas faixas incompatível",
    "Album not found; refresh the library": "Álbum não encontrado; atualize a biblioteca",
    "The library is busy with another write; wait and try again": "A biblioteca está ocupada por outra gravação; aguarde e tente novamente",
    "The playlist backup folder cannot be a symbolic link": "A pasta de backup de playlists não pode ser simbólica",
    "The playlist changed during backup; review it again": "A playlist mudou durante o backup; revise novamente",
    "Host not allowed": "Host não permitido",
    "Unauthorized request": "Requisição não autorizada",
    "Invalid request": "Requisição inválida",
    "The request body must be a JSON object": "O corpo da requisição deve ser um objeto JSON",
    "\nWaiting for the current write to finish so files are not left incomplete…": "\nAguardando a gravação em andamento terminar para não deixar arquivos pela metade…",
    "{done} of {total} · {name}": "{done} de {total} · {name}",
    "The track changed in the inventory; review it again": "A faixa mudou no inventário; confira novamente",
    "The file changed while being read": "O arquivo mudou durante a leitura",
    "Download saved, but the track was not added to the inventory: {file_name}. {error}": "Download salvo, mas a faixa não entrou no inventário: {file_name}. {error}",
    "Downloads saved; refresh the library after restarting: {error}": "Downloads salvos; atualize a biblioteca após reiniciar: {error}",
    "Invalid track selection; refresh the library": "Seleção de faixas inválida; atualize a biblioteca",
    "Asset not found": "Recurso não encontrado",
    "Operation completed": "Operação concluída",
    "Select a track by its library ID": "Selecione uma música pelo identificador da biblioteca",
    "Track not found": "Faixa não encontrada",
    "No log available.": "Sem registro disponível.",
    "Invalid selection": "Seleção inválida",
    "No lookup or download is running": "Não há consulta ou download em andamento",
    "Creating backups and validating approved files…": "Criando backups e validando os arquivos aprovados…",
    "Creating full backups and validating tags…": "Criando backups completos e validando as tags…",
    "Select changes to approve": "Selecione alterações para aprovar",
    "Scan the library first": "Analise a biblioteca primeiro",
    "The selection is outdated; reload the page": "A seleção está desatualizada; recarregue a página",
    "Validating backups and restoring files…": "Validando backups e restaurando os arquivos…",
    "Operation not found": "Execução não encontrada",
    "Route not found": "Rota não encontrada",
    "Page not found": "Página não encontrada",
    "Run a scan first": "Execute uma análise primeiro"
})


WRITING_JOBS = {"import", "apply", "restore"}
SERVER_SOURCE = Path(__file__).resolve()
SERVER_CODE_HASH = sha256(SERVER_SOURCE)


def restart_required():
    try:
        return sha256(SERVER_SOURCE) != SERVER_CODE_HASH
    except OSError:
        return True


def album_key(track):
    return (tuple(track["artista_album"] or track["artista"]), tuple(track["album"]))


def track_version(track):
    return classify(" / ".join(track.get("titulo", [])), " / ".join(track.get("album", [])))


def version_matches(version, choice):
    marks = set(version["marcas"])
    if choice == "todas":
        return True
    if choice == "demo-remix":
        return bool(marks & {"demo", "remix"})
    if choice == "estudio":
        return version["tipo"] == "estudio" and "coletanea" not in marks
    return choice in marks


def readable_music(root, value):
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise ValueError(_('Invalid or symbolic music path'))
    path.relative_to(root)
    if not path.is_file() or path.suffix.casefold() not in (".mp3", ".flac"):
        raise ValueError(_('Missing or invalid file; refresh the library'))
    return path


class App:
    def __init__(self, root: Path, storage: Path | None = None):
        self.root = root.expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError(_('The library must be a folder'))
        self.storage = (storage or BASE).expanduser().resolve()
        if self.storage == self.root or self.root in self.storage.parents:
            raise ValueError(_('Application data must stay outside the music library'))
        self.state_dir = self.storage / ".biblioteca"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.job = None
        self.worker = None
        self.playlist_busy = False
        self.policy_busy = False
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
        self.plan = None
        self.plan_path = None
        self.error = None
        self.downloads = Downloads(self.root, self.storage, self.download_progress)
        self.download_picker = DownloadPicker(self.root, self.download_progress, self.inventory_snapshot, self.refresh_downloaded)
        for path in sorted((self.storage / "relatorios").glob("*/plano.json"), key=lambda p: p.stat().st_mtime, reverse=True):
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
        tracks = json.loads((path.parent / "inventario.json").read_text())["musicas"]
        with self.lock:
            self.plan = plan
            self.plan_path = path
            self.tracks = tracks
            self.by_id = {t["id"]: t for t in tracks}
            self.root = Path(plan["biblioteca"])
            self.library_revision = secrets.token_hex(8)
            self.incremental_paths.clear()

    def start(self, kind, operation):
        with self.lock:
            if self.playlist_busy or self.policy_busy or (self.job and self.job["status"] == "running"):
                raise ValueError(_('Wait for the current operation to finish'))
            if kind in WRITING_JOBS or kind == "direct_download":
                protections.load(self.storage, self.root)
            self.error = None
            self.job = {"id": secrets.token_hex(8), "type": kind, "status": "running", "message": _('Preparing…'), "progress": None}
        job_language = current_language()
        def run():
            with language(job_language):
                try:
                    result = operation()
                    with self.lock:
                        self.job.update(status="completed", message=_('Operation completed'), progress=100, result=result)
                except Exception as error:
                    with self.lock:
                        self.error = str(error)
                        self.job.update(status="error", message=str(error), progress=None)
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

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
                self.job.update(message=_('{done} of {total} · {name}', done=done, total=total, name=name), progress=round(done / total * 100) if total else 0)
        result = create_plan(self.root, output, normalize_albums=True, online=online, progress=progress, storage=self.storage)
        self.load(Path(result["plano"]))
        try:
            self.download_cache.unlink(missing_ok=True)
        except OSError as error:
            result["avisos"] = [_('Could not clear the new-track cache: {error}', error=error)]
        return result

    def inventory_snapshot(self):
        with self.lock:
            return list(self.tracks)

    def lyrics(self, ident):
        with self.lock:
            track = self.by_id.get(ident)
            if track is None:
                raise ValueError(_('Song not found'))
            path = readable_music(self.root, track["arquivo"])
            artist = " / ".join(track["artista"] or track["artista_album"])
            title = " / ".join(track["titulo"])
            album = " / ".join(track["album"])
            seconds = track["duracao_segundos"]
        return lyrics_for_track(path, artist, title, album, seconds, self.lyrics_client)

    def preview_lyrics(self, data):
        if set(data) != {"id", "texto"} or not isinstance(data.get("id"), str):
            raise ValueError(_('Select a track by its ID and review the lyrics'))
        text = data.get("texto")
        if not isinstance(text, str) or not text.strip() or len(text) > 100_000 or "\x00" in text:
            raise ValueError(_('Empty or invalid lyrics'))
        text = text.replace("\r\n", "\n").strip()
        if parse_lrc(text):
            raise ValueError(_('Use plain lyrics without timestamps'))
        with self.lock:
            if self.playlist_busy or self.policy_busy or (self.job and self.job["status"] == "running"):
                raise ValueError(_('Wait for the current operation to finish'))
            protections.load(self.storage, self.root)
            track = self.by_id.get(data["id"])
            if track is None:
                raise ValueError(_('Song not found'))
            path = secure_file(self.root, track["arquivo"])
            before = sha256(path)
            current = current_lyrics(path)
            if current == [text]:
                raise ValueError(_('The track already has exactly these lyrics'))
            if sha256(path) != before:
                raise ValueError(_('The file changed during preview; review it again'))
            ident = secrets.token_hex(20)
            self.lyrics_previews[ident] = {"id": data["id"], "arquivo": str(path), "texto": text, "sha256": before, "criado": time.monotonic()}
            while len(self.lyrics_previews) > 20:
                self.lyrics_previews.pop(next(iter(self.lyrics_previews)))
        return {"previa": ident, "titulo": track["titulo"], "artista": track["artista"], "atual": current, "texto": text, "arquivo": str(path)}

    def save_lyrics(self, data):
        if set(data) != {"previa"} or not isinstance(data.get("previa"), str):
            raise ValueError(_('Review the lyrics preview before saving'))
        with self.lock:
            draft = self.lyrics_previews.get(data["previa"])
            if draft is None or time.monotonic() - draft["criado"] > 900:
                raise ValueError(_('The preview expired; review the lyrics again'))
        def save():
            with self.lock:
                self.lyrics_previews.pop(data["previa"], None)
                track = self.by_id.get(draft["id"])
                if track is None or track["arquivo"] != draft["arquivo"]:
                    raise ValueError(_('The track changed in the inventory; review it again'))
            result = save_lyrics(self.root, self.storage, draft["arquivo"], draft["texto"], draft["sha256"])
            notices = self.refresh_downloaded([{"estado": "baixado", "arquivo": draft["arquivo"]}])
            return {**result, "letra_track_id": draft["id"], "avisos": notices}
        self.start("import", save)

    def load_download_cache(self):
        try:
            data = json.loads(self.download_cache.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("biblioteca") != str(self.root) or not isinstance(data.get("arquivos"), list) or any(not isinstance(p, str) for p in data["arquivos"]):
                raise ValueError(_('Incompatible cache of new tracks'))
            notices = self.refresh_downloaded([{"estado": "baixado", "arquivo": p} for p in data["arquivos"]], persist=False)
            if notices:
                self.error = " ".join(notices)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as error:
            self.error = _('Could not load new tracks; refresh the library: {error}', error=error)

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
                    raise ValueError(_('The file changed while being read'))
                track["id"] = hashlib.sha256(str(path).encode()).hexdigest()[:24]
                updates[track["id"]] = track
            except Exception as error:
                notices.append(_('Download saved, but the track was not added to the inventory: {file_name}. {error}', file_name=path.name, error=error))
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
                notices.append(_('Downloads saved; refresh the library after restarting: {error}', error=error))
        return notices

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
            for change in entry["alteracoes"]:
                if change["campo"] in protections.protected(policy, self.root, entry["arquivo"]):
                    continue
                rows.append({**change, "arquivo": entry["arquivo"], "track_id": track["id"], "titulo": " / ".join(track["titulo"]), "artista": " / ".join(track["artista"]), "album": " / ".join(track["album"]), "tamanho_bytes": entry["tamanho_bytes"]})
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
            rows.append({**row, "id": track.get("id"), "titulo": track.get("titulo", []), "artista": track.get("artista", [])})
        result = {"campos": list(protections.FIELDS), "protecoes": rows}
        if album_id:
            track = by_id.get(album_id)
            if track is None:
                raise ValueError(_('Album not found; refresh the library'))
            members = [t for t in tracks if album_key(t) == album_key(track)]
            result["album"] = {"id": album_id, "titulo": track["album"], "artista": track["artista_album"] or track["artista"],
                               "faixas": len(members), "campos": {f: sum(f in protections.protected(policy, self.root, t["arquivo"]) for t in members) for f in protections.FIELDS}}
        return result

    def change_protections(self, data):
        if "arquivos" in data or "arquivo" in data:
            raise ValueError(_('Select tracks by their library IDs'))
        fields, enabled = data.get("campos"), data.get("proteger")
        if not isinstance(enabled, bool) or not isinstance(fields, list) or not fields or any(not isinstance(f, str) or f not in protections.FIELDS for f in fields):
            raise ValueError(_('Choose valid fields and whether to protect or unlock them'))
        ids, album_id = data.get("ids", []), data.get("album_id", "")
        if not isinstance(ids, list) or len(ids) > 10000 or any(not isinstance(i, str) for i in ids) or not isinstance(album_id, str) or bool(ids) == bool(album_id):
            raise ValueError(_('Select tracks or an album from the library'))
        with self.lock:
            if self.playlist_busy or self.policy_busy or (self.job and self.job["status"] == "running"):
                raise ValueError(_('Wait for the current operation to finish'))
            if album_id:
                track = self.by_id.get(album_id)
                if track is None:
                    raise ValueError(_('Album not found; refresh the library'))
                chosen = [t for t in self.tracks if album_key(t) == album_key(track)]
            else:
                if len(ids) != len(set(ids)) or any(i not in self.by_id for i in ids):
                    raise ValueError(_('Invalid track selection; refresh the library'))
                chosen = [self.by_id[i] for i in ids]
            self.policy_busy = True
        try:
            operation = protections.protect if enabled else protections.unprotect
            return operation(self.storage, self.root, [t["arquivo"] for t in chosen], fields)
        finally:
            with self.lock:
                self.policy_busy = False

    @staticmethod
    def search_query(needle, scope="biblioteca"):
        if not isinstance(needle, str) or len(needle) > 200 or any(ord(c) < 32 for c in needle):
            raise ValueError(_('Use up to 200 characters in the search'))
        if scope not in ("biblioteca", "global"):
            raise ValueError(_('Choose to search the library or the global catalog'))
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
            raise ValueError(_('Invalid library filter'))
        if version not in ("todas", "estudio", "ao_vivo", "acustico", "demo-remix", "coletanea"):
            raise ValueError(_('Invalid version filter'))
        if not isinstance(artist, str) or len(artist) > 200 or not isinstance(album_id, str):
            raise ValueError(_('Invalid artist or album filter'))
        with self.lock:
            rows = self.tracks if tracks is None else tracks
            index = self.library_index(rows)
        if album_id:
            selected = next((t for t in rows if t["id"] == album_id), None)
            if selected is None:
                raise ValueError(_('Album not found; refresh the library'))
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
            if self.playlist_busy or self.policy_busy or (self.job and self.job["status"] == "running"):
                raise ValueError(_('Wait for the current operation to finish'))
            self.playlist_busy = True
            tracks, by_id = list(self.tracks), dict(self.by_id)
        try:
            with library_lock(self.root):
                return self._playlist(data, tracks, by_id, write)
        except BlockingIOError as error:
            raise ValueError(_('The library is busy with another write; wait and try again')) from error
        finally:
            with self.lock:
                self.playlist_busy = False

    def _playlist(self, data, tracks, by_id, write):
        name = data.get("nome", "")
        if not isinstance(name, str):
            raise ValueError(_('Enter a playlist name'))
        name = name.strip()
        if name.casefold().endswith(".m3u8"):
            name = name[:-5].strip()
        if not name or name in (".", "..") or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', name) or len(name.encode("utf-8")) > 240:
            raise ValueError(_('Use a simple name without slashes, special characters or line breaks'))
        album_id = data.get("album_id", "")
        if not isinstance(album_id, str):
            raise ValueError(_('Invalid album'))
        rows = self.library(data.get("q", ""), data.get("filter", "todas"), tracks, data.get("artista", ""), album_id, data.get("versao", "todas"))
        if album_id:
            track = by_id.get(album_id)
            if track is None:
                raise ValueError(_('Album not found; refresh the library'))
            rows = [t for t in rows if album_key(t) == album_key(track)]
        rows = sorted(rows, key=album_order)
        if not rows:
            raise ValueError(_('No songs selected for the playlist'))
        for track in rows:
            readable_music(self.root, track["arquivo"])
        folder = self.root / "Playlists"
        path = folder / (name + ".m3u8")
        if folder.resolve() != folder or path.is_symlink() or path.resolve() != path:
            raise ValueError(_('The Playlists folder and playlist cannot be symbolic links'))
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise ValueError(_('The existing playlist must be a regular file without hard links'))
        # Validate all paths before creating even the Playlists directory.
        render_m3u8(path, rows, name)
        exists = path.exists()
        previous = sha256(path) if exists else None
        preview = {"playlist": str(path), "faixas": len(rows), "existe": exists, "anterior_sha256": previous,
                   "amostra": [{"titulo": t["titulo"], "artista": t["artista"]} for t in rows[:10]]}
        if not write:
            return preview
        overwrite = data.get("sobrescrever", False)
        if not isinstance(overwrite, bool):
            raise ValueError(_('Replacing a playlist requires an explicit choice'))
        if exists and not overwrite:
            raise ValueError(_('The playlist already exists. Choose another name or approve replacement'))
        if overwrite and (not exists or data.get("anterior_sha256") != previous):
            raise ValueError(_('The playlist changed since preview; review it again'))
        backup = None
        if overwrite:
            backup_folder = self.storage / "backups/playlists"
            if backup_folder.resolve() != backup_folder:
                raise ValueError(_('The playlist backup folder cannot be a symbolic link'))
            backup_folder.mkdir(parents=True, exist_ok=True)
            short_name = name.encode("utf-8")[:120].decode("utf-8", errors="ignore")
            backup = backup_folder / (datetime.now().strftime("%Y%m%d_%H%M%S_") + short_name + "_" + secrets.token_hex(8) + ".m3u8")
            durable_copy(path, backup)
            if sha256(backup) != previous or sha256(path) != previous:
                raise ValueError(_('The playlist changed during backup; review it again'))
        return {**write_m3u8(path, rows, name, overwrite=overwrite), "backup": str(backup) if backup else None}

    def history(self):
        rows = []
        for path in (self.storage / "backups").glob("*/manifesto.json"):
            try:
                manifest = json.loads(path.read_text())
                if manifest["biblioteca"] == str(self.root):
                    rows.append({"id": path.parent.name, "tipo": manifest.get("tipo", "metadados"), "categoria": "letra" if str(manifest.get("lote", "")).startswith("letra-") else "", "estado": manifest["estado"], "data": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "arquivos": len(manifest["arquivos"]), "erro": manifest.get("erro")})
            except (OSError, ValueError, KeyError):
                continue
        return sorted(rows, key=lambda r: r["data"], reverse=True)

    def snapshot(self):
        with self.lock:
            policy, policy_error = self.read_protections()
            proposals = self.proposals(policy) if policy is not None else []
            summary = {**(self.plan or {}).get("resumo", {}), "alteracoes_propostas": len(proposals),
                       "lidos": len(self.tracks), "formatos": dict(Counter(t["formato"] for t in self.tracks)),
                       "campos_ausentes": {f: sum(f in t["campos_ausentes"] for t in self.tracks) for f in protections.FIELDS},
                       "com_capa": sum(bool(t["capas"]) for t in self.tracks),
                       "arquivos_com_propostas": len({p["arquivo"] for p in proposals}),
                       "propostas_por_campo": dict(Counter(p["campo"] for p in proposals))}
            revision = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest() if policy is not None else None
            return {"biblioteca": str(self.root), "biblioteca_revisao": self.library_revision, "analisado_em": (self.plan or {}).get("criado_em"), "resumo": summary, "job": self.job, "erro": self.error, "erro_protecoes": policy_error, "protecoes_revisao": revision, "total_albuns": len({(tuple(t["artista_album"] or t["artista"]), tuple(t["album"])) for t in self.tracks}), "total_artistas": len({tuple(t["artista_album"] or t["artista"]) for t in self.tracks}), "duracao_segundos": sum(t["duracao_segundos"] for t in self.tracks), "propostas": len(proposals), "reinicio_necessario": restart_required()}


class Handler(BaseHTTPRequestHandler):
    server_version = "Acervo/0.1"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        pass

    def trusted(self):
        port = self.server.server_port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def send(self, status, data: bytes, mime="application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: https://*.googleusercontent.com https://i.scdn.co https://mosaic.scdn.co https://*.spotifycdn.com; media-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; connect-src 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def json(self, data, status=200):
        self.send(status, json.dumps(to_api(data), ensure_ascii=False).encode())

    def track(self, query):
        with self.app.lock:
            track = getattr(self.app, "by_id", {}).get(query.get("id", [""])[0])
            if track is None:
                raise ValueError(_('Song not found'))
            readable_music(self.app.root, track["arquivo"])
            return track

    def do_GET(self):
        requested = self.headers.get("X-Acervo-Language") or self.headers.get("Accept-Language")
        with language(normalize_language(requested) or "en"):
            self.handle_get()

    def handle_get(self):
        if not self.trusted():
            self.json({"erro": _('Host not allowed')}, 403)
            return
        try:
            self.get_route()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, OSError, KeyError) as error:
            self.json({"erro": str(error)}, 400)
        except RuntimeError as error:
            self.json({"erro": str(error)}, 503)

    def get_route(self):
        url = urlsplit(self.path)
        query = from_api(parse_qs(url.query))
        if url.path in ("/", "/index.html"):
            html = (BASE / "web/index.html").read_text().replace("__TOKEN__", self.app.token)
            self.send(200, html.encode(), "text/html; charset=utf-8")
        elif re.fullmatch(r"/[a-z0-9_-]+\.(js|css)", url.path):
            web = BASE / "web"
            asset = web / url.path[1:]
            if web.is_symlink() or asset.is_symlink() or not asset.is_file() or asset.resolve().parent != web.resolve():
                self.send(404, _('Asset not found').encode(), "text/plain; charset=utf-8")
                return
            mime = "text/javascript; charset=utf-8" if url.path.endswith(".js") else "text/css; charset=utf-8"
            self.send(200, asset.read_bytes(), mime)
        elif url.path == "/api/state":
            self.json(self.app.snapshot())
        elif url.path == "/api/lyrics":
            if set(query) != {"id"} or len(query["id"]) != 1:
                raise ValueError(_('Select a track by its library ID'))
            self.json(self.app.lyrics(query["id"][0]))
        elif url.path == "/api/protections":
            self.json(self.app.protection_info(query.get("album_id", [""])[0]))
        elif url.path == "/api/download":
            self.json(self.app.download_picker.info(query.get("catalogo", [None])[0]))
        elif url.path == "/api/downloads":
            folders = sorted({str(self.app.root)} | {str(Path(t["arquivo"]).parent) for t in self.app.tracks})
            self.json({"lotes": self.app.downloads.list(), "ferramentas": tool_status(), "pastas": folders})
        elif url.path == "/api/downloads/batch":
            self.json(self.app.downloads.load(query.get("id", [""])[0]))
        elif url.path == "/api/downloads/log":
            batch = self.app.downloads.load(query.get("id", [""])[0])
            row_id = query.get("faixa", [""])[0]
            if row_id and row_id not in {r["id"] for r in batch["faixas"]}:
                raise ValueError(_('Track not found'))
            path = self.app.downloads.path(batch["id"]) / (row_id + ".log" if row_id else "catalogo.log")
            self.json({"registro": clean_log(path.read_text(errors="replace")) if path.is_file() else _('No log available.')})
        elif url.path == "/api/suggestions" or (url.path == "/api/search" and query.get("escopo", ["biblioteca"])[0] == "global"):
            self.json(self.app.suggestions(query.get("q", [""])[0], query.get("escopo", ["biblioteca"])[0]))
        elif url.path in ("/api/library", "/api/search"):
            self.app.search_query(query.get("q", [""])[0], query.get("escopo", ["biblioteca"])[0])
            rows = self.app.library(query.get("q", [""])[0], query.get("filter", ["todas"])[0],
                                    artist=query.get("artista", [""])[0], album_id=query.get("album_id", [""])[0], version=query.get("versao", ["todas"])[0])
            offset = max(0, int(query.get("offset", [0])[0]))
            limit = min(100, max(1, int(query.get("limit", [50])[0])))
            self.json({"total": len(rows), "musicas": [{**{k: t[k] for k in ("id", "titulo", "artista", "album", "genero", "ano", "faixa", "formato", "duracao_segundos", "bitrate_bps", "campos_ausentes", "capas")}, "versao": track_version(t)} for t in rows[offset:offset + limit]]})
        elif url.path == "/api/albums":
            groups = defaultdict(list)
            for track in self.app.tracks:
                groups[(tuple(track["artista_album"] or track["artista"]), tuple(track["album"]))].append(track)
            rows = [{"artista": " / ".join(artist), "album": " / ".join(album), "track_id": members[0]["id"], "faixas": len(members), "ano": " / ".join(members[0]["ano"]), "genero": " / ".join(members[0]["genero"])} for (artist, album), members in groups.items()]
            self.json({"albuns": rows})
        elif url.path == "/api/proposals":
            self.json({"alteracoes": self.app.proposals()})
        elif url.path == "/api/unidentified":
            suggested = {p["arquivo"] for p in self.app.proposals() if p["campo"] == "genero"}
            policy, error = self.app.read_protections()
            rows = [t for t in self.app.tracks if "genero" in t["campos_ausentes"] and t["arquivo"] not in suggested and "genero" not in protections.protected(policy, self.app.root, t["arquivo"])] if policy is not None else []
            self.json({"musicas": rows, "erro_protecoes": error})
        elif url.path == "/api/history":
            self.json({"execucoes": self.app.history()})
        elif url.path == "/api/track":
            track = self.track(query)
            policy, error = self.app.read_protections()
            fields = sorted(protections.protected(policy, self.app.root, track["arquivo"])) if policy is not None else None
            self.json({**track, "campos_protegidos": fields, "erro_protecoes": error})
        elif url.path in ("/api/cover", "/api/downloads/cover"):
            path = self.app.downloads.file(query.get("id", [""])[0], query.get("faixa", [""])[0]) if url.path.startswith("/api/downloads/") else Path(self.track(query)["arquivo"])
            covers = ID3(path).getall("APIC") if path.suffix.casefold() == ".mp3" else FLAC(path).pictures
            if not covers:
                self.send(404, b"")
                return
            cover = next((c for c in covers if c.type == 3), covers[0])
            mime = cover.mime if cover.mime in ("image/jpeg", "image/png", "image/webp", "image/gif") else "application/octet-stream"
            self.send(200, cover.data, mime)
        elif url.path in ("/api/audio", "/api/downloads/audio"):
            path = self.app.downloads.file(query.get("id", [""])[0], query.get("faixa", [""])[0]) if url.path.startswith("/api/downloads/") else Path(self.track(query)["arquivo"])
            size = path.stat().st_size
            start, end = 0, size - 1
            header = self.headers.get("Range")
            if header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
                if not match or not any(match.groups()):
                    self.send(416, b"", extra={"Content-Range": f"bytes */{size}"})
                    return
                first, last = match.groups()
                if not first:
                    start = max(0, size - int(last))
                else:
                    start = int(first)
                    end = min(size - 1, int(last)) if last else size - 1
                if start > end or start >= size:
                    self.send(416, b"", extra={"Content-Range": f"bytes */{size}"})
                    return
            self.send_response(206 if header else 200)
            self.send_header("Content-Type", "audio/mpeg" if path.suffix.casefold() == ".mp3" else "audio/flac")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            if header:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = handle.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        elif url.path == "/api/export":
            if self.app.plan_path is None:
                raise ValueError(_('Run a scan first'))
            self.send(200, (self.app.plan_path.parent / "alteracoes.csv").read_bytes(), "text/csv; charset=utf-8", {"Content-Disposition": 'attachment; filename="changes.csv"'})
        else:
            self.send(404, _('Page not found').encode(), "text/plain; charset=utf-8")

    def do_POST(self):
        requested = self.headers.get("X-Acervo-Language") or self.headers.get("Accept-Language")
        with language(normalize_language(requested) or "en"):
            self.handle_post()

    def handle_post(self):
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if not self.trusted() or origin != "http://" + host or not secrets.compare_digest(self.headers.get("X-Library-Token", ""), self.app.token):
            self.json({"erro": _('Unauthorized request')}, 403)
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if size < 0 or size > 1024 * 1024 or self.headers.get_content_type() != "application/json":
                raise ValueError(_('Invalid request'))
            data = from_api(json.loads(self.rfile.read(size)))
            if not isinstance(data, dict):
                raise ValueError(_('The request body must be a JSON object'))
            route = urlsplit(self.path).path
            if route == "/api/analyze":
                online = MusicBrainz(self.app.state_dir / "cache/musicbrainz", data.get("contato", ""), 200) if data.get("musicbrainz") else None
                self.app.start("analysis", lambda: self.app.analyze(online))
            elif route == "/api/lyrics/preview":
                self.json(self.app.preview_lyrics(data))
                return
            elif route == "/api/lyrics/save":
                self.app.save_lyrics(data)
            elif route == "/api/download/search":
                self.app.download_picker.search_options(data)
                self.app.start("direct_search", lambda: self.app.download_picker.search(data))
            elif route == "/api/download/open":
                self.app.download_picker.open_options(data)
                self.app.start("direct_search", lambda: self.app.download_picker.open(data))
            elif route == "/api/download/download":
                self.app.download_picker.download_options(data)
                self.app.start("direct_download", lambda: self.app.download_picker.download(data))
            elif route in ("/api/playlists/preview", "/api/playlists/export"):
                self.json(self.app.playlist(data, write=route.endswith("export")))
                return
            elif route == "/api/protections":
                self.json(self.app.change_protections(data))
                return
            elif route == "/api/downloads/prepare":
                self.app.start("catalog", lambda: self.app.downloads.prepare(data, list(self.app.tracks)))
            elif route == "/api/downloads/fetch":
                ident, ids = data.get("id", ""), data.get("faixas", [])
                self.app.downloads.load(ident)
                if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
                    raise ValueError(_('Invalid selection'))
                self.app.start("download", lambda: self.app.downloads.download(ident, ids))
            elif route == "/api/downloads/cancel":
                if not self.app.job or self.app.job["status"] != "running" or self.app.job["type"] not in ("download", "catalog"):
                    raise ValueError(_('No lookup or download is running'))
                self.app.downloads.runner.cancelled.set()
                self.json({"cancelamento_solicitado": True})
                return
            elif route in ("/api/downloads/preview", "/api/downloads/import"):
                ident, selections = data.get("id", ""), data.get("selecoes", [])
                operations = self.app.downloads.operations(ident, selections)
                if route.endswith("preview"):
                    self.json({"operacoes": operations})
                    return
                def import_downloads():
                    self.app.download_progress(_('Creating backups and validating approved files…'), None)
                    result = self.app.downloads.import_batch(ident, selections)
                    self.app.analyze()
                    return result
                self.app.start("import", import_downloads)
            elif route == "/api/apply":
                ids = set(data.get("ids", []))
                if not ids or any(not isinstance(i, str) for i in ids):
                    raise ValueError(_('Select changes to approve'))
                plan_path = self.app.plan_path
                if plan_path is None:
                    raise ValueError(_('Scan the library first'))
                plan = load_plan(plan_path)
                known = {c["id"] for t in plan["arquivos"] for c in t["alteracoes"]}
                if not ids <= known:
                    raise ValueError(_('The selection is outdated; reload the page'))
                def apply():
                    path = self.app.state_dir / ("aprovacoes_" + secrets.token_hex(8) + ".csv")
                    with (plan_path.parent / "alteracoes.csv").open(encoding="utf-8-sig", newline="") as handle:
                        reader = csv.DictReader(handle)
                        fields = reader.fieldnames
                        rows = list(reader)
                    for row in rows:
                        row["aprovar"] = "sim" if row["id"] in ids else "nao"
                    with path.open("w", encoding="utf-8-sig", newline="") as handle:
                        writer = csv.DictWriter(handle, fieldnames=fields)
                        writer.writeheader()
                        writer.writerows(rows)
                    self.app.job["message"] = _('Creating full backups and validating tags…')
                    result = apply_plan(plan_path, path, self.app.storage / "backups", storage=self.app.storage)
                    self.app.analyze()
                    return result
                self.app.start("apply", apply)
            elif route == "/api/restore":
                ident = data.get("id", "")
                if ident not in {r["id"] for r in self.app.history()}:
                    raise ValueError(_('Operation not found'))
                def undo():
                    self.app.job["message"] = _('Validating backups and restoring files…')
                    result = restore(self.app.storage / "backups" / ident / "manifesto.json", storage=self.app.storage)
                    self.app.analyze()
                    return result
                self.app.start("restore", undo)
            else:
                self.json({"erro": _('Route not found')}, 404)
                return
            self.json({"iniciado": True}, 202)
        except (ValueError, OSError, TypeError) as error:
            self.json({"erro": str(error)}, 400)


def serve(root: Path, port: int = 8765, storage: Path | None = None, open_browser: bool = False):
    app = App(root, storage)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = app
    print(f"Acervo · http://127.0.0.1:{port}", flush=True)
    print(_('Library: {folder}', folder=app.root), flush=True)
    if app.plan is None:
        app.start("analysis", app.analyze)
    if open_browser:
        import webbrowser
        threading.Timer(.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
        while app.writing():
            print(_('\nWaiting for the current write to finish so files are not left incomplete…'), flush=True)
            try:
                app.worker.join()
            except KeyboardInterrupt:
                continue
        print(_('\nInterface closed.'))
    finally:
        server.server_close()
        app.global_search.close()
