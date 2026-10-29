"""Leitura, gravação validada e transações reversíveis de metadados."""

from __future__ import annotations

from contextlib import contextmanager
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TALB, TCON, TXXX


ORIGINAL_MP3 = "BibliotecaMusical:AlbumAnterior"
ORIGINAL_FLAC = "BIBLIOTECA_ALBUM_ANTERIOR"


def sha256(path: Path, start: int = 0) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        handle.seek(start)
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".json-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        sync_dir(path.parent)
    finally:
        Path(tmp).unlink(missing_ok=True)


def id3_size(handle) -> int:
    handle.seek(0)
    header = handle.read(10)
    if header[:3] != b"ID3":
        return 0
    if len(header) != 10 or any(v & 128 for v in header[6:10]):
        raise ValueError("Cabeçalho ID3 inválido")
    return 10 + sum(v << (7 * (3 - i)) for i, v in enumerate(header[6:10]))


def freeze(value):
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    if isinstance(value, (list, tuple)):
        return [freeze(v) for v in value]
    if isinstance(value, dict):
        return {str(k): freeze(v) for k, v in sorted(value.items())}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "__dict__"):
        return {"type": type(value).__name__, "attrs": freeze(vars(value))}
    return str(value)


def integrity(path: Path) -> dict:
    """Estado semântico completo das tags e hash do fluxo comprimido original."""
    if path.suffix.casefold() == ".mp3":
        tags = ID3(path, translate=False)
        if tags.version[:2] not in ((2, 3), (2, 4)):
            raise ValueError("Gravação restrita a ID3v2.3/v2.4; arquivo encaminhado para revisão")
        with path.open("rb") as handle:
            offset = id3_size(handle)
        return {
            "formato": "MP3", "versao": list(tags.version),
            "tags": {k: freeze(vars(v)) for k, v in tags.items()},
            "desconhecidos": freeze(tags.unknown_frames),
            "audio_sha256": sha256(path, offset),
        }
    if path.suffix.casefold() != ".flac":
        raise ValueError("Formato não suportado")
    audio = FLAC(path)
    with path.open("rb") as handle:
        offset = id3_size(handle)
        handle.seek(0)
        prefix = hashlib.sha256(handle.read(offset)).hexdigest()
        handle.seek(offset)
        if handle.read(4) != b"fLaC":
            raise ValueError("Cabeçalho FLAC inválido")
        blocks = []
        while True:
            header = handle.read(4)
            if len(header) != 4:
                raise ValueError("Bloco FLAC truncado")
            kind = header[0] & 127
            size = int.from_bytes(header[1:], "big")
            data = handle.read(size)
            if len(data) != size:
                raise ValueError("Bloco FLAC truncado")
            if kind not in (1, 4):  # padding e Vorbis Comments podem mudar
                blocks.append([kind, hashlib.sha256(data).hexdigest()])
            if header[0] & 128:
                break
        offset = handle.tell()
    return {
        "formato": "FLAC", "versao": None,
        "tags": {k: list(v) for k, v in (audio.tags or {}).items()},
        "vendor": getattr(audio.tags, "vendor", None), "blocos": blocks,
        "prefixo_id3_sha256": prefix,
        "audio_sha256": sha256(path, offset),
    }


def previous_album(path: Path) -> list[str]:
    if path.suffix.casefold() == ".mp3":
        tags = ID3(path, translate=False)
        return [str(v) for f in tags.getall("TXXX:" + ORIGINAL_MP3) for v in f.text]
    return list(FLAC(path).get(ORIGINAL_FLAC, []))


