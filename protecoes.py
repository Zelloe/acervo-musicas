"""Campos protegidos: escolhas do usuário que o Acervo não pode propor nem gravar."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat

from biblioteca_core import TAG_FIELDS, atomic_json, library_lock, metadata_values

FIELDS = tuple(TAG_FIELDS)
SCHEMA = 1


def policy_path(storage: Path, root: Path) -> Path:
    """Um arquivo por biblioteca canônica, para não misturar coleções com os mesmos dados."""
    root = root.resolve()
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return storage.resolve() / ".biblioteca" / "protecoes" / f"{digest}.json"


def empty(root: Path) -> dict:
    return {"schema": SCHEMA, "biblioteca": str(root.resolve()), "faixas": {}}


def load(storage: Path, root: Path) -> dict:
    """Não pega o lock: deve ser chamada por quem já está dentro de library_lock."""
    path = policy_path(storage, root)
    # Falha fechada: só a ausência comprovada do arquivo vale como política vazia.
    try:
        info = path.lstat()
    except FileNotFoundError:
        return empty(root)
    except OSError as error:
        raise ValueError(f"Não foi possível ler os campos protegidos; nenhuma gravação será feita: {path}") from error
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"O arquivo de campos protegidos deve ser um arquivo comum, não um link: {path}")
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Arquivo de campos protegidos ilegível; nenhuma gravação será feita: {path}") from error
    if not isinstance(policy, dict) or policy.get("schema") != SCHEMA or not isinstance(policy.get("faixas"), dict):
        raise ValueError(f"Arquivo de campos protegidos incompatível; nenhuma gravação será feita: {path}")
    if policy.get("biblioteca") != str(root.resolve()):
        raise ValueError(f"O arquivo de campos protegidos pertence a outra biblioteca: {path}")
    for relative, entry in policy["faixas"].items():
        fields = entry.get("campos") if isinstance(entry, dict) else None
        if not isinstance(fields, list) or not all(isinstance(c, str) and c in FIELDS for c in fields) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError(f"Arquivo de campos protegidos incompatível; nenhuma gravação será feita: {path}")
    return policy


def _relative(root: Path, arquivo: str) -> str:
    path = Path(arquivo)
    if not path.is_absolute():
        raise ValueError(f"Caminho inválido: {arquivo}")
    return path.relative_to(root.resolve()).as_posix()


def protected(policy: dict, root: Path, arquivo: str) -> frozenset[str]:
    entry = policy["faixas"].get(_relative(root, arquivo))
    return frozenset(entry["campos"]) if entry else frozenset()


def listing(policy: dict) -> list[dict]:
    root = Path(policy["biblioteca"])
    return [{"arquivo": str(root / relative), "campos": entry["campos"], "atualizado_em": entry.get("atualizado_em")} for relative, entry in sorted(policy["faixas"].items())]


def blocked_changes(policy: dict, root: Path, arquivo: str, fields) -> list[str]:
    return sorted(protected(policy, root, arquivo) & set(fields))


def require_unprotected(policy: dict, root: Path, arquivo: str, fields) -> None:
    blocked = blocked_changes(policy, root, arquivo, fields)
    if blocked:
        raise ValueError(f"Campo protegido ({', '.join(blocked)}): {Path(arquivo).name}. Desbloqueie antes de alterar.")


def require_same_protected(policy: dict, root: Path, arquivo: str, current: Path, candidate: Path) -> None:
    """O arquivo que vai ocupar o caminho precisa manter os valores protegidos atuais."""
    fields = protected(policy, root, arquivo)
    if not fields:
        return
    before, after = metadata_values(current), metadata_values(candidate)
    changed = sorted(f for f in fields if before.get(f, []) != after.get(f, []))
    if changed:
        raise ValueError(f"A operação mudaria campo protegido ({', '.join(changed)}): {Path(arquivo).name}. Desbloqueie antes.")


def _validate(root: Path, arquivos: list[str], campos: list[str]) -> tuple[list[str], list[str]]:
    if not isinstance(campos, list) or not campos or any(c not in FIELDS for c in campos):
        raise ValueError(f"Campos inválidos; use: {', '.join(FIELDS)}")
    if not isinstance(arquivos, list) or not arquivos:
        raise ValueError("Selecione ao menos uma faixa")
    relatives = []
    for arquivo in arquivos:
        path = Path(arquivo) if isinstance(arquivo, str) else None
        if path is None or not path.is_absolute() or path.is_symlink() or path.resolve() != path:
            raise ValueError(f"Caminho inválido ou simbólico: {arquivo}")
        relative = _relative(root, arquivo)
        if not path.is_file() or path.suffix.casefold() not in (".mp3", ".flac"):
            raise ValueError(f"Faixa inexistente ou formato não suportado: {arquivo}")
        relatives.append(relative)
    return relatives, sorted(set(campos), key=FIELDS.index)


def _change(storage: Path, root: Path, arquivos: list[str], campos: list[str], add: bool) -> dict:
    root = root.resolve(strict=True)
    relatives, fields = _validate(root, arquivos, campos)
    path = policy_path(storage, root)
    try:
        with library_lock(root):
            policy = load(storage, root)
            now = datetime.now(timezone.utc).isoformat()
            for relative in relatives:
                current = set(policy["faixas"].get(relative, {"campos": []})["campos"])
                current = current | set(fields) if add else current - set(fields)
                if current:
                    policy["faixas"][relative] = {"campos": sorted(current, key=FIELDS.index), "atualizado_em": now}
                else:
                    policy["faixas"].pop(relative, None)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(path, policy)
    except BlockingIOError as error:
        raise ValueError("A biblioteca está ocupada por outra gravação; aguarde e tente novamente") from error
    return {"arquivos": len(relatives), "campos": fields, "politica": str(path)}


def protect(storage: Path, root: Path, arquivos: list[str], campos: list[str]) -> dict:
    return _change(storage, root, arquivos, campos, True)


def unprotect(storage: Path, root: Path, arquivos: list[str], campos: list[str]) -> dict:
    return _change(storage, root, arquivos, campos, False)
