"""Geração de planos imutáveis e planilhas de aprovação."""

from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from biblioteca_core import atomic_json, integrity, previous_album, sha256
from diagnostico_biblioteca import album_base, normalized, read_track, signature, write_csv, TEMP_PREFIXES, VERSION


BASE = Path(__file__).resolve().parent


def rules(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def propose(track: dict, genres: list[dict], albums: list[dict], online=None) -> tuple[list[dict], list[str]]:
    changes, review = [], []
    artists = track["artista_album"] or track["artista"]
    # Uma string com '/' é um crédito inteiro; não se divide AC/DC nem convidados.
    artist = artists[0] if len(artists) == 1 else ""
    genre_rules = [r for r in genres if normalized(r["artista"]) == normalized(artist) and (not r.get("album") or any(normalized(album_base(a)) == normalized(r["album"]) for a in track["album"]))]
    specific = [r for r in genre_rules if r.get("album")]
    matches = specific or genre_rules
    existing_genre = any(v.strip() for v in track["genero"])
    if not existing_genre:
        options = {r["genero"] for r in matches if r.get("genero", "").strip()}
        if len(options) == 1:
            rule = matches[0]
            changes.append({"campo": "genero", "atual": track["genero"], "proposto": [next(iter(options))], "fonte": rule["origem"], "criterio": "Regra por álbum" if specific else "Regra por crédito exato de artista; requer aprovação"})
        elif online is not None and artist and len(track["album"]) == 1:
            found = online.genre(artist, track["album"][0])
            if found.get("genero"):
                changes.append({"campo": "genero", "atual": track["genero"], "proposto": [found["genero"]], "fonte": found["fonte"], "criterio": found["criterio"]})
            else:
                review.append(found.get("revisao", "Sem gênero identificado"))
        else:
            review.append("Gênero sem regra ou regras conflitantes")

    for rule in albums:
        if track["artista"] != [rule["artista"]] or track["album"] != [rule["album_atual"]]:
            continue
        if track["album"] == [rule["album_padrao"]]:
            continue
        title = track["titulo"][0] if len(track["titulo"]) == 1 else ""
        clean_title = re.sub(r"\s*-\s*remaster(?:ed)?(?:\s+\d{4})?\s*$", "", title, flags=re.I)
        numbers = json.loads(rule["faixas_json"])
        number = track["faixa"][0].split("/")[0] if len(track["faixa"]) == 1 else ""
        disc = track["disco"][0].split("/")[0] if len(track["disco"]) == 1 else ""
        # O título inteiro deve corresponder: "To Live Is to Die" não é uma versão ao vivo.
        if disc != rule["disco"] or normalized(clean_title) != normalized(numbers.get(number, "")) or not clean_title:
            review.append("Álbum candidato com versão/faixa/disco incompatível; preservar e revisar")
            continue
        if previous_album(Path(track["arquivo"])):
            review.append("Álbum já tem registro de nome anterior; revisão necessária")
            continue
        changes.append({"campo": "album", "atual": track["album"], "proposto": [rule["album_padrao"]], "fonte": rule["fonte"], "criterio": "Correspondência de artista, álbum, disco, faixa e título; gravação não confirmada", "preservacao": "Nome atual preservado em BibliotecaMusical:AlbumAnterior (MP3) ou BIBLIOTECA_ALBUM_ANTERIOR (FLAC)"})
    return changes, review


def create_plan(root: Path, output: Path, normalize_albums: bool = False, genre_path: Path | None = None, album_path: Path | None = None, online=None, progress=None) -> dict:
    root = root.expanduser().resolve(strict=True)
    output = output.expanduser().resolve()
    if not root.is_dir() or output == root or root in output.parents:
        raise ValueError("A biblioteca deve ser uma pasta e os relatórios devem ficar fora dela")
    if output.exists():
        raise ValueError("A pasta de saída já existe; escolha outra")
    genres = rules(genre_path or BASE / "generos_artistas.csv")
    albums = rules(album_path or BASE / "albuns_excecoes.csv") if normalize_albums else []
    files, ignored, errors = [], [], []
    def walk_error(error):
        errors.append({"arquivo": str(error.filename), "erro": str(error)})
    for folder, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        for name in list(dirs):
            if (Path(folder) / name).is_symlink():
                ignored.append(str(Path(folder) / name))
                dirs.remove(name)
        for name in names:
            path = Path(folder) / name
            if path.suffix.casefold() not in (".mp3", ".flac"):
                continue
            if name.startswith(TEMP_PREFIXES) or path.is_symlink():
                ignored.append(str(path))
            else:
                files.append(path)
    tracks, planned, flat, reviews = [], [], [], []
    for index, path in enumerate(sorted(files)):
        if progress:
            progress(index, len(files), path.name)
        try:
            stat_before = signature(path)
            track = read_track(path)
            digest = sha256(path)
            if signature(path) != stat_before or not track["arquivo_estavel_durante_leitura"]:
                raise ValueError("Arquivo mudou durante a análise")
            track["id"] = hashlib.sha256(str(path).encode()).hexdigest()[:24]
            tracks.append(track)
            changes, review = propose(track, genres, albums, online)
            if changes:
                # Verifica suporte antes de oferecer aprovação de gravação.
                integrity(path)
                if path.stat().st_nlink > 1:
                    raise ValueError("Arquivo com hard links; aprovação bloqueada")
                for change in changes:
                    change["id"] = hashlib.sha256((str(path) + digest + json.dumps(change, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()[:24]
                    flat.append({"id": change["id"], "arquivo": str(path), "titulo": " | ".join(track["titulo"]), "artista": " | ".join(track["artista"]), "campo": change["campo"], "atual": json.dumps(change["atual"], ensure_ascii=False), "proposto": json.dumps(change["proposto"], ensure_ascii=False), "fonte": change["fonte"], "criterio": change["criterio"], "preservacao": change.get("preservacao", ""), "aprovar": "nao"})
                planned.append({"arquivo": str(path), "sha256": digest, "tamanho_bytes": stat_before["tamanho_bytes"], "alteracoes": changes})
            if review or track["campos_ausentes"]:
                reviews.append({"arquivo": str(path), "campos_ausentes": track["campos_ausentes"], "observacoes": review})
        except Exception as error:
            errors.append({"arquivo": str(path), "erro": f"{type(error).__name__}: {error}"})
    summary = {"encontrados": len(files), "lidos": len(tracks), "formatos": dict(Counter(r["formato"] for r in tracks)), "campos_ausentes": {k: sum(k in t["campos_ausentes"] for t in tracks) for k in ("titulo", "artista", "artista_album", "album", "ano", "faixa", "disco", "genero")}, "com_capa": sum(bool(t["capas"]) for t in tracks), "arquivos_com_propostas": len(planned), "alteracoes_propostas": len(flat), "propostas_por_campo": dict(Counter(c["campo"] for r in planned for c in r["alteracoes"])), "revisoes": len(reviews), "erros": errors, "ignorados": ignored, "consultas_musicbrainz": online.requests if online else 0}
    plan = {"schema": 1, "criado_em": datetime.now(timezone.utc).isoformat(), "biblioteca": str(root), "resumo": summary, "arquivos": planned}
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / "plano.json", plan)
    (output / "plano.sha256").write_text(sha256(output / "plano.json") + "\n")
    atomic_json(output / "inventario.json", {"resumo": summary, "musicas": tracks})
    atomic_json(output / "resumo.json", summary)
    write_csv(output / "alteracoes.csv", flat, ["id", "arquivo", "titulo", "artista", "campo", "atual", "proposto", "fonte", "criterio", "preservacao", "aprovar"])
    write_csv(output / "revisao.csv", reviews, ["arquivo", "campos_ausentes", "observacoes"])
    write_csv(output / "erros.csv", errors, ["arquivo", "erro"])
    if progress:
        progress(len(files), len(files), "Análise concluída")
    return {"pasta_relatorios": str(output), "resumo": summary, "plano": str(output / "plano.json")}
