"""Reviewed import of downloads, replacements and album fixes."""

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

from library_core import (
    atomic_json, durable_copy, id3_size, integrity, library_lock,
    secure_file, sha256, sync_dir, write_validated,
)
from messages import _, register

register({
    "Truncated MP3": "MP3 truncado",
    "Replacement requires MP3 for MP3; keep the FLAC and add the new version separately": "A substituição exige MP3 por MP3; preserve o FLAC e adicione a nova versão separadamente",
    "The original contains identification or audio measurements; add the download as another version": "O original contém identificação ou medições de áudio; adicione o download como outra versão",
    "The download's audio stream changed while preserving the tags": "O fluxo de áudio do download mudou ao preservar as tags",
    "Could not preserve the original ID3 structure": "Não foi possível preservar a estrutura ID3 original",
    "Could not preserve all the original tags": "Não foi possível preservar todas as tags originais",
    "Invalid destination folder or symbolic link": "Pasta de destino inválida ou simbólica",
    "Invalid new file name": "Nome de arquivo novo inválido",
    "The destination already exists; generate a new preview: {name}": "O destino já existe; gere outra prévia: {name}",
    "The file system of {directory} does not support hard links (common on exFAT/FAT32); adding new tracks there is not supported yet": "O sistema de arquivos de {directory} não aceita hard links (comum em exFAT/FAT32); adicionar faixas novas ali ainda não é suportado",
    "Select at least one operation to approve": "Selecione ao menos uma operação para aprovar",
    "Backups must be kept outside the library": "Backups devem ficar fora da biblioteca",
    "Two operations affect the same file; review the selection": "Duas operações atingem o mesmo arquivo; revise a seleção",
    "The original changed since the preview: {name}": "O original mudou desde a prévia: {name}",
    "The download changed since the preview": "O download mudou desde a prévia",
    "Not enough space to prepare the import and the backups": "Espaço insuficiente para preparar a importação e os backups",
    "The original changed during the backup": "O original mudou durante o backup",
    "The original was modified by another program": "O original foi alterado por outro programa",
    "The prepared copy changed": "A cópia preparada mudou",
    "The original changed before the replacement": "O original mudou antes da substituição",
    "Import interrupted. The history allows restoring: {manifest_path}. {error}": "Importação interrompida. O histórico permite restaurar: {manifest_path}. {error}",
    "Invalid import manifest": "Manifesto de importação inválido",
    "Invalid restore destination": "Destino da restauração inválido",
    "Kept, it was not published by this import: {name}": "Mantido, não foi publicado por esta importação: {name}",
    "The added track was edited afterwards: {name}": "A faixa adicionada recebeu edições posteriores: {name}",
    "The copy of an undone addition was modified": "A cópia de uma adição desfeita foi alterada",
    "Damaged backup; restore refused": "Backup danificado; restauração recusada",
    "Kept, it was modified by another program before the replacement: {name}": "Mantido, foi alterado por outro programa antes da substituição: {name}",
    "There are later edits: {name}": "Há edições posteriores: {name}",
    "The track changed during the restore": "A faixa mudou durante a restauração",
    "The file changed during the restore": "O arquivo mudou durante a restauração",
})


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
                raise ValueError(_("Truncated MP3"))
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def preserve_original_tags(original: Path, candidate: Path) -> None:
    if original.suffix.casefold() != candidate.suffix.casefold() or candidate.suffix.casefold() != ".mp3":
        raise ValueError(_("Replacement requires MP3 for MP3; keep the FLAC and add the new version separately"))
    before = integrity(original)
    for key in before["tags"]:
        if any(word in key.casefold() for word in ("replaygain", "acoustid", "fingerprint", "musicbrainz", "rva2", "rva")):
            raise ValueError(_("The original contains identification or audio measurements; add the download as another version"))
    stream_hash = compressed_mp3_hash(candidate)
    fresh = ID3(candidate, translate=False)
    tags = deepcopy(ID3(original, translate=False))
    # TLEN describes the audio length; the download's value applies, not the original's.
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
        raise ValueError(_("The download's audio stream changed while preserving the tags"))
    if before["versao"] != after["versao"] or before["desconhecidos"] != after["desconhecidos"]:
        raise ValueError(_("Could not preserve the original ID3 structure"))
    if any(after["tags"].get(k) != v for k, v in before["tags"].items() if not k.startswith("TLEN")):
        raise ValueError(_("Could not preserve all the original tags"))