def write_validated(path: Path, changes: dict) -> dict:
    """Só deve receber uma cópia temporária. Aborta se algo não aprovado mudar."""
    if not changes or set(changes) - {"genero", "album"}:
        raise ValueError("Campos de alteração não suportados")
    for change in changes.values():
        proposed = change["proposto"]
        if not isinstance(proposed, list) or not proposed or any(not isinstance(v, str) or not v.strip() or "\x00" in v for v in proposed):
            raise ValueError("Valor proposto inválido")
    before = integrity(path)
    allowed = set()
    if before["formato"] == "MP3":
        tags = ID3(path, translate=False)
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 128))
            tail = handle.read()
        v1 = tail if len(tail) == 128 and tail[:3] == b"TAG" else b""
        for field, change in changes.items():
            key, frame = ("TCON", TCON) if field == "genero" else ("TALB", TALB)
            old = [str(v) for f in tags.getall(key) for v in (f.genres if field == "genero" else f.text)]
            if old != change["atual"]:
                raise ValueError(f"Valor atual de {field} difere do plano")
            tags.add(frame(encoding=1 if tags.version[1] == 3 else 3, text=change["proposto"]))
            allowed.add(key)
            if field == "album":
                companion = "TXXX:" + ORIGINAL_MP3
                if companion in tags:
                    raise ValueError("Tag AlbumAnterior já existe; revisão necessária")
                tags.add(TXXX(encoding=1 if tags.version[1] == 3 else 3, desc=ORIGINAL_MP3, text=old))
                allowed.add(companion)
        tags.save(path, v2_version=before["versao"][1], v1=0, v23_sep=None)
        if v1:
            with path.open("ab") as handle:
                handle.write(v1)
    else:
        audio = FLAC(path)
        if audio.tags is None:
            audio.add_tags()
        for field, change in changes.items():
            key = "genre" if field == "genero" else "album"
            old = list(audio.get(key, []))
            if old != change["atual"]:
                raise ValueError(f"Valor atual de {field} difere do plano")
            audio[key] = change["proposto"]
            allowed.add(key)
            if field == "album":
                if ORIGINAL_FLAC in audio:
                    raise ValueError("Tag AlbumAnterior já existe; revisão necessária")
                audio[ORIGINAL_FLAC] = old
                allowed.add(ORIGINAL_FLAC.lower())
        audio.save(path, deleteid3=False)
    after = integrity(path)
    for field in before.keys() | after.keys():
        if field != "tags" and before.get(field) != after.get(field):
            raise ValueError(f"Validação recusou mudança em {field}")
    for key in before["tags"].keys() | after["tags"].keys():
        if key not in allowed and before["tags"].get(key) != after["tags"].get(key):
            raise ValueError(f"Validação recusou mudança na tag {key}")
    # Confere os valores gravados, incluindo a preservação da edição.
    if after["formato"] == "MP3":
        saved = ID3(path, translate=False)
        for field, change in changes.items():
            key = "TCON" if field == "genero" else "TALB"
            actual = [str(v) for f in saved.getall(key) for v in (f.genres if field == "genero" else f.text)]
            if actual != change["proposto"]:
                raise ValueError(f"Não foi possível confirmar a gravação de {field}")
    else:
        saved = FLAC(path)
        for field, change in changes.items():
            if list(saved.get("genre" if field == "genero" else "album", [])) != change["proposto"]:
                raise ValueError(f"Não foi possível confirmar a gravação de {field}")
    if "album" in changes and previous_album(path) != changes["album"]["atual"]:
        raise ValueError("Informação da edição não foi preservada")
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    return after


