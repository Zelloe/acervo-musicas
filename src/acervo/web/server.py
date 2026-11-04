"""Local HTTP server for the interface."""

import csv
import json
import os
import re
import secrets
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from mutagen.flac import FLAC
from mutagen.id3 import ID3

from acervo.app import App, readable_music
from acervo.downloads.staged import clean_log, tool_status
from acervo.library import protections
from acervo.library.core import (
    apply_plan,
    load_plan,
    restore,
)
from acervo.library.musicbrainz import MusicBrainz
from acervo.library.query import sort_tracks, track_version
from acervo.messages import _, language, normalize_language
from acervo.paths import STATIC
from acervo.web.api_contract import from_api, to_api

LIBRARY_SWITCH = threading.Lock()


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
        if status != 304:  # A 304 describes the cached response, not an empty one.
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        extra = dict(extra or {})
        self.send_header("Cache-Control", extra.pop("Cache-Control", "no-store"))
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https://*.googleusercontent.com https://i.scdn.co https://mosaic.scdn.co https://*.spotifycdn.com; media-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; connect-src 'self'",
        )
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def json(self, data, status=200):
        self.send(status, json.dumps(to_api(data), ensure_ascii=False).encode())

    def track(self, query):
        with self.app.lock:
            track = getattr(self.app, "by_id", {}).get(query.get("id", [""])[0])
            if track is None:
                raise ValueError(_("Song not found"))
            readable_music(self.app.root, track["arquivo"])
            return track

    def do_GET(self):
        requested = self.headers.get("X-Acervo-Language") or self.headers.get("Accept-Language")
        with language(normalize_language(requested) or "en"):
            self.handle_get()

    def handle_get(self):
        if not self.trusted():
            self.json({"erro": _("Host not allowed")}, 403)
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
            html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", self.app.token)
            self.send(200, html.encode(), "text/html; charset=utf-8")
        elif re.fullmatch(r"/[a-z0-9_-]+\.(js|css)", url.path):
            web = STATIC
            asset = web / url.path[1:]
            if web.is_symlink() or asset.is_symlink() or not asset.is_file() or asset.resolve().parent != web.resolve():
                self.send(404, _("Asset not found").encode(), "text/plain; charset=utf-8")
                return
            mime = "text/javascript; charset=utf-8" if url.path.endswith(".js") else "text/css; charset=utf-8"
            self.send(200, asset.read_bytes(), mime)
        elif url.path == "/api/state":
            self.json(self.app.snapshot())
        elif url.path == "/api/lyrics/bulk":
            if set(query) - {"id"} or len(query.get("id", [None])) != 1:
                raise ValueError(_("Select lyrics from the lookup results"))
            self.json(self.app.bulk_lyrics_info(query.get("id", [None])[0]))
        elif url.path == "/api/lyrics":
            if set(query) != {"id"} or len(query["id"]) != 1:
                raise ValueError(_("Select a track by its library ID"))
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
                raise ValueError(_("Track not found"))
            path = self.app.downloads.path(batch["id"]) / (row_id + ".log" if row_id else "catalogo.log")
            self.json(
                {
                    "registro": clean_log(path.read_text(errors="replace", encoding="utf-8"))
                    if path.is_file()
                    else _("No log available.")
                }
            )
        elif url.path == "/api/suggestions" or (
            url.path == "/api/search" and query.get("escopo", ["biblioteca"])[0] == "global"
        ):
            self.json(self.app.suggestions(query.get("q", [""])[0], query.get("escopo", ["biblioteca"])[0]))
        elif url.path in ("/api/library", "/api/search", "/api/queue"):
            self.app.search_query(query.get("q", [""])[0], query.get("escopo", ["biblioteca"])[0])
            rows = self.app.library(
                query.get("q", [""])[0],
                query.get("filter", ["todas"])[0],
                artist=query.get("artista", [""])[0],
                album_id=query.get("album_id", [""])[0],
                version=query.get("versao", ["todas"])[0],
            )
            rows = sort_tracks(rows, query.get("sort", ["default"])[0], query.get("direction", ["asc"])[0])
            if url.path == "/api/queue":
                self.json(
                    {
                        "total": len(rows),
                        "musicas": [
                            {k: t[k] for k in ("id", "titulo", "artista", "album", "duracao_segundos")} for t in rows
                        ],
                    }
                )
                return
            offset = max(0, int(query.get("offset", [0])[0]))
            limit = min(100, max(1, int(query.get("limit", [50])[0])))
            self.json(
                {
                    "total": len(rows),
                    "musicas": [
                        {
                            **{
                                k: t[k]
                                for k in (
                                    "id",
                                    "titulo",
                                    "artista",
                                    "album",
                                    "genero",
                                    "ano",
                                    "faixa",
                                    "formato",
                                    "duracao_segundos",
                                    "bitrate_bps",
                                    "campos_ausentes",
                                    "capas",
                                )
                            },
                            "versao": track_version(t),
                        }
                        for t in rows[offset : offset + limit]
                    ],
                }
            )
        elif url.path == "/api/albums":
            groups = defaultdict(list)
            for track in self.app.tracks:
                groups[(tuple(track["artista_album"] or track["artista"]), tuple(track["album"]))].append(track)
            rows = [
                {
                    "artista": " / ".join(artist),
                    "album": " / ".join(album),
                    "track_id": members[0]["id"],
                    "tem_capa": bool(members[0].get("capas")),
                    "faixas": len(members),
                    "ano": " / ".join(members[0]["ano"]),
                    "genero": " / ".join(members[0]["genero"]),
                }
                for (artist, album), members in groups.items()
            ]
            self.json({"albuns": rows})
        elif url.path == "/api/proposals":
            self.json({"alteracoes": self.app.proposals()})
        elif url.path == "/api/unidentified":
            suggested = {p["arquivo"] for p in self.app.proposals() if p["campo"] == "genero"}
            policy, error = self.app.read_protections()
            rows = (
                [
                    t
                    for t in self.app.tracks
                    if "genero" in t["campos_ausentes"]
                    and t["arquivo"] not in suggested
                    and "genero" not in protections.protected(policy, self.app.root, t["arquivo"])
                ]
                if policy is not None
                else []
            )
            self.json({"musicas": rows, "erro_protecoes": error})
        elif url.path == "/api/history":
            self.json({"execucoes": self.app.history()})
        elif url.path == "/api/track":
            track = self.track(query)
            policy, error = self.app.read_protections()
            fields = (
                sorted(protections.protected(policy, self.app.root, track["arquivo"])) if policy is not None else None
            )
            self.json({**track, "campos_protegidos": fields, "erro_protecoes": error})
        elif url.path in ("/api/cover", "/api/downloads/cover"):
            path = (
                self.app.downloads.file(query.get("id", [""])[0], query.get("faixa", [""])[0])
                if url.path.startswith("/api/downloads/")
                else Path(self.track(query)["arquivo"])
            )
            tag = None
            if url.path == "/api/cover":
                stat = path.stat()
                tag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
                if self.headers.get("If-None-Match") == tag:
                    self.send(304, b"", extra={"ETag": tag, "Cache-Control": "private, max-age=60"})
                    return
            covers = ID3(path).getall("APIC") if path.suffix.casefold() == ".mp3" else FLAC(path).pictures
            if not covers:
                self.send(404, b"")
                return
            cover = next((c for c in covers if c.type == 3), covers[0])
            mime = (
                cover.mime
                if cover.mime in ("image/jpeg", "image/png", "image/webp", "image/gif")
                else "application/octet-stream"
            )
            self.send(
                200, cover.data, mime, extra={"ETag": tag, "Cache-Control": "private, max-age=60"} if tag else None
            )
        elif url.path in ("/api/audio", "/api/downloads/audio"):
            path = (
                self.app.downloads.file(query.get("id", [""])[0], query.get("faixa", [""])[0])
                if url.path.startswith("/api/downloads/")
                else Path(self.track(query)["arquivo"])
            )
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
                raise ValueError(_("Run a scan first"))
            self.send(
                200,
                (self.app.plan_path.parent / "alteracoes.csv").read_bytes(),
                "text/csv; charset=utf-8",
                {"Content-Disposition": 'attachment; filename="changes.csv"'},
            )
        else:
            self.send(404, _("Page not found").encode(), "text/plain; charset=utf-8")

    def do_POST(self):
        requested = self.headers.get("X-Acervo-Language") or self.headers.get("Accept-Language")
        with language(normalize_language(requested) or "en"):
            self.handle_post()

    def handle_post(self):
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if (
            not self.trusted()
            or origin != "http://" + host
            or not secrets.compare_digest(self.headers.get("X-Library-Token", ""), self.app.token)
        ):
            self.json({"erro": _("Unauthorized request")}, 403)
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if size < 0 or size > 1024 * 1024 or self.headers.get_content_type() != "application/json":
                raise ValueError(_("Invalid request"))
            data = from_api(json.loads(self.rfile.read(size)))
            if not isinstance(data, dict):
                raise ValueError(_("The request body must be a JSON object"))
            route = urlsplit(self.path).path
            if route == "/api/analyze":
                online = (
                    MusicBrainz(self.app.state_dir / "cache/musicbrainz", data.get("contato", ""), 200)
                    if data.get("musicbrainz")
                    else None
                )
                self.app.start("analysis", lambda: self.app.analyze(online))
            elif route == "/api/lyrics/bulk/start":
                self.app.start_bulk_lyrics(data)
            elif route == "/api/lyrics/bulk/cancel":
                self.json(self.app.cancel_bulk_lyrics(data))
                return
            elif route == "/api/lyrics/bulk/preview":
                self.json(self.app.preview_bulk_lyrics(data))
                return
            elif route == "/api/lyrics/bulk/save":
                self.app.save_bulk_lyrics(data)
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
                self.app.queue_download(data)
            elif route == "/api/download/queue/remove":
                self.app.remove_download(data)
            elif route == "/api/folders":
                self.json(list_folders(data.get("path")))
                return
            elif route == "/api/library/open":
                self.json({"biblioteca": str(open_library(self.server, data.get("pasta")).root)})
                return
            elif route == "/api/catalog/search":
                self.json({"catalogo": self.app.download_picker.browse_search(data)})
                return
            elif route == "/api/catalog/open":
                self.json({"catalogo": self.app.download_picker.browse_open(data)})
                return
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
                    raise ValueError(_("Invalid selection"))
                self.app.start("download", lambda: self.app.downloads.download(ident, ids))
            elif route == "/api/downloads/cancel":
                if (
                    not self.app.job
                    or self.app.job["status"] != "running"
                    or self.app.job["type"] not in ("download", "catalog")
                ):
                    raise ValueError(_("No lookup or download is running"))
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
                    self.app.download_progress(_("Creating backups and validating approved files…"), None)
                    result = self.app.downloads.import_batch(ident, selections)
                    self.app.analyze()
                    return result

                self.app.start("import", import_downloads)
            elif route == "/api/apply":
                ids = set(data.get("ids", []))
                if not ids or any(not isinstance(i, str) for i in ids):
                    raise ValueError(_("Select changes to approve"))
                plan_path = self.app.plan_path
                if plan_path is None:
                    raise ValueError(_("Scan the library first"))
                plan = load_plan(plan_path)
                known = {c["id"] for t in plan["arquivos"] for c in t["alteracoes"]}
                if not ids <= known:
                    raise ValueError(_("The selection is outdated; reload the page"))

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
                    self.app.job["message"] = _("Creating full backups and validating tags…")
                    result = apply_plan(plan_path, path, self.app.storage / "backups", storage=self.app.storage)
                    self.app.analyze()
                    return result

                self.app.start("apply", apply)
            elif route == "/api/restore":
                ident = data.get("id", "")
                if ident not in {r["id"] for r in self.app.history()}:
                    raise ValueError(_("Operation not found"))

                def undo():
                    self.app.job["message"] = _("Validating backups and restoring files…")
                    result = restore(self.app.storage / "backups" / ident / "manifesto.json", storage=self.app.storage)
                    self.app.analyze()
                    return result

                self.app.start("restore", undo)
            else:
                self.json({"erro": _("Route not found")}, 404)
                return
            self.json({"iniciado": True}, 202)
        except (ValueError, OSError, TypeError) as error:
            self.json({"erro": str(error)}, 400)


