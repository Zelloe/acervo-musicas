"""Explicit removal with a verified original kept outside the music library."""

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from acervo.library.core import atomic_json, durable_copy, library_lock, secure_file, sha256, sync_dir
from acervo.messages import _, register

register(
    {
        "The file changed since the preview; review it again": "O arquivo mudou desde a prévia; confira novamente",
        "The original folder is unavailable or contains symbolic links": "A pasta original não está disponível ou contém links simbólicos",
        "A file already occupies the original path; it will not be overwritten": "Já existe um arquivo no caminho original; ele não será sobrescrito",
        "The saved original is damaged; restoration was refused": "O original guardado está danificado; a restauração foi recusada",
        "Not enough space to keep a recoverable original": "Não há espaço suficiente para guardar um original recuperável",
        "Removal was interrupted; check the original file and History": "A exclusão foi interrompida; confira o arquivo original e o Histórico",
    }
)


def archived_files(root, storage):
    result = set()
    for path in (Path(storage) / "backups").glob("*/manifesto.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if (
                manifest.get("tipo") == "exclusao"
                and manifest.get("biblioteca") == str(root)
                and manifest.get("estado") != "restaurado"
            ):
                result.update(row["arquivo"] for row in manifest["arquivos"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def remove_track(root, storage, file, expected):
    root, storage = Path(root).resolve(strict=True), Path(storage).resolve()
    backups = storage / "backups"
    if backups.resolve() != backups or backups == root or root in backups.parents:
        raise ValueError(_("Backups must be kept outside the library"))
    with library_lock(root):
        source = secure_file(root, file)
        if sha256(source) != expected:
            raise ValueError(_("The file changed since the preview; review it again"))
        backups.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(backups).free < source.stat().st_size:
            raise ValueError(_("Not enough space to keep a recoverable original"))
        folder = backups / uuid.uuid4().hex
        folder.mkdir(mode=0o700)
        backup = folder / ("original" + source.suffix)
        manifest_path = folder / "manifesto.json"
        entry = {"arquivo": str(source), "backup": backup.name, "antes_sha256": expected, "estado": "preparando_backup"}
        manifest = {
            "schema": 1,
            "tipo": "exclusao",
            "biblioteca": str(root),
            "estado": "preparando_backups",
            "arquivos": [entry],
        }
        atomic_json(manifest_path, manifest)
        try:
            durable_copy(source, backup)
            if sha256(backup) != expected or sha256(secure_file(root, file)) != expected:
                raise ValueError(_("The file changed since the preview; review it again"))
            entry["estado"] = "pronto_para_remover"
            atomic_json(manifest_path, manifest)
            if sha256(secure_file(root, file)) != expected:
                raise ValueError(_("The file changed since the preview; review it again"))
            source.unlink()
            sync_dir(source.parent)
            entry["estado"] = "removido"
            manifest["estado"] = "concluido"
        except Exception as error:
            manifest.update(estado="interrompido", erro=str(error))
            atomic_json(manifest_path, manifest)
            raise RuntimeError(_("Removal was interrupted; check the original file and History")) from error
        atomic_json(manifest_path, manifest)
        return {"manifesto": str(manifest_path), "arquivos_alterados": 1, "removed_files": [file]}


def restore_deleted(manifest_path):
    manifest_path = Path(manifest_path).resolve(strict=True)
    folder = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or manifest.get("tipo") != "exclusao" or len(manifest.get("arquivos", [])) != 1:
        raise ValueError(_("Incompatible manifest"))
    root = Path(manifest["biblioteca"]).resolve(strict=True)
    entry = manifest["arquivos"][0]
    target = Path(entry["arquivo"])
    backup = folder / entry["backup"]
    if backup.is_symlink() or backup.resolve() != backup or backup.parent != folder or not backup.is_file():
        raise ValueError(_("The saved original is damaged; restoration was refused"))
    target.relative_to(root)
    if (
        not target.is_absolute()
        or target.parent.resolve() != target.parent
        or not target.parent.is_dir()
        or target.suffix.casefold() not in (".mp3", ".flac")
    ):
        raise ValueError(_("The original folder is unavailable or contains symbolic links"))
    with library_lock(root):
        if sha256(backup) != entry["antes_sha256"]:
            raise ValueError(_("The saved original is damaged; restoration was refused"))
        if os.path.lexists(target):
            # A restore interrupted after publication is also safe to repeat.
            if (
                manifest["estado"] == "restaurado"
                and not target.is_symlink()
                and target.is_file()
                and sha256(target) == entry["antes_sha256"]
            ):
                return {"estado": "restaurado", "arquivos_restaurados": 0, "manifesto": str(manifest_path)}
            if (
                not target.is_symlink()
                and target.is_file()
                and sha256(secure_file(root, str(target))) == entry["antes_sha256"]
            ):
                entry["estado"] = "restaurado"
                manifest["estado"] = "restaurado"
                atomic_json(manifest_path, manifest)
                return {"estado": "restaurado", "arquivos_restaurados": 0, "manifesto": str(manifest_path)}
            raise ValueError(_("A file already occupies the original path; it will not be overwritten"))
        if shutil.disk_usage(target.parent).free < backup.stat().st_size:
            raise ValueError(_("Not enough space in the library for a temporary file"))
        fd, name = tempfile.mkstemp(prefix=".acervo-undo-", suffix=target.suffix, dir=target.parent)
        os.close(fd)
        temp = Path(name)
        try:
            durable_copy(backup, temp)
            if sha256(temp) != entry["antes_sha256"] or sha256(backup) != entry["antes_sha256"]:
                raise ValueError(_("The saved original is damaged; restoration was refused"))
            # Exclusive publication cannot overwrite a file created during restore.
            os.link(temp, target)
            temp.unlink()
            sync_dir(target.parent)
            entry["estado"] = "restaurado"
            manifest["estado"] = "restaurado"
            atomic_json(manifest_path, manifest)
        finally:
            temp.unlink(missing_ok=True)
    return {"estado": "restaurado", "arquivos_restaurados": 1, "manifesto": str(manifest_path)}