def secure_file(root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise ValueError(f"Caminho inválido ou simbólico: {path}")
    path.relative_to(root)
    if not path.is_file() or path.suffix.casefold() not in (".mp3", ".flac"):
        raise ValueError(f"Arquivo inválido: {path}")
    if path.stat().st_nlink > 1:
        raise ValueError(f"Arquivo com hard links exige revisão: {path}")
    return path


@contextmanager
def library_lock(root: Path):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def load_plan(path: Path) -> dict:
    expected = path.with_suffix(".sha256").read_text().strip()
    if sha256(path) != expected:
        raise ValueError("O plano foi alterado; gere outra análise")
    plan = json.loads(path.read_text())
    if plan.get("schema") != 1:
        raise ValueError("Versão de plano incompatível")
    return plan


def approved_changes(plan: dict, csv_path: Path) -> dict:
    known = {c["id"]: (r, c) for r in plan["arquivos"] for c in r["alteracoes"]}
    selected = {}
    seen = set()
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            ident = row.get("id")
            if ident not in known or ident in seen:
                raise ValueError("Aprovações contêm ID desconhecido ou repetido")
            seen.add(ident)
            track, change = known[ident]
            fixed = {"arquivo": track["arquivo"], "campo": change["campo"], "atual": json.dumps(change["atual"], ensure_ascii=False), "proposto": json.dumps(change["proposto"], ensure_ascii=False)}
            if any(row.get(k) != v for k, v in fixed.items()):
                raise ValueError("Edite apenas a coluna aprovar; valores diferem do plano")
            answer = row.get("aprovar", "").strip().casefold()
            if answer not in ("", "nao", "não", "no", "sim", "yes"):
                raise ValueError("Use sim ou não na coluna aprovar")
            if answer in ("sim", "yes"):
                selected.setdefault(track["arquivo"], {"sha256": track["sha256"], "alteracoes": {}})["alteracoes"][change["campo"]] = change
    return selected


def durable_copy(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    with destination.open("rb") as handle:
        os.fsync(handle.fileno())
    sync_dir(destination.parent)


def apply_plan(plan_path: Path, approvals: Path, backup_root: Path) -> dict:
    plan = load_plan(plan_path)
    root = Path(plan["biblioteca"]).resolve(strict=True)
    selected = approved_changes(plan, approvals)
    if not selected:
        raise ValueError("Nenhuma linha aprovada; a biblioteca continua sem alterações")
    backup_root = backup_root.expanduser().resolve()
    if backup_root == root or root in backup_root.parents:
        raise ValueError("Backups devem ficar fora da biblioteca")
    with library_lock(root):
        for name, entry in selected.items():
            path = secure_file(root, name)
            if sha256(path) != entry["sha256"]:
                raise ValueError(f"Arquivo mudou desde a análise: {path}")
        total = sum(Path(name).stat().st_size for name in selected)
        largest = max(Path(name).stat().st_size for name in selected)
        backup_root.mkdir(parents=True, exist_ok=True)
        same_device = backup_root.stat().st_dev == root.stat().st_dev
        if shutil.disk_usage(backup_root).free < total + (largest if same_device else 0):
            raise ValueError("Espaço insuficiente para backups completos e arquivo temporário")
        if shutil.disk_usage(root).free < largest:
            raise ValueError("Espaço insuficiente na biblioteca para arquivo temporário")
        transaction = backup_root / uuid.uuid4().hex
        transaction.mkdir(mode=0o700)
        originals = transaction / "originais"
        originals.mkdir()
        manifest_path = transaction / "manifesto.json"
        manifest = {"schema": 1, "biblioteca": str(root), "plano_sha256": sha256(plan_path), "estado": "preparando_backups", "arquivos": []}
        atomic_json(manifest_path, manifest)
        try:
            # Todos os backups são confirmados antes da primeira gravação na coleção.
            for index, (name, entry) in enumerate(selected.items()):
                source = secure_file(root, name)
                backup = originals / (f"{index:06d}" + source.suffix)
                durable_copy(source, backup)
                if sha256(backup) != entry["sha256"] or sha256(source) != entry["sha256"]:
                    raise ValueError(f"Backup não corresponde ao plano: {source}")
                manifest["arquivos"].append({"arquivo": name, "backup": str(backup.relative_to(transaction)), "antes_sha256": entry["sha256"], "depois_sha256": None, "estado": "backup_confirmado", "alteracoes": entry["alteracoes"]})
                atomic_json(manifest_path, manifest)
            manifest["estado"] = "aplicando"
            atomic_json(manifest_path, manifest)
            for entry in manifest["arquivos"]:
                source = secure_file(root, entry["arquivo"])
                fd, temp_name = tempfile.mkstemp(prefix=".biblioteca-", suffix=source.suffix, dir=source.parent)
                os.close(fd)
                temp = Path(temp_name)
                try:
                    durable_copy(transaction / entry["backup"], temp)
                    entry["verificacao"] = write_validated(temp, entry["alteracoes"])
                    # Frames de capa já foram verificados; evita duplicar tags no manifesto.
                    entry["verificacao"] = {k: v for k, v in entry["verificacao"].items() if k in ("audio_sha256", "formato", "versao")}
                    entry["depois_sha256"] = sha256(temp)
                    entry["estado"] = "pronto_para_substituir"
                    atomic_json(manifest_path, manifest)
                    if sha256(source) != entry["antes_sha256"]:
                        raise ValueError(f"Arquivo alterado por outro programa: {source}")
                    os.replace(temp, source)
                    sync_dir(source.parent)
                    entry["estado"] = "aplicado"
                    atomic_json(manifest_path, manifest)
                finally:
                    temp.unlink(missing_ok=True)
            manifest["estado"] = "concluido"
        except Exception as error:
            manifest["estado"] = "interrompido"
            manifest["erro"] = f"{type(error).__name__}: {error}"
            atomic_json(manifest_path, manifest)
            raise RuntimeError(f"Execução interrompida. Consulte/restaure {manifest_path}: {error}") from error
        atomic_json(manifest_path, manifest)
        return {"manifesto": str(manifest_path), "arquivos_alterados": len(selected), "estado": manifest["estado"]}


def restore(manifest_path: Path) -> dict:
    manifest_path = manifest_path.resolve(strict=True)
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != 1:
        raise ValueError("Manifesto incompatível")
    transaction = manifest_path.parent
    root = Path(manifest["biblioteca"]).resolve(strict=True)
    with library_lock(root):
        work = []
        # Recusa todo o lote antes de escrever se houver edições posteriores.
        for entry in manifest["arquivos"]:
            source = secure_file(root, entry["arquivo"])
            backup = (transaction / entry["backup"]).resolve(strict=True)
            backup.relative_to(transaction)
            if sha256(backup) != entry["antes_sha256"]:
                raise ValueError(f"Backup danificado: {backup}")
            current = sha256(source)
            if current == entry["antes_sha256"]:
                continue
            if current != entry["depois_sha256"]:
                raise ValueError(f"Há edições posteriores; restauração recusada: {source}")
            work.append((entry, source, backup))
        for entry, source, backup in work:
            fd, name = tempfile.mkstemp(prefix=".restaurar-", suffix=source.suffix, dir=source.parent)
            os.close(fd)
            temp = Path(name)
            try:
                durable_copy(backup, temp)
                if sha256(temp) != entry["antes_sha256"] or sha256(source) != entry["depois_sha256"]:
                    raise ValueError(f"Arquivo ou backup mudou durante a restauração: {source}")
                os.replace(temp, source)
                sync_dir(source.parent)
                entry["estado"] = "restaurado"
                atomic_json(manifest_path, manifest)
            finally:
                temp.unlink(missing_ok=True)
        manifest["estado"] = "restaurado"
        atomic_json(manifest_path, manifest)
    return {"estado": "restaurado", "arquivos_restaurados": len(work), "manifesto": str(manifest_path)}
