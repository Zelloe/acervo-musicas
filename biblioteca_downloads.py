"""Catálogo, download em área de revisão e importação explícita de músicas."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

from mutagen.id3 import ID3, ID3NoHeaderError

from biblioteca_core import atomic_json, metadata_values, secure_file, sha256, write_validated
from biblioteca_importacao import import_reviewed, new_destination
import protecoes
from biblioteca_plano import BASE, propose, rules
from diagnostico_biblioteca import album_base, normalized, read_track


def clean_log(text: str) -> str:
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"(?i)(bearer\s+|access_token[=:]\s*|client_secret[=:]\s*)[^\s,]+", r"\1[oculto]", text)
    return text[-8000:]


def title_key(value: str) -> str:
    value = normalized(value)
    # Só elimina qualificadores de remasterização; live/demo/mix continuam distintos.
    return re.sub(r"\s*(?:-\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?|[\[(](?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?[\])])\s*$", "", value).strip()


def validate_query(query: str, provider: str) -> str:
    if not isinstance(query, str) or not query.strip() or len(query) > 1200:
        raise ValueError("Informe um link de música/álbum ou uma busca curta")
    query = query.strip()
    if query.startswith("-") or any(ord(c) < 32 for c in query):
        raise ValueError("Consulta inválida")
    if "://" in query:
        parsed = urlsplit(query)
        hosts = {"open.spotify.com", "youtube.com", "www.youtube.com", "music.youtube.com", "youtu.be"}
        if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password:
            raise ValueError("Use um link HTTPS público do Spotify ou YouTube")
        if provider == "yt-dlp" and parsed.hostname == "open.spotify.com":
            raise ValueError("O yt-dlp recebe links do YouTube; use spotDL para links Spotify")
        if parsed.hostname == "open.spotify.com" and not re.search(r"/(?:track|album|playlist)/[A-Za-z0-9]+", parsed.path):
            raise ValueError("Use uma faixa, álbum ou playlist; artistas inteiros não são baixados em lote")
    elif provider == "yt-dlp":
        raise ValueError("Informe um link público do YouTube para usar yt-dlp")
    elif "/" in query or "\\" in query or query.endswith(".spotdl"):
        raise ValueError("Use uma busca por nome ou um link público; caminhos locais não são consultas")
    return query


def tool_status() -> dict:
    return {name: {"disponivel": bool(shutil.which(name)), "executavel": shutil.which(name)} for name in ("spotdl", "yt-dlp", "ffmpeg", "deno")}


class CommandRunner:
    def __init__(self):
        self.cancelled = threading.Event()

    def run(self, args: list[str], cwd: Path, log: Path, timeout: int = 180):
        if self.cancelled.is_set():
            raise RuntimeError("Operação cancelada")
        with log.open("a", encoding="utf-8") as output:
            process = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + timeout
            stopped = None
            while process.poll() is None:
                if self.cancelled.is_set() or time.monotonic() > deadline:
                    stopped = "Operação cancelada" if self.cancelled.is_set() else "Tempo de espera esgotado; revise o registro ou tente outra fonte"
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    break
                time.sleep(.2)
            if stopped:
                raise RuntimeError(stopped)
            if process.returncode:
                raise RuntimeError("A ferramenta não concluiu a operação. " + clean_log(log.read_text(errors="replace"))[-1200:])


def safe_filename(artist: str, title: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", f"{artist} - {title}")
    text = " ".join(text.split()).strip(" .")
    return (text.encode("utf-8")[:190].decode("utf-8", errors="ignore").rstrip(" .") or "Faixa") + ".mp3"


def changes_for(current: dict, desired: dict, replace: bool, force_genre: bool, mp3_version: int | None = None, protected: frozenset = frozenset()) -> dict:
    changes = {}
    for field, values in desired.items():
        if field in protected:
            continue
        old = current.get(field, [])
        if field == "ano" and mp3_version == 3:
            values = [v[:4] for v in values]
        if not values or old == values:
            continue
        if field == "genero" and any(v.strip() for v in old) and not force_genre:
            continue
        if not replace and field not in ("album", "genero") and any(v.strip() for v in old):
            continue
        changes[field] = {"atual": old, "proposto": values}
    return changes


class Downloads:
    def __init__(self, root: Path, storage: Path, progress=None, runner=None):
        self.root = root.resolve(strict=True)
        self.storage = storage.resolve()
        if self.storage == self.root or self.root in self.storage.parents:
            raise ValueError("Os dados e downloads de revisão devem ficar fora da biblioteca")
        self.folder = self.storage / ".biblioteca/downloads"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.progress = progress or (lambda *args: None)
        self.runner = runner or CommandRunner()

    def path(self, ident: str) -> Path:
        if not isinstance(ident, str) or not re.fullmatch(r"[a-f0-9]{32}", ident):
            raise ValueError("Lote inválido")
        path = self.folder / ident
        if path.is_symlink() or path.resolve() != path:
            raise ValueError("Lote simbólico não permitido")
        return path

    def load(self, ident: str) -> dict:
        batch = json.loads((self.path(ident) / "lote.json").read_text())
        if batch.get("biblioteca") != str(self.root):
            raise ValueError("O lote pertence a outra biblioteca")
        return batch

    def save(self, batch: dict):
        atomic_json(self.path(batch["id"]) / "lote.json", batch)

    def list(self) -> list[dict]:
        batches = []
        for path in self.folder.glob("*/lote.json"):
            try:
                batch = self.load(path.parent.name)
                batches.append({k: batch[k] for k in ("id", "criado_em", "consulta", "ferramenta", "modo", "estado")})
            except (OSError, ValueError, KeyError):
                continue
        return sorted(batches, key=lambda b: b["criado_em"], reverse=True)

    def catalog_command(self, provider: str, query: str, path: Path) -> list[str]:
        executable = shutil.which(provider)
        if not executable:
            raise ValueError(f"{provider} não está instalado")
        if provider == "spotdl":
            # O save tradicional reconsulta cada faixa e suas letras; o adaptador
            # usa o catálogo do próprio spotDL, sem essa repetição.
            try:
                first = Path(executable).resolve().open().readline().strip()
                interpreter = shlex.split(first[2:]) if first.startswith("#!") else []
            except (UnicodeError, OSError):
                interpreter = []
            if interpreter and any("python" in Path(v).name for v in interpreter):
                return interpreter + [str(BASE / "spotdl_catalogo.py"), query, str(path / "catalogo.spotdl")]
            return [executable, "save", query, "--save-file", str(path / "catalogo.spotdl"), "--lyrics", "--threads", "1", "--max-retries", "1", "--simple-tui"]
        return [executable, "--ignore-config", "--skip-download", "--flat-playlist", "--dump-single-json", "--playlist-end", "200", "--socket-timeout", "20", "--retries", "1", query]

    def resolve_catalog(self, provider: str, query: str, path: Path) -> list[dict]:
        command = self.catalog_command(provider, query, path)
        log = path / "catalogo.log"
        self.runner.run(command, path, log, timeout=180)
        if provider == "spotdl":
            return json.loads((path / "catalogo.spotdl").read_text())
        # yt-dlp imprime JSON em stdout; stderr de avisos pode antecedê-lo.
        lines = log.read_text(errors="replace").splitlines()
        data = next((json.loads(line) for line in reversed(lines) if line.startswith("{")), None)
        if not data:
            raise ValueError("O yt-dlp não retornou um catálogo válido")
        rows = data.get("entries") or [data]
        result = []
        for index, row in enumerate(rows):
            if not row:
                continue
            artists = row.get("artists") or ([row["artist"]] if row.get("artist") else [])
            url = row.get("webpage_url") or row.get("url")
            if not isinstance(url, str) or not url.startswith("https://"):
                url = "https://www.youtube.com/watch?v=" + row["id"]
            result.append({"name": row.get("track") or row.get("title"), "artists": artists, "artist": artists[0] if artists else "", "album_name": row.get("album") or "", "album_artist": row.get("album_artist") or (artists[0] if artists else ""), "duration": row.get("duration"), "year": row.get("release_year"), "date": str(row.get("release_year") or ""), "track_number": row.get("track_number") or index+1, "tracks_count": len(rows), "disc_number": row.get("disc_number") or 1, "disc_count": 1, "genres": row.get("genres") or [], "url": url, "song_id": row.get("id"), "cover_url": row.get("thumbnail")})
        return result

    def metadata(self, raw: dict, options: dict) -> tuple[dict, str]:
        artist = options.get("artista") or raw.get("artist") or ""
        artists = [artist] if options.get("artista") else raw.get("artists") or ([artist] if artist else [])
        original_album = raw.get("album_name") or ""
        album = options.get("album") or (album_base(original_album) if options.get("unificar_album", True) else original_album)
        metadata = {"titulo": [str(raw.get("name") or "")], "artista": artists, "artista_album": [options.get("artista_album") or raw.get("album_artist") or artist], "album": [album] if album else [], "ano": [str(options.get("ano") or raw.get("date") or raw.get("year") or "")], "faixa": [str(raw.get("track_number") or "")], "disco": [str(raw.get("disc_number") or "1")], "genero": []}
        metadata = {k: [str(v) for v in values if str(v).strip()] for k, values in metadata.items()}
        origin = "sem identificação"
        if options.get("genero"):
            metadata["genero"] = [options["genero"]]
            origin = "gênero escolhido nesta revisão"
        else:
            probe = {**metadata, "arquivo": ""}
            changes, _ = propose(probe, rules(BASE / "generos_artistas.csv"), [])
            if changes:
                metadata["genero"] = changes[0]["proposto"]
                origin = changes[0]["fonte"]
            elif raw.get("genres"):
                genres = [v for v in raw["genres"] if normalized(v) not in ("rock", "metal", "pop")]
                metadata["genero"] = [str((genres or raw["genres"])[0]).title()]
                origin = "metadados da fonte; requer revisão"
        return metadata, origin

    def matches(self, metadata: dict, seconds, tracks: list[dict]) -> list[dict]:
        result = []
        artist = normalized(" / ".join(metadata["artista_album"] or metadata["artista"]))
        title = title_key(" ".join(metadata["titulo"]))
        album = normalized(album_base(" ".join(metadata["album"])))
        for track in tracks:
            if title_key(" ".join(track["titulo"])) != title:
                continue
            local_artist = normalized(" / ".join(track["artista_album"] or track["artista"]))
            if local_artist != artist or not album or normalized(album_base(" ".join(track["album"]))) != album:
                continue
            path = secure_file(self.root, track["arquivo"])
            current = metadata_values(path)
            delta = abs(float(seconds)-track["duracao_segundos"]) if seconds else None
            version = ID3(path, translate=False).version[1] if path.suffix.casefold() == ".mp3" else None
            result.append({"id": track["id"], "arquivo": str(path), "sha256": sha256(path), "tamanho_bytes": path.stat().st_size, "formato": track["formato"], "metadados": current, "versao_id3": version, "duracao_segundos": track["duracao_segundos"], "diferenca_duracao": round(delta, 3) if delta is not None else None, "duracao_compativel": delta is not None and delta <= max(3, float(seconds)*.02)})
        return result

    def prepare(self, options: dict, tracks: list[dict], catalog: list[dict] | None = None) -> dict:
        provider = options.get("ferramenta", "spotdl")
        if provider not in ("spotdl", "yt-dlp"):
            raise ValueError("Ferramenta de download inválida")
        query = validate_query(options.get("consulta", ""), provider)
        policy = protecoes.load(self.storage, self.root)
        mode = options.get("modo", "faltantes")
        if mode not in ("faltantes", "completo", "metadados"):
            raise ValueError("Modo de download inválido")
        options = {k: str(options.get(k) or "").strip() for k in ("artista", "artista_album", "album", "ano", "genero")} | {"forcar_genero": bool(options.get("forcar_genero")), "unificar_album": bool(options.get("unificar_album", True)), "bitrate": options.get("bitrate", "auto"), "destino": options.get("destino")}
        if any(len(options[k]) > 300 or any(ord(c)<32 for c in options[k]) for k in ("artista", "artista_album", "album", "ano", "genero")):
            raise ValueError("Metadados informados inválidos")
        if options["bitrate"] not in ("auto", "128k", "192k", "256k", "320k"):
            raise ValueError("Bitrate inválido")
        if options["ano"] and not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", options["ano"]):
            raise ValueError("Informe o ano como AAAA ou uma data ISO")
        parents = Counter(str(Path(t["arquivo"]).parent) for t in tracks)
        destination = options["destino"] or (parents.most_common(1)[0][0] if parents else str(self.root))
        new_destination(self.root, destination, ".acervo-teste-destino.mp3")
        options["destino"] = destination
        ident = uuid.uuid4().hex
        path = self.path(ident)
        path.mkdir()
        (path / "audio").mkdir()
        batch = {"schema": 1, "id": ident, "biblioteca": str(self.root), "criado_em": datetime.now(timezone.utc).isoformat(), "consulta": query, "ferramenta": provider, "modo": mode, "opcoes": options, "estado": "consultando", "faixas": [], "avisos": []}
        self.save(batch)
        try:
            self.runner.cancelled.clear()
            self.progress("Consultando o catálogo da fonte…", None)
            catalog = catalog if catalog is not None else self.resolve_catalog(provider, query, path)
            if not isinstance(catalog, list) or not catalog or len(catalog)>200 or not all(isinstance(v, dict) for v in catalog):
                raise ValueError("O catálogo precisa conter de 1 a 200 faixas válidas")
            if options["album"] and len({normalized(album_base(r.get("album_name") or "")) for r in catalog}) > 1:
                raise ValueError("A fonte contém vários álbuns; escolha um álbum por vez para impor um nome único")
            reserved = set()
            for index, raw in enumerate(catalog):
                metadata, genre_origin = self.metadata(raw, options)
                matches = self.matches(metadata, raw.get("duration"), tracks)
                filename = safe_filename(" / ".join(metadata["artista"]), " ".join(metadata["titulo"]))
                stem = Path(filename).stem
                suffix = 2
                while filename in reserved or os.path.lexists(Path(destination)/filename):
                    filename = f"{stem} ({suffix}).mp3"
                    suffix += 1
                reserved.add(filename)
                row = {"id": uuid.uuid4().hex, "fonte": raw, "metadados": metadata, "origem_genero": genre_origin, "correspondencias": matches, "arquivo_novo": str(Path(destination)/filename), "estado": "catalogado", "avisos": [], "alteracoes": {}}
                if not metadata["genero"]:
                    row["avisos"].append("Gênero pendente de identificação")
                if len(matches)>1:
                    row["avisos"].append("Mais de uma versão local: escolha o alvo na revisão")
                for local in matches:
                    locked = protecoes.protected(policy, self.root, local["arquivo"])
                    local["campos_protegidos"] = sorted(locked, key=protecoes.FIELDS.index)
                    local["alteracoes"] = changes_for(local["metadados"], metadata, False, options["forcar_genero"], local["versao_id3"], locked)
                    local["alteracoes_substituicao"] = changes_for(local["metadados"], metadata, True, options["forcar_genero"], local["versao_id3"], locked)
                    if not local["duracao_compativel"]:
                        row["avisos"].append("A duração difere da referência: confira a versão antes de substituir")
                batch["faixas"].append(row)
                self.progress(f"Comparando {index+1} de {len(catalog)} faixas…", round((index+1)/len(catalog)*100))
            if "/album/" in query and catalog[0].get("tracks_count") and len(catalog) != int(catalog[0]["tracks_count"]):
                batch["avisos"].append("O catálogo retornou menos faixas do que o total informado pela fonte")
            batch["estado"] = "pronto"
        except Exception as error:
            batch.update(estado="erro", erro=str(error))
            self.save(batch)
            raise
        self.save(batch)
        return {"lote": ident, "faixas": len(batch["faixas"])}

    def file(self, batch_id: str, row_id: str) -> Path:
        batch = self.load(batch_id)
        row = next((r for r in batch["faixas"] if r["id"] == row_id), None)
        if not row or row.get("estado") != "baixado":
            raise ValueError("Download ainda não está pronto")
        path = self.path(batch_id) / "audio" / (row_id + ".mp3")
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ValueError("Arquivo de download inválido")
        return path

    def download(self, ident: str, ids: list[str]) -> dict:
        batch = self.load(ident)
        if batch["modo"] == "metadados":
            raise ValueError("Este lote foi preparado apenas para unificar metadados")
        known = {r["id"]: r for r in batch["faixas"]}
        if not ids or len(ids)!=len(set(ids)) or not set(ids)<=known.keys():
            raise ValueError("Seleção de download inválida")
        if batch["modo"] == "faltantes" and any(known[i]["correspondencias"] for i in ids):
            raise ValueError("Há faixas já existentes; use o modo álbum completo para um redownload explícito")
        if batch.get("importacao"):
            raise ValueError("Este lote já foi importado; prepare outro para novas alterações")
        path = self.path(ident)
        self.runner.cancelled.clear()
        batch["estado"] = "baixando"
        self.save(batch)
        for index, row_id in enumerate(ids):
            row = known[row_id]
            if row["estado"] == "baixado":
                continue
            try:
                self.progress(f"Baixando {index+1} de {len(ids)} · {' / '.join(row['metadados']['titulo'])}", round(index/len(ids)*100))
                output = path / "audio" / (row_id + ".mp3")
                log = path / (row_id + ".log")
                # Sobras de uma tentativa interrompida seriam reaproveitadas
                # pelas opções de não sobrescrever das ferramentas.
                for leftover in (path / "audio").glob(row_id + ".*"):
                    leftover.unlink()
                if batch["ferramenta"] == "spotdl":
                    raw = dict(row["fonte"])
                    raw["genres"] = row["metadados"]["genero"]
                    # O catálogo simples pode omitir ISRC; Mutagen rejeita None
                    # na gravação feita pelo spotDL. Ausência não vira um ID falso.
                    for field in ("isrc", "publisher", "date"):
                        raw[field] = raw.get(field) or ""
                    single = path / (row_id + ".spotdl")
                    single.write_text(json.dumps([raw], ensure_ascii=False))
                    # Templates absolutos são sanitizados pelo spotDL (inclusive
                    # pastas que começam com ponto). Um caminho relativo fixo
                    # mantém o download estritamente dentro do lote de revisão.
                    template = str(Path("audio") / output.with_suffix(".{output-ext}").name)
                    command = [shutil.which("spotdl") or "spotdl", "download", str(single), "--output", template, "--format", "mp3", "--bitrate", batch["opcoes"]["bitrate"], "--overwrite", "skip", "--threads", "1", "--lyrics", "--max-retries", "1", "--simple-tui", "--print-errors"]
                else:
                    url = validate_query(row["fonte"]["url"], "yt-dlp")
                    quality = "0" if batch["opcoes"]["bitrate"]=="auto" else batch["opcoes"]["bitrate"]
                    command = [shutil.which("yt-dlp") or "yt-dlp", "--ignore-config", "--no-playlist", "--no-overwrites", "--socket-timeout", "20", "--retries", "2", "-f", "bestaudio/best", "-x", "--audio-format", "mp3", "--audio-quality", quality, "--embed-metadata", "--embed-thumbnail", "--convert-thumbnails", "jpg", "-o", str(output.with_suffix(".%(ext)s")), url]
                self.runner.run(command, path, log, timeout=600)
                if not output.is_file():
                    raise ValueError("A ferramenta terminou sem produzir um MP3 válido; consulte o registro")
                self.runner.run([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(output), "-map", "0:a:0", "-f", "null", "-"], path, log, timeout=180)
                try:
                    ID3(output)
                except ID3NoHeaderError:
                    ID3().save(output, v2_version=4, v1=0)
                current = metadata_values(output)
                version = ID3(output, translate=False).version[1]
                changes = changes_for(current, row["metadados"], True, True, version)
                if changes:
                    write_validated(output, changes)
                actual = read_track(output)
                row.update(estado="baixado", sha256=sha256(output), real=actual)
                if row["fonte"].get("duration") and abs(actual["duracao_segundos"]-float(row["fonte"]["duration"]))>max(3,float(row["fonte"]["duration"])*.02):
                    row["avisos"].append("O áudio baixado difere em duração do catálogo: ouça antes de importar")
            except Exception as error:
                row.update(estado="erro", erro=str(error))
                if self.runner.cancelled.is_set():
                    batch["estado"] = "cancelado"
                    self.save(batch)
                    raise
            self.save(batch)
        batch["estado"] = "revisao"
        self.save(batch)
        self.progress("Downloads preparados para ouvir e revisar",100)
        return {"lote": ident, "baixadas": sum(r["estado"]=="baixado" for r in batch["faixas"]), "erros": sum(r["estado"]=="erro" for r in batch["faixas"])}

    def operations(self, ident: str, selections: list[dict]) -> list[dict]:
        batch = self.load(ident)
        if batch.get("importacao"):
            raise ValueError("Este lote já foi importado")
        known = {r["id"]: r for r in batch["faixas"]}
        if not isinstance(selections, list) or not selections or any(not isinstance(s, dict) or not isinstance(s.get("id"), str) for s in selections):
            raise ValueError("Seleção de importação inválida")
        if len(selections) != len({s.get("id") for s in selections}):
            raise ValueError("Seleção de importação vazia ou repetida")
        operations = []
        for selected in selections:
            row = known.get(selected.get("id"))
            action = selected.get("acao")
            if not row or action not in ("adicionar", "substituir", "metadados"):
                raise ValueError("Operação de importação inválida")
            operation = {"id": row["id"], "acao": action, "alteracoes": {}}
            if action != "metadados":
                source = self.file(ident, row["id"])
                operation.update(origem=str(source), origem_sha256=row["sha256"])
            if action == "adicionar":
                target = Path(row["arquivo_novo"])
                new_destination(self.root, str(target.parent), target.name)
                if not all(row["real"].get(k) for k in ("titulo", "artista", "album")):
                    raise ValueError("Faltam título, artista ou álbum; preencha os metadados e prepare uma nova prévia")
                operation["arquivo"] = str(target)
            else:
                matches = row["correspondencias"]
                target_id = selected.get("alvo") or (matches[0]["id"] if len(matches)==1 else None)
                local = next((m for m in matches if m["id"]==target_id), None)
                if local is None:
                    raise ValueError("Escolha explicitamente a versão local correspondente")
                if action == "substituir" and local["formato"] != "MP3":
                    raise ValueError("Não substituímos FLAC por MP3; use metadados ou adicione como outra versão")
                changes = local["alteracoes_substituicao"] if action=="substituir" else local["alteracoes"]
                if action=="metadados" and not changes:
                    raise ValueError("As tags desta faixa já correspondem à referência")
                operation.update(arquivo=local["arquivo"], antes_sha256=local["sha256"], alteracoes=changes)
            operations.append(operation)
        return operations

    def import_batch(self, ident: str, selections: list[dict]) -> dict:
        operations = self.operations(ident, selections)
        result = import_reviewed(self.root, self.storage/"backups", operations, ident)
        batch = self.load(ident)
        batch.update(importacao=result, estado="importado")
        self.save(batch)
        return result