def new_destination(root: Path, folder: str, name: str) -> Path:
    directory = Path(folder)
    if not directory.is_absolute() or directory.resolve() != directory or not directory.is_dir():
        raise ValueError(_("Invalid destination folder or symbolic link"))
    directory.relative_to(root)
    if Path(name).name != name or name in ("", ".", "..") or Path(name).suffix.casefold() != ".mp3":
        raise ValueError(_("Invalid new file name"))
    path = directory / name
    if os.path.lexists(path):
        raise ValueError(_("The destination already exists; generate a new preview: {name}", name=name))
    return path


def require_hard_links(directory: Path) -> None:
    """Additions are published with link(), which never overwrites a destination."""
    fd, name = tempfile.mkstemp(prefix=".acervo-import-", dir=directory)
    os.close(fd)
    probe = Path(name)
    linked = probe.with_name(probe.name + "-link")
    try:
        os.link(probe, linked)
    except OSError as error:
        raise ValueError(_("The file system of {directory} does not support hard links (common on exFAT/FAT32); adding new tracks there is not supported yet", directory=directory)) from error
    finally:
        linked.unlink(missing_ok=True)
        probe.unlink(missing_ok=True)


def import_reviewed(root: Path, backup_root: Path, operations: list[dict], batch_id: str, storage: Path | None = None) -> dict:
    import protections
    root = root.resolve(strict=True)
    backup_root = backup_root.resolve()
    if not operations:
        raise ValueError(_("Select at least one operation to approve"))
    if backup_root == root or root in backup_root.parents:
        raise ValueError(_("Backups must be kept outside the library"))
    if len({op["arquivo"] for op in operations}) != len(operations):
        raise ValueError(_("Two operations affect the same file; review the selection"))
    with library_lock(root):
        policy = protections.load(storage or backup_root.parent, root)
        for op in operations:
            target = Path(op["arquivo"])
            if op["acao"] != "adicionar":
                protections.require_unprotected(policy, root, str(target), op.get("alteracoes") or {})
            if op["acao"] == "adicionar":
                new_destination(root, str(target.parent), target.name)
            else:
                secure_file(root, str(target))
                if sha256(target) != op["antes_sha256"]:
                    raise ValueError(_("The original changed since the preview: {name}", name=target.name))
            if op["acao"] != "metadados":
                source = Path(op["origem"])
                if not source.is_file() or source.is_symlink() or sha256(source) != op["origem_sha256"]:
                    raise ValueError(_("The download changed since the preview"))
        for directory in {Path(op["arquivo"]).parent for op in operations if op["acao"] == "adicionar"}:
            require_hard_links(directory)
        backup_root.mkdir(parents=True, exist_ok=True)
        required = sum(Path(op["arquivo"]).stat().st_size for op in operations if op["acao"] != "adicionar")
        required += sum(Path(op["origem"] if op["acao"] != "metadados" else op["arquivo"]).stat().st_size for op in operations)
        largest = max(Path(op["origem"] if op["acao"] != "metadados" else op["arquivo"]).stat().st_size for op in operations)
        if shutil.disk_usage(backup_root).free < required + largest or shutil.disk_usage(root).free < largest:
            raise ValueError(_("Not enough space to prepare the import and the backups"))
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
                        raise ValueError(_("The original changed during the backup"))
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
                    protections.require_same_protected(policy, root, str(target), backup, ready)
                entry = {"arquivo": str(target), "acao": op["acao"], "antes_sha256": op.get("antes_sha256"), "depois_sha256": sha256(ready), "backup": str(backup.relative_to(transaction)) if backup else None, "pronto": str(ready.relative_to(transaction)), "retirada": f"retiradas/{index:06d}{target.suffix}", "alteracoes": op.get("alteracoes", {}), "estado": "preparado"}
                manifest["arquivos"].append(entry)
                atomic_json(manifest_path, manifest)
            manifest["estado"] = "aplicando"
            atomic_json(manifest_path, manifest)
            for entry in manifest["arquivos"]:
                target = Path(entry["arquivo"])
                if entry["acao"] != "adicionar" and sha256(secure_file(root, str(target))) != entry["antes_sha256"]:
                    raise ValueError(_("The original was modified by another program"))
                fd, name = tempfile.mkstemp(prefix=".acervo-import-", suffix=target.suffix, dir=target.parent)
                os.close(fd)
                temp = Path(name)
                # Recorded before the copy so a restore can find leftovers from an interruption.
                entry["temporario"] = str(temp)
                atomic_json(manifest_path, manifest)
                try:
                    durable_copy(transaction / entry["pronto"], temp)
                    if sha256(temp) != entry["depois_sha256"]:
                        raise ValueError(_("The prepared copy changed"))
                    entry["estado"] = "pronto_para_substituir"
                    atomic_json(manifest_path, manifest)
                    if entry["acao"] == "adicionar":
                        # link() atomically refuses destinations that appeared after the preview.
                        os.link(temp, target)
                        temp.unlink()
                    else:
                        if sha256(target) != entry["antes_sha256"]:
                            raise ValueError(_("The original changed before the replacement"))
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
            raise RuntimeError(_("Import interrupted. The history allows restoring: {manifest_path}. {error}", manifest_path=manifest_path, error=error)) from error
        atomic_json(manifest_path, manifest)
        return {"manifesto": str(manifest_path), "arquivos_alterados": len(operations), "estado": "concluido"}


