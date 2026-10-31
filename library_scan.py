#!/usr/bin/env python3
"""Read-only diagnosis of the music library.

Reads MP3/FLAC with Mutagen and exports a JSON/CSV inventory and album
variant candidates. Has no tag-writing function and never uses the network.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import unicodedata

from mutagen import File
from mutagen.flac import FLAC
from mutagen.mp3 import MP3


FIELDS = {
    "titulo": ("TIT2", "title"),
    "artista": ("TPE1", "artist"),
    "artista_album": ("TPE2", "albumartist"),
    "album": ("TALB", "album"),
    "ano": ("TDRC", "date"),
    "ano_original": ("TDOR", "originaldate"),
    "faixa": ("TRCK", "tracknumber"),
    "disco": ("TPOS", "discnumber"),
    "genero": ("TCON", "genre"),
}
# Temporary copies Acervo creates next to the original while writing.
TEMP_PREFIXES = (".acervo-import-", ".acervo-undo-", ".acervo-baixando-", ".biblioteca-", ".restaurar-")
REQUIRED = ("titulo", "artista", "artista_album", "album", "ano", "faixa", "disco", "genero")
VERSION = re.compile(
    r"\b(live|ao vivo|demo|remix|alternate|alternative mix|mix(?:es)?|session|bonus|"
    r"acoustic|ac[uú]stic[ao]|instrumental|rehearsal|ensaio|outtake|edit|"
    r"version|vers[aã]o)\b", re.I
)
EDITION = re.compile(r"\b(remaster(?:ed)?|deluxe|expanded|anniversary|box set)\b", re.I)


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def album_base(value: str) -> str:
    """Heuristic used only in the report, never as identification."""
    value = unicodedata.normalize("NFKC", value).strip()
    while match := re.search(r"\s*[\[(]([^\[\]()]+)[\])]\s*$", value):
        label = match.group(1)
        if not EDITION.search(label) or VERSION.search(label):
            break
        value = value[:match.start()].strip()
    return value


def signature(path: Path) -> dict:
    stat = path.stat()
    return {
        "tamanho_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
        "dispositivo": stat.st_dev,
        "inode": stat.st_ino,
    }


def picture(data: bytes, mime: str, kind: int, description: str) -> dict:
    return {
        "tamanho_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "mime": mime, "tipo": int(kind), "descricao": description,
    }


def read_track(path: Path) -> dict:
    before = signature(path)
    audio = File(path)
    if not isinstance(audio, (MP3, FLAC)):
        raise ValueError("Arquivo não reconhecido como MP3/FLAC")
    tags = audio.tags
    is_mp3 = isinstance(audio, MP3)

    def values(id3: str, vorbis: str) -> list[str]:
        if tags is None:
            return []
        if is_mp3:
            result = [str(v) for frame in tags.getall(id3) for v in (frame.genres if id3 == "TCON" else frame.text)]
        else:
            result = [str(v) for v in tags.get(vorbis, [])]
        return result

    row = {field: values(*keys) for field, keys in FIELDS.items()}
    if is_mp3:
        covers = [picture(f.data, f.mime, f.type, f.desc) for f in tags.getall("APIC")] if tags else []
        ids = {}
        if tags:
            for frame in tags.getall("TXXX"):
                if "musicbrainz" in frame.desc.casefold():
                    ids[frame.desc] = [str(v) for v in frame.text]
            for frame in tags.getall("UFID"):
                if "musicbrainz" in frame.owner.casefold():
                    ids["UFID:" + frame.owner] = [frame.data.decode("utf-8", errors="replace")]
        tag_format = ".".join(map(str, tags.version)) if tags is not None else None
    else:
        covers = [picture(p.data, p.mime, p.type, p.desc) for p in audio.pictures]
        ids = {k: list(v) for k, v in (tags or {}).items() if "musicbrainz" in k.casefold()}
        tag_format = "Vorbis Comment"

    row.update({
        "arquivo": str(path), "formato": "MP3" if is_mp3 else "FLAC",
        "formato_tags": tag_format, "assinatura_antes": before,
        "duracao_segundos": round(audio.info.length, 6),
        "bitrate_bps": getattr(audio.info, "bitrate", None),
        "sample_rate_hz": audio.info.sample_rate,
        "canais": audio.info.channels,
        "bits_por_amostra": getattr(audio.info, "bits_per_sample", None),
        "capas": covers, "musicbrainz": ids,
        "chaves_tags": sorted(tags.keys()) if tags is not None else [],
        "campos_ausentes": [k for k in REQUIRED if not any(v.strip() for v in row[k])],
        "marcadores_versao": sorted(set(VERSION.findall(" ".join(row["titulo"] + row["album"])))),
    })
    row["arquivo_estavel_durante_leitura"] = before == signature(path)
    return row


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                for k, v in row.items() if k in fields
            })


def scan(root: Path, output: Path) -> dict:
    # Never writes reports into the library nor allows overwriting a previous run.
    if output == root or root in output.parents:
        raise ValueError("A saída dos relatórios deve ficar fora da biblioteca")
    if output.exists():
        raise ValueError("A pasta de saída já existe; escolha uma nova para preservar os relatórios")

    tracks, errors, skipped = [], [], []
    walk_errors = []
    files = []
    for folder, dirs, names in os.walk(root, followlinks=False, onerror=walk_errors.append):
        for name in list(dirs):
            directory = Path(folder) / name
            if directory.is_symlink():
                skipped.append({"arquivo": str(directory), "motivo": "diretório simbólico"})
                dirs.remove(name)
        for name in names:
            path = Path(folder) / name
            if path.suffix.casefold() not in (".mp3", ".flac"):
                continue
            if name.startswith(TEMP_PREFIXES):
                skipped.append({"arquivo": str(path), "motivo": "cópia temporária do Acervo"})
            elif path.is_symlink():
                skipped.append({"arquivo": str(path), "motivo": "arquivo simbólico"})
            else:
                files.append(path)
    for error in walk_errors:
        errors.append({"arquivo": str(error.filename), "erro": str(error)})
    for path in sorted(files):
        try:
            tracks.append(read_track(path))
        except Exception as error:
            errors.append({"arquivo": str(path), "erro": f"{type(error).__name__}: {error}"})

    changed = []
    for row in tracks:
        try:
            stable = row["arquivo_estavel_durante_leitura"] and signature(Path(row["arquivo"])) == row["assinatura_antes"]
        except OSError:
            stable = False
        row["arquivo_estavel_ate_fim_varredura"] = stable
        if not stable:
            changed.append(row["arquivo"])

    groups = defaultdict(list)
    for row in tracks:
        # Different credits stay separate; AC/DC is never split at the slash.
        artist = tuple(normalized(v) for v in row["artista_album"] or row["artista"])
        if artist and len(row["album"]) == 1:
            groups[(artist, normalized(album_base(row["album"][0])))].append(row)
    variants = []
    for (artist, base), members in sorted(groups.items()):
        if len({tuple(row["album"]) for row in members}) < 2:
            continue
        for row in members:
            variants.append({
                "artista_grupo": list(artist), "chave_album_candidato": base,
                "album_base_candidato": album_base(row["album"][0]),
                "album_atual": row["album"], "titulo": row["titulo"],
                "faixa": row["faixa"], "disco": row["disco"],
                "arquivo": row["arquivo"], "marcadores_versao": row["marcadores_versao"],
                "status": "revisão obrigatória; sem identificação da gravação",
                "evidencia": "semelhança textual; não confirma Release Group, Release ou Recording",
            })

    formats = Counter(row["formato"] for row in tracks)
    missing = {k: sum(k in row["campos_ausentes"] for row in tracks) for k in REQUIRED}
    summary = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(), "biblioteca": str(root),
        "encontrados": len(files), "lidos": len(tracks), "formatos": dict(formats),
        "campos_ausentes": missing,
        "com_capa": sum(bool(row["capas"]) for row in tracks),
        "com_identificadores_musicbrainz": sum(bool(row["musicbrainz"]) for row in tracks),
        "com_marcadores_de_versao": sum(bool(row["marcadores_versao"]) for row in tracks),
        "grupos_candidatos_de_album": len({(tuple(r["artista_grupo"]), r["chave_album_candidato"]) for r in variants}),
        "erros": errors, "ignorados": skipped, "arquivos_com_mudanca_detectada": changed,
        "somente_leitura": True,
        "limites": [
            "O inventário não é um backup restaurável dos metadados.",
            "A estabilidade compara tamanho, mtime, ctime, dispositivo e inode; não calcula hash integral do áudio.",
            "A leitura pode atualizar atime conforme a configuração do sistema de arquivos.",
            "Marcadores de versão e grupos de álbum são heurísticas para revisão humana.",
            "Não identifica gravações, não detecta duplicatas e não aprova alterações.",
            "Nenhuma consulta externa foi feita por este programa.",
        ],
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "inventario.json").write_text(json.dumps({"resumo": summary, "musicas": tracks}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "resumo.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    columns = ["arquivo", "formato", *FIELDS, "duracao_segundos", "bitrate_bps", "sample_rate_hz", "canais", "bits_por_amostra", "formato_tags", "capas", "musicbrainz", "campos_ausentes", "marcadores_versao", "arquivo_estavel_ate_fim_varredura"]
    write_csv(output / "inventario.csv", tracks, columns)
    write_csv(output / "variantes_albuns.csv", variants, ["artista_grupo", "chave_album_candidato", "album_base_candidato", "album_atual", "titulo", "faixa", "disco", "arquivo", "marcadores_versao", "status", "evidencia"])
    write_csv(output / "erros.csv", errors, ["arquivo", "erro"])
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pasta", type=Path, required=True, help="Biblioteca de origem; somente leitura")
    parser.add_argument("--saida", type=Path, required=True, help="Nova pasta de relatórios, fora da biblioteca")
    args = parser.parse_args()
    root = args.pasta.expanduser().resolve()
    output = args.saida.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"Pasta de músicas inexistente: {root}")
    try:
        summary = scan(root, output)
    except (OSError, ValueError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Relatórios: {output}")
    return 2 if summary["erros"] or summary["arquivos_com_mudanca_detectada"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