def existing_folder(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError(_("Choose an existing folder using its full path"))
    path = Path(value).expanduser()
    try:
        if path.is_absolute() and path.resolve(strict=True).is_dir():
            return path.resolve()
    except OSError:
        pass
    raise ValueError(_("Choose an existing folder using its full path"))


def list_folders(value):
    path = existing_folder(value)
    try:
        with os.scandir(path) as entries:
            names = [e.name for e in entries if not e.name.startswith(".") and e.is_dir(follow_symlinks=False)]
    except OSError as error:
        raise ValueError(_("Could not read this folder: {error}", error=error.strerror or error)) from error
    names.sort(key=str.casefold)
    return {
        "path": str(path),
        "parent": str(path.parent) if path.parent != path else None,
        "folders": [{"name": name, "path": str(path / name)} for name in names[:1000]],
    }


def open_library(server, value):
    folder = existing_folder(value)
    if not LIBRARY_SWITCH.acquire(blocking=False):
        raise ValueError(_("Another folder is already opening"))
    try:
        app = server.app
        if folder == app.root:
            return app
        with app.lock:
            # A scan only reads files and can finish unattended; anything else needs this library.
            scanning = app.job and app.job["status"] == "running" and app.job["type"] == "analysis"
            running = app.job and app.job["status"] == "running" and not scanning
            queued = any(entry["estado"] in ("aguardando", "baixando") for entry in app.download_queue)
            if running or queued or app.playlist_busy or app.policy_busy or app.lyrics_busy:
                raise ValueError(_("Wait for the current operation to finish before opening another folder"))
            # From here on, this library refuses new work, even from requests already in flight.
            app.closed = True
        try:
            opened = App(folder, app.storage)
            opened.token, opened.fixed_folder = app.token, app.fixed_folder
            if opened.plan is None:
                opened.start("analysis", opened.analyze)
        except Exception:
            with app.lock:
                app.closed = False
            raise
        server.app = opened
        app.global_search.close()
        return opened
    finally:
        LIBRARY_SWITCH.release()


def serve(
    root: Path, port: int = 8765, storage: Path | None = None, open_browser: bool = False, fixed_folder: bool = False
):
    app = App(root, storage)
    app.fixed_folder = fixed_folder
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = app
    print(f"Acervo · http://127.0.0.1:{port}", flush=True)
    print(_("Library: {folder}", folder=app.root), flush=True)
    if app.plan is None:
        app.start("analysis", app.analyze)
    if open_browser:
        import webbrowser

        threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
        app = server.app
        while app.writing():
            print(_("\nWaiting for the current write to finish so files are not left incomplete…"), flush=True)
            try:
                app.worker.join()
            except KeyboardInterrupt:
                continue
        print(_("\nInterface closed."))
    finally:
        server.server_close()
        server.app.global_search.close()
