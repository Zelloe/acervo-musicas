"""Interface local do Acervo. Escuta exclusivamente no endereço de loopback."""

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import parse_qs, urlsplit

from mutagen.flac import FLAC
from mutagen.id3 import ID3

from biblioteca_core import apply_plan, atomic_json, durable_copy, library_lock, load_plan, restore, secure_file, sha256
from biblioteca_downloads import Downloads, clean_log, tool_status
from biblioteca_plano import BASE, create_plan
from musicbrainz_local import MusicBrainz
from playlists import album_order, render as render_m3u8, write_m3u8


WRITING_JOBS = {"importacao", "aplicacao", "restauracao"}


class App:
    def __init__(self, root: Path, storage: Path | None = None):
        self.root = root.expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("A biblioteca deve ser uma pasta")
        self.storage = (storage or BASE).expanduser().resolve()
        if self.storage == self.root or self.root in self.storage.parents:
            raise ValueError("Os dados do programa devem ficar fora da biblioteca musical")
        self.state_dir = self.storage / ".biblioteca"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.job = None
        self.worker = None
        self.playlist_busy = False
        self.tracks = []
        self.by_id = {}
        self.plan = None
        self.plan_path = None
        self.error = None
        self.downloads = Downloads(self.root, self.storage, self.download_progress)
        for path in sorted((self.storage / "relatorios").glob("*/plano.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                plan = load_plan(path)
                if plan["biblioteca"] == str(self.root):
                    self.load(path)
                    break
            except (OSError, ValueError, KeyError):
                continue

    def load(self, path: Path):
        plan = load_plan(path)
        tracks = json.loads((path.parent / "inventario.json").read_text())["musicas"]
        with self.lock:
            self.plan = plan
            self.plan_path = path
            self.tracks = tracks
            self.by_id = {t["id"]: t for t in tracks}
            self.root = Path(plan["biblioteca"])

    def start(self, kind, operation):
        with self.lock:
            if self.playlist_busy or (self.job and self.job["estado"] == "executando"):
                raise ValueError("Aguarde a operação atual terminar")
            self.error = None
            self.job = {"id": secrets.token_hex(8), "tipo": kind, "estado": "executando", "mensagem": "Preparando…", "progresso": None}
        def run():
            try:
                result = operation()
                with self.lock:
                    self.job.update(estado="concluido", mensagem="Operação concluída", progresso=100, resultado=result)
            except Exception as error:
                with self.lock:
                    self.error = str(error)
                    self.job.update(estado="erro", mensagem=str(error), progresso=None)
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def writing(self):
        """Tarefa que grava na coleção e não deve ser interrompida no meio."""
        with self.lock:
            job = self.job
        return bool(job and job["estado"] == "executando" and job["tipo"] in WRITING_JOBS and self.worker)

    def download_progress(self, message, percent):
        with self.lock:
            if self.job:
                self.job.update(mensagem=message, progresso=percent)

    def analyze(self, online=None):
        output = self.storage / "relatorios" / datetime.now().strftime("previa_%Y%m%d_%H%M%S_%f")
        def progress(done, total, name):
            with self.lock:
                self.job.update(mensagem=f"{done} de {total} · {name}", progresso=round(done / total * 100) if total else 0)
        result = create_plan(self.root, output, normalize_albums=True, online=online, progress=progress)
        self.load(Path(result["plano"]))
        return result

    def proposals(self):
        tracks = {t["arquivo"]: t for t in self.tracks}
        rows = []
        for entry in (self.plan or {}).get("arquivos", []):
            track = tracks[entry["arquivo"]]
            for change in entry["alteracoes"]:
                rows.append({**change, "arquivo": entry["arquivo"], "track_id": track["id"], "titulo": " / ".join(track["titulo"]), "artista": " / ".join(track["artista"]), "album": " / ".join(track["album"]), "tamanho_bytes": entry["tamanho_bytes"]})
        return rows

    def library(self, needle="", kind="todas", tracks=None):
        if not isinstance(needle, str) or kind not in ("todas", "sem-genero", "flac", "versoes"):
            raise ValueError("Filtro de biblioteca inválido")
        rows = self.tracks if tracks is None else tracks
        if needle:
            rows = [t for t in rows if needle.casefold() in " ".join(t["titulo"] + t["artista"] + t["album"]).casefold()]
        if kind == "sem-genero":
            rows = [t for t in rows if "genero" in t["campos_ausentes"]]
        elif kind == "flac":
            rows = [t for t in rows if t["formato"] == "FLAC"]
        elif kind == "versoes":
            rows = [t for t in rows if t["marcadores_versao"]]
        return rows

    def playlist(self, data, write=False):
        with self.lock:
            if self.playlist_busy or (self.job and self.job["estado"] == "executando"):
                raise ValueError("Aguarde a operação atual terminar")
            self.playlist_busy = True
            tracks, by_id = list(self.tracks), dict(self.by_id)
        try:
            with library_lock(self.root):
                return self._playlist(data, tracks, by_id, write)
        except BlockingIOError as error:
            raise ValueError("A biblioteca está ocupada por outra gravação; aguarde e tente novamente") from error
        finally:
            with self.lock:
                self.playlist_busy = False

    def _playlist(self, data, tracks, by_id, write):
        name = data.get("nome", "")
        if not isinstance(name, str):
            raise ValueError("Informe um nome para a playlist")
        name = name.strip()
        if name.casefold().endswith(".m3u8"):
            name = name[:-5].strip()
        if not name or name in (".", "..") or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', name) or len(name.encode("utf-8")) > 240:
            raise ValueError("Use um nome simples, sem barras, caracteres especiais ou quebras de linha")
        album_id = data.get("album_id", "")
        if not isinstance(album_id, str):
            raise ValueError("Álbum inválido")
        rows = self.library(data.get("q", ""), data.get("filter", "todas"), tracks)
        if album_id:
            track = by_id.get(album_id)
            if track is None:
                raise ValueError("Álbum não encontrado; atualize a biblioteca")
            def group(t):
                return (tuple(t["artista_album"] or t["artista"]), tuple(t["album"]))
            rows = [t for t in rows if group(t) == group(track)]
        rows = sorted(rows, key=album_order)
        if not rows:
            raise ValueError("Nenhuma música selecionada para a playlist")
        for track in rows:
            music = Path(track["arquivo"])
            music.relative_to(self.root)
            if music.is_symlink() or music.resolve() != music or not music.is_file():
                raise ValueError("Arquivo ausente ou simbólico; atualize a biblioteca")
        folder = self.root / "Playlists"
        path = folder / (name + ".m3u8")
        if folder.resolve() != folder or path.is_symlink() or path.resolve() != path:
            raise ValueError("A pasta Playlists e a playlist não podem ser links simbólicos")
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise ValueError("A playlist existente deve ser um arquivo comum, sem hard links")
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
            raise ValueError("A substituição da playlist exige uma escolha explícita")
        if exists and not overwrite:
            raise ValueError("A playlist já existe. Escolha outro nome ou aprove a substituição")
        if overwrite and (not exists or data.get("anterior_sha256") != previous):
            raise ValueError("A playlist mudou desde a prévia; revise novamente")
        backup = None
        if overwrite:
            backup_folder = self.storage / "backups/playlists"
            if backup_folder.resolve() != backup_folder:
                raise ValueError("A pasta de backup de playlists não pode ser simbólica")
            backup_folder.mkdir(parents=True, exist_ok=True)
            short_name = name.encode("utf-8")[:120].decode("utf-8", errors="ignore")
            backup = backup_folder / (datetime.now().strftime("%Y%m%d_%H%M%S_") + short_name + "_" + secrets.token_hex(8) + ".m3u8")
            durable_copy(path, backup)
            if sha256(backup) != previous or sha256(path) != previous:
                raise ValueError("A playlist mudou durante o backup; revise novamente")
        return {**write_m3u8(path, rows, name, overwrite=overwrite), "backup": str(backup) if backup else None}

    def history(self):
        rows = []
        for path in (self.storage / "backups").glob("*/manifesto.json"):
            try:
                manifest = json.loads(path.read_text())
                if manifest["biblioteca"] == str(self.root):
                    rows.append({"id": path.parent.name, "tipo": manifest.get("tipo", "metadados"), "estado": manifest["estado"], "data": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "arquivos": len(manifest["arquivos"]), "erro": manifest.get("erro")})
            except (OSError, ValueError, KeyError):
                continue
        return sorted(rows, key=lambda r: r["data"], reverse=True)

    def snapshot(self):
        with self.lock:
            summary = (self.plan or {}).get("resumo", {})
            return {"biblioteca": str(self.root), "analisado_em": (self.plan or {}).get("criado_em"), "resumo": summary, "job": self.job, "erro": self.error, "total_albuns": len({(tuple(t["artista_album"] or t["artista"]), tuple(t["album"])) for t in self.tracks}), "total_artistas": len({tuple(t["artista_album"] or t["artista"]) for t in self.tracks}), "duracao_segundos": sum(t["duracao_segundos"] for t in self.tracks), "propostas": len(self.proposals())}


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
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; media-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; connect-src 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def json(self, data, status=200):
        self.send(status, json.dumps(data, ensure_ascii=False).encode())

    def track(self, query):
        with self.app.lock:
            track = getattr(self.app, "by_id", {}).get(query.get("id", [""])[0])
            if track is None:
                raise ValueError("Música não encontrada")
            secure_file(self.app.root, track["arquivo"])
            return track

    def do_GET(self):
        if not self.trusted():
            self.json({"erro": "Host não permitido"}, 403)
            return
        try:
            self.get_route()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, OSError, KeyError) as error:
            self.json({"erro": str(error)}, 400)

    def get_route(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            html = (BASE / "web/index.html").read_text().replace("__TOKEN__", self.app.token)
            self.send(200, html.encode(), "text/html; charset=utf-8")
        elif url.path in ("/app.js", "/downloads.js", "/style.css"):
            mime = "text/javascript; charset=utf-8" if url.path.endswith(".js") else "text/css; charset=utf-8"
            self.send(200, (BASE / "web" / url.path[1:]).read_bytes(), mime)
        elif url.path == "/api/state":
            self.json(self.app.snapshot())
        elif url.path == "/api/downloads":
            folders = sorted({str(self.app.root)} | {str(Path(t["arquivo"]).parent) for t in self.app.tracks})
            self.json({"lotes": self.app.downloads.list(), "ferramentas": tool_status(), "pastas": folders})
        elif url.path == "/api/downloads/batch":
            self.json(self.app.downloads.load(query.get("id", [""])[0]))
        elif url.path == "/api/downloads/log":
            batch = self.app.downloads.load(query.get("id", [""])[0])
            row_id = query.get("faixa", [""])[0]
            if row_id and row_id not in {r["id"] for r in batch["faixas"]}:
                raise ValueError("Faixa não encontrada")
            path = self.app.downloads.path(batch["id"]) / (row_id + ".log" if row_id else "catalogo.log")
            self.json({"registro": clean_log(path.read_text(errors="replace")) if path.is_file() else "Sem registro disponível."})
        elif url.path == "/api/library":
            rows = self.app.library(query.get("q", [""])[0], query.get("filter", ["todas"])[0])
            offset = max(0, int(query.get("offset", [0])[0]))
            limit = min(100, max(1, int(query.get("limit", [50])[0])))
            self.json({"total": len(rows), "musicas": [{k: t[k] for k in ("id", "titulo", "artista", "album", "genero", "ano", "faixa", "formato", "duracao_segundos", "bitrate_bps", "campos_ausentes", "capas")} for t in rows[offset:offset + limit]]})
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
            self.json({"musicas": [t for t in self.app.tracks if "genero" in t["campos_ausentes"] and t["arquivo"] not in suggested]})
        elif url.path == "/api/history":
            self.json({"execucoes": self.app.history()})
        elif url.path == "/api/track":
            self.json(self.track(query))
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
                raise ValueError("Execute uma análise primeiro")
            self.send(200, (self.app.plan_path.parent / "alteracoes.csv").read_bytes(), "text/csv; charset=utf-8", {"Content-Disposition": 'attachment; filename="alteracoes.csv"'})
        else:
            self.send(404, b"Pagina nao encontrada", "text/plain; charset=utf-8")

    def do_POST(self):
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if not self.trusted() or origin != "http://" + host or not secrets.compare_digest(self.headers.get("X-Library-Token", ""), self.app.token):
            self.json({"erro": "Requisição não autorizada"}, 403)
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if size < 0 or size > 1024 * 1024 or self.headers.get_content_type() != "application/json":
                raise ValueError("Requisição inválida")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("O corpo da requisição deve ser um objeto JSON")
            route = urlsplit(self.path).path
            if route == "/api/analyze":
                online = MusicBrainz(self.app.state_dir / "cache/musicbrainz", data.get("contato", ""), 200) if data.get("musicbrainz") else None
                self.app.start("analise", lambda: self.app.analyze(online))
            elif route in ("/api/playlists/preview", "/api/playlists/export"):
                self.json(self.app.playlist(data, write=route.endswith("export")))
                return
            elif route == "/api/downloads/prepare":
                self.app.start("catalogo", lambda: self.app.downloads.prepare(data, list(self.app.tracks)))
            elif route == "/api/downloads/fetch":
                ident, ids = data.get("id", ""), data.get("faixas", [])
                self.app.downloads.load(ident)
                if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
                    raise ValueError("Seleção inválida")
                self.app.start("download", lambda: self.app.downloads.download(ident, ids))
            elif route == "/api/downloads/cancel":
                if not self.app.job or self.app.job["estado"] != "executando" or self.app.job["tipo"] not in ("download", "catalogo"):
                    raise ValueError("Não há consulta ou download em andamento")
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
                    self.app.download_progress("Criando backups e validando os arquivos aprovados…", None)
                    result = self.app.downloads.import_batch(ident, selections)
                    self.app.analyze()
                    return result
                self.app.start("importacao", import_downloads)
            elif route == "/api/apply":
                ids = set(data.get("ids", []))
                if not ids or any(not isinstance(i, str) for i in ids):
                    raise ValueError("Selecione alterações para aprovar")
                plan_path = self.app.plan_path
                if plan_path is None:
                    raise ValueError("Analise a biblioteca primeiro")
                plan = load_plan(plan_path)
                known = {c["id"] for t in plan["arquivos"] for c in t["alteracoes"]}
                if not ids <= known:
                    raise ValueError("A seleção está desatualizada; recarregue a página")
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
                    self.app.job["mensagem"] = "Criando backups completos e validando as tags…"
                    result = apply_plan(plan_path, path, self.app.storage / "backups")
                    self.app.analyze()
                    return result
                self.app.start("aplicacao", apply)
            elif route == "/api/restore":
                ident = data.get("id", "")
                if ident not in {r["id"] for r in self.app.history()}:
                    raise ValueError("Execução não encontrada")
                def undo():
                    self.app.job["mensagem"] = "Validando backups e restaurando os arquivos…"
                    result = restore(self.app.storage / "backups" / ident / "manifesto.json")
                    self.app.analyze()
                    return result
                self.app.start("restauracao", undo)
            else:
                self.json({"erro": "Rota não encontrada"}, 404)
                return
            self.json({"iniciado": True}, 202)
        except (ValueError, OSError, TypeError) as error:
            self.json({"erro": str(error)}, 400)


def serve(root: Path, port: int = 8765, storage: Path | None = None, open_browser: bool = False):
    app = App(root, storage)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = app
    print(f"Acervo · http://127.0.0.1:{port}", flush=True)
    print(f"Biblioteca: {app.root}", flush=True)
    if app.plan is None:
        app.start("analise", app.analyze)
    if open_browser:
        import webbrowser
        threading.Timer(.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
        while app.writing():
            print("\nAguardando a gravação em andamento terminar para não deixar arquivos pela metade…", flush=True)
            try:
                app.worker.join()
            except KeyboardInterrupt:
                continue
        print("\nInterface encerrada.")
    finally:
        server.server_close()