def restore_import(manifest_path: Path, storage: Path | None = None) -> dict:
    import protections
    manifest_path = manifest_path.resolve(strict=True)
    storage = storage or manifest_path.parent.parent.parent
    transaction = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != 1 or manifest.get("tipo") != "downloads":
        raise ValueError(_("Invalid import manifest"))
    root = Path(manifest["biblioteca"]).resolve(strict=True)
    with library_lock(root):
        policy = protections.load(storage, root)
        work, notices = [], []
        for index, entry in enumerate(manifest["arquivos"]):
            target = Path(entry["arquivo"])
            target.relative_to(root)
            if target.resolve() != target or target.is_symlink():
                raise ValueError(_("Invalid restore destination"))
            temp = Path(entry.get("temporario", "/inexistente"))
            if temp.parent == target.parent and temp.name.startswith(".acervo-import-") and temp.is_file() and not temp.is_symlink():
                if target.is_file() and temp.stat().st_ino == target.stat().st_ino:
                    # Window between link() and unlink() when the process was interrupted.
                    temp.unlink()
                else:
                    # Interrupted copy: leaves the collection but is kept in the transaction.
                    leftover = transaction / "retiradas" / f"temporario-{index:06d}{temp.suffix}"
                    if not leftover.exists():
                        durable_copy(temp, leftover)
                    temp.unlink()
                    sync_dir(target.parent)
            # Entries never published left nothing in the collection to undo.
            if entry.get("estado") == "preparado":
                continue
            pending = entry.get("estado") == "pronto_para_substituir"
            if entry["acao"] == "adicionar":
                if not target.exists():
                    continue
                secure_file(root, str(target))
                if sha256(target) != entry["depois_sha256"]:
                    if pending:
                        notices.append(_("Kept, it was not published by this import: {name}", name=target.name))
                        continue
                    raise ValueError(_("The added track was edited afterwards: {name}", name=target.name))
                archive = (transaction / entry["retirada"]).resolve()
                archive.relative_to(transaction)
                if archive.exists() and sha256(archive) != entry["depois_sha256"]:
                    raise ValueError(_("The copy of an undone addition was modified"))
                work.append((entry, target, archive))
            else:
                secure_file(root, str(target))
                backup = (transaction / entry["backup"]).resolve(strict=True)
                backup.relative_to(transaction)
                if sha256(backup) != entry["antes_sha256"]:
                    raise ValueError(_("Damaged backup; restore refused"))
                current = sha256(target)
                if current == entry["antes_sha256"]:
                    continue
                if current != entry["depois_sha256"]:
                    if pending:
                        notices.append(_("Kept, it was modified by another program before the replacement: {name}", name=target.name))
                        continue
                    raise ValueError(_("There are later edits: {name}", name=target.name))
                protections.require_same_protected(policy, root, str(target), target, backup)
                work.append((entry, target, backup))
        for entry, target, backup in work:
            if entry["acao"] == "adicionar":
                if not backup.exists():
                    durable_copy(target, backup)
                if sha256(backup) != entry["depois_sha256"] or sha256(target) != entry["depois_sha256"]:
                    raise ValueError(_("The track changed during the restore"))
                entry["estado"] = "adicao_arquivada"
                atomic_json(manifest_path, manifest)
                target.unlink()  # The audio remains in retiradas/ and in the original download.
                sync_dir(target.parent)
            else:
                fd, name = tempfile.mkstemp(prefix=".acervo-undo-", suffix=target.suffix, dir=target.parent)
                os.close(fd)
                temp = Path(name)
                try:
                    durable_copy(backup, temp)
                    if sha256(temp) != entry["antes_sha256"] or sha256(target) != entry["depois_sha256"]:
                        raise ValueError(_("The file changed during the restore"))
                    os.replace(temp, target)
                    sync_dir(target.parent)
                finally:
                    temp.unlink(missing_ok=True)
            entry["estado"] = "restaurado"
            atomic_json(manifest_path, manifest)
        manifest["estado"] = "restaurado"
        atomic_json(manifest_path, manifest)
    return {"manifesto": str(manifest_path), "arquivos_restaurados": len(work), "estado": "restaurado", "avisos": notices}
