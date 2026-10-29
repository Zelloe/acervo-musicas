"""Importação revisada de downloads, substituições e correções de álbum."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from mutagen.id3 import ID3

from biblioteca_core import (
    atomic_json, durable_copy, id3_size, integrity, library_lock,
    secure_file, sha256, sync_dir, write_validated,
)


def compressed_mp3_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        start = id3_size(handle)
        end = path.stat().st_size
        if end >= 128:
            handle.seek(end - 128)
            if handle.read(3) == b"TAG":
                end -= 128
        handle.seek(start)
        remaining = end - start
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("MP3 truncado")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def preserve_original_tags(original: Path, candidate: Path) -> None:
    if original.suffix.casefold() != candidate.suffix.casefold() or candidate.suffix.casefold() != ".mp3":
        raise ValueError("A substituição exige MP3 por MP3; preserve o FLAC e adicione a nova versão separadamente")
    before = integrity(original)
    for key in before["tags"]:
        if any(word in key.casefold() for word in ("replaygain", "acoustid", "fingerprint", "musicbrainz", "rva2", "rva")):
            raise ValueError("O original contém identificação ou medições de áudio; adicione o download como outra versão")
    stream_hash = compressed_mp3_hash(candidate)
    fresh = ID3(candidate, translate=False)
    tags = deepcopy(ID3(original, translate=False))
    # TLEN descreve a duração do áudio; vale a do download, não a do original.
    tags.delall("TLEN")
    for frame in fresh.getall("TLEN"):
        tags.add(deepcopy(frame))
    if not tags.getall("APIC"):
        for frame in fresh.getall("APIC"):
            tags.add(deepcopy(frame))
    with original.open("rb") as handle:
        handle.seek(max(0, original.stat().st_size - 128))
        tail = handle.read()
    tags.save(candidate, v2_version=before["versao"][1], v1=0, v23_sep=None)
    if len(tail) == 128 and tail[:3] == b"TAG":
        with candidate.open("ab") as handle:
            handle.write(tail)
    after = integrity(candidate)
    if stream_hash != compressed_mp3_hash(candidate):
        raise ValueError("O fluxo de áudio do download mudou ao preservar as tags")
    if before["versao"] != after["versao"] or before["desconhecidos"] != after["desconhecidos"]:
        raise ValueError("Não foi possível preservar a estrutura ID3 original")
    if any(after["tags"].get(k) != v for k, v in before["tags"].items() if not k.startswith("TLEN")):
        raise ValueError("Não foi possível preservar todas as tags originais")


def new_destination(root: Path, folder: str, name: str) -> Path:
    directory = Path(folder)
    if not directory.is_absolute() or directory.resolve() != directory or not directory.is_dir():
        raise ValueError("Pasta de destino inválida ou simbólica")
    directory.relative_to(root)
    if Path(name).name != name or name in ("", ".", "..") or Path(name).suffix.casefold() != ".mp3":
        raise ValueError("Nome de arquivo novo inválido")
    path = directory / name
    if os.path.lexists(path):
        raise ValueError(f"O destino já existe; gere outra prévia: {name}")
    return path


def require_hard_links(directory: Path) -> None:
    """Adições são publicadas com link(), que nunca sobrescreve um destino."""
    fd, name = tempfile.mkstemp(prefix=".acervo-import-", dir=directory)
    os.close(fd)
    probe = Path(name)
    linked = probe.with_name(probe.name + "-link")
    try:
        os.link(probe, linked)
    except OSError as error:
        raise ValueError(f"O sistema de arquivos de {directory} não aceita hard links (comum em exFAT/FAT32); adicionar faixas novas ali ainda não é suportado") from error
    finally:
        linked.unlink(missing_ok=True)
        probe.unlink(missing_ok=True)


def import_reviewed(root: Path, backup_root: Path, operations: list[dict], batch_id: str, storage: Path | None = None) -> dict:
    import protecoes
    root = root.resolve(strict=True)
    backup_root = backup_root.resolve()
    if not operations:
        raise ValueError("Selecione ao menos uma operação para aprovar")
    if backup_root == root or root in backup_root.parents:
        raise ValueError("Backups devem ficar fora da biblioteca")
    if len({op["arquivo"] for op in operations}) != len(operations):
        raise ValueError("Duas operações atingem o mesmo arquivo; revise a seleção")
    with library_lock(root):
        policy = protecoes.load(storage or backup_root.parent, root)
        for op in operations:
            target = Path(op["arquivo"])
            if op["acao"] != "adicionar":
                protecoes.require_unprotected(policy, root, str(target), op.get("alteracoes") or {})
            if op["acao"] == "adicionar":
                new_destination(root, str(target.parent), target.name)
            else:
                secure_file(root, str(target))
                if sha256(target) != op["antes_sha256"]:
                    raise ValueError(f"O original mudou desde a prévia: {target.name}")
            if op["acao"] != "metadados":
                source = Path(op["origem"])
                if not source.is_file() or source.is_symlink() or sha256(source) != op["origem_sha256"]:
                    raise ValueError("O download mudou desde a prévia")
        for directory in {Path(op["arquivo"]).parent for op in operations if op["acao"] == "adicionar"}:
            require_hard_links(directory)
        backup_root.mkdir(parents=True, exist_ok=True)
        required = sum(Path(op["arquivo"]).stat().st_size for op in operations if op["acao"] != "adicionar")
        required += sum(Path(op["origem"] if op["acao"] != "metadados" else op["arquivo"]).stat().st_size for op in operations)
        largest = max(Path(op["origem"] if op["acao"] != "metadados" else op["arquivo"]).stat().st_size for op in operations)
        if shutil.disk_usage(backup_root).free < required + largest or shutil.disk_usage(root).free < largest:
            raise ValueError("Espaço insuficiente para preparar a importação e os backups")
        transaction = backup_root / uuid.uuid4().hex
        transaction.mkdir(mode=0o700)
        for name in ("originais", "prontos", "retiradas"):
            (transaction / name).mkdir()
        manifest_path = transaction / "manifesto.json"
        manifest = {"schema": 1, "tipo": "downloads", "lote": batch_id, "biblioteca": str(root), "criado_em": datetime.now(timezone.utc).isoformat(), "estado": "preparando_backups", "arquivos": []}
        atomic_json(manifest_path, manifest)
        try:
            for index, op in enumerate(operations):
                target = Path(op["arquivo"])
                backup = None
                if op["acao"] != "adicionar":
                    backup = transaction / "originais" / f"{index:06d}{target.suffix}"
                    durable_copy(target, backup)
                    if sha256(backup) != op["antes_sha256"]:
                        raise ValueError("O original mudou durante o backup")
                ready = transaction / "prontos" / f"{index:06d}{target.suffix}"
                if op["acao"] == "metadados":
                    durable_copy(backup, ready)
                else:
                    durable_copy(Path(op["origem"]), ready)
                    if op["acao"] == "substituir":
                        preserve_original_tags(backup, ready)
                if op.get("alteracoes"):
                    write_validated(ready, op["alteracoes"])
                else:
                    integrity(ready)
                if backup:
                    protecoes.require_same_protected(policy, root, str(target), backup, ready)
                entry = {"arquivo": str(target), "acao": op["acao"], "antes_sha256": op.get("antes_sha256"), "depois_sha256": sha256(ready), "backup": str(backup.relative_to(transaction)) if backup else None, "pronto": str(ready.relative_to(transaction)), "retirada": f"retiradas/{index:06d}{target.suffix}", "alteracoes": op.get("alteracoes", {}), "estado": "preparado"}
                manifest["arquivos"].append(entry)
                atomic_json(manifest_path, manifest)
            manifest["estado"] = "aplicando"
            atomic_json(manifest_path, manifest)
            for entry in manifest["arquivos"]:
                target = Path(entry["arquivo"])
                if entry["acao"] != "adicionar" and sha256(secure_file(root, str(target))) != entry["antes_sha256"]:
                    raise ValueError("O original foi alterado por outro programa")
                fd, name = tempfile.mkstemp(prefix=".acervo-import-", suffix=target.suffix, dir=target.parent)
                os.close(fd)
                temp = Path(name)
                # Registrado antes da cópia para que a restauração encontre sobras de uma interrupção.
                entry["temporario"] = str(temp)
                atomic_json(manifest_path, manifest)
                try:
                    durable_copy(transaction / entry["pronto"], temp)
                    if sha256(temp) != entry["depois_sha256"]:
                        raise ValueError("A cópia preparada mudou")
                    entry["estado"] = "pronto_para_substituir"
                    atomic_json(manifest_path, manifest)
                    if entry["acao"] == "adicionar":
                        # link() recusa atomicamente destinos que surgiram depois da prévia.
                        os.link(temp, target)
                        temp.unlink()
                    else:
                        if sha256(target) != entry["antes_sha256"]:
                            raise ValueError("O original mudou antes da substituição")
                        os.replace(temp, target)
                    sync_dir(target.parent)
                    entry["estado"] = "aplicado"
                    atomic_json(manifest_path, manifest)
                finally:
                    temp.unlink(missing_ok=True)
            manifest["estado"] = "concluido"
        except Exception as error:
            manifest.update(estado="interrompido", erro=str(error))
            atomic_json(manifest_path, manifest)
            raise RuntimeError(f"Importação interrompida. O histórico permite restaurar: {manifest_path}. {error}") from error
        atomic_json(manifest_path, manifest)
        return {"manifesto": str(manifest_path), "arquivos_alterados": len(operations), "estado": "concluido"}


def restore_import(manifest_path: Path, storage: Path | None = None) -> dict:
    import protecoes
    manifest_path = manifest_path.resolve(strict=True)
    storage = storage or manifest_path.parent.parent.parent
    transaction = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != 1 or manifest.get("tipo") != "downloads":
        raise ValueError("Manifesto de importação inválido")
    root = Path(manifest["biblioteca"]).resolve(strict=True)
    with library_lock(root):
        policy = protecoes.load(storage, root)
        work, notices = [], []
        for index, entry in enumerate(manifest["arquivos"]):
            target = Path(entry["arquivo"])
            target.relative_to(root)
            if target.resolve() != target or target.is_symlink():
                raise ValueError("Destino da restauração inválido")
            temp = Path(entry.get("temporario", "/inexistente"))
            if temp.parent == target.parent and temp.name.startswith(".acervo-import-") and temp.is_file() and not temp.is_symlink():
                if target.is_file() and temp.stat().st_ino == target.stat().st_ino:
                    # Janela entre link() e unlink() em uma interrupção do processo.
                    temp.unlink()
                else:
                    # Cópia interrompida: sai da coleção, mas fica guardada na transação.
                    leftover = transaction / "retiradas" / f"temporario-{index:06d}{temp.suffix}"
                    if not leftover.exists():
                        durable_copy(temp, leftover)
                    temp.unlink()
                    sync_dir(target.parent)
            # Entradas nunca publicadas não deixaram nada na coleção para desfazer.
            if entry.get("estado") == "preparado":
                continue
            pending = entry.get("estado") == "pronto_para_substituir"
            if entry["acao"] == "adicionar":
                if not target.exists():
                    continue
                secure_file(root, str(target))
                if sha256(target) != entry["depois_sha256"]:
                    if pending:
                        notices.append(f"Mantido, não foi publicado por esta importação: {target.name}")
                        continue
                    raise ValueError(f"A faixa adicionada recebeu edições posteriores: {target.name}")
                archive = (transaction / entry["retirada"]).resolve()
                archive.relative_to(transaction)
                if archive.exists() and sha256(archive) != entry["depois_sha256"]:
                    raise ValueError("A cópia de uma adição desfeita foi alterada")
                work.append((entry, target, archive))
            else:
                secure_file(root, str(target))
                backup = (transaction / entry["backup"]).resolve(strict=True)
                backup.relative_to(transaction)
                if sha256(backup) != entry["antes_sha256"]:
                    raise ValueError("Backup danificado; restauração recusada")
                current = sha256(target)
                if current == entry["antes_sha256"]:
                    continue
                if current != entry["depois_sha256"]:
                    if pending:
                        notices.append(f"Mantido, foi alterado por outro programa antes da substituição: {target.name}")
                        continue
                    raise ValueError(f"Há edições posteriores: {target.name}")
                protecoes.require_same_protected(policy, root, str(target), target, backup)
                work.append((entry, target, backup))
        for entry, target, backup in work:
            if entry["acao"] == "adicionar":
                if not backup.exists():
                    durable_copy(target, backup)
                if sha256(backup) != entry["depois_sha256"] or sha256(target) != entry["depois_sha256"]:
                    raise ValueError("A faixa mudou durante a restauração")
                entry["estado"] = "adicao_arquivada"
                atomic_json(manifest_path, manifest)
                target.unlink()  # O áudio continua em retiradas/ e no download original.
                sync_dir(target.parent)
            else:
                fd, name = tempfile.mkstemp(prefix=".acervo-undo-", suffix=target.suffix, dir=target.parent)
                os.close(fd)
                temp = Path(name)
                try:
                    durable_copy(backup, temp)
                    if sha256(temp) != entry["antes_sha256"] or sha256(target) != entry["depois_sha256"]:
                        raise ValueError("O arquivo mudou durante a restauração")
                    os.replace(temp, target)
                    sync_dir(target.parent)
                finally:
                    temp.unlink(missing_ok=True)
            entry["estado"] = "restaurado"
            atomic_json(manifest_path, manifest)
        manifest["estado"] = "restaurado"
        atomic_json(manifest_path, manifest)
    return {"manifesto": str(manifest_path), "arquivos_restaurados": len(work), "estado": "restaurado", "avisos": notices}
