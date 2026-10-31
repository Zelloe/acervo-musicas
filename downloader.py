"""Baixar músicas com o spotDL direto numa pasta, escolhendo artista, álbum, edição e faixas."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, ID3NoHeaderError, TCON

from library_plan import BASE, rules
from library_scan import TEMP_PREFIXES, album_base, normalized
from lyrics import Letras, embutir_texto
from versions import classificar

ADAPTER = BASE / "spotdl_search.py"
TEMPLATE = "{artist} - {title}.{output-ext}"
REMASTER = re.compile(r"\s*(?:-\s*|\(|\[)\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?(?:\s+version)?\s*[\])]?\s*$", re.I)


def spotdl_executable() -> str:
    # Aberto pelo menu do sistema, o PATH pode não incluir ~/.local/bin (pipx).
    found = shutil.which("spotdl") or str(Path.home() / ".local/bin/spotdl")
    if not Path(found).is_file():
        raise RuntimeError("spotDL não encontrado. Instale com: pipx install spotdl")
    return found


def spotdl_python() -> list[str]:
    """O adaptador precisa rodar no Python em que o spotDL está instalado."""
    executable = Path(spotdl_executable()).resolve()
    try:
        first = executable.open(encoding="utf-8").readline().strip()
    except (UnicodeError, OSError):
        first = ""
    command = shlex.split(first[2:]) if first.startswith("#!") else []
    if not command or not any("python" in Path(part).name for part in command):
        raise RuntimeError(f"Não foi possível descobrir o Python do spotDL em {executable}")
    return command


def _adapter(command: str, *args: str, timeout: int = 120) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="acervo-busca-") as folder:
        output = Path(folder) / "resultado.json"
        process = subprocess.run([*spotdl_python(), "-I", str(ADAPTER), command, str(output), *args], capture_output=True, text=True, timeout=timeout)
        if process.returncode or not output.is_file():
            detail = (process.stderr or process.stdout).strip().splitlines()
            raise RuntimeError(f"A busca no spotDL falhou: {detail[-1] if detail else 'sem detalhes'}")
        return json.loads(output.read_text(encoding="utf-8"))


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 300 or value.strip().startswith("-"):
        raise ValueError(f"Informe {name}")
    return value.strip()


# Só imagens dos CDNs de capas; nada de URLs arbitrárias vindas da busca.
PHOTO_HOSTS = re.compile(r"https://(?:[a-z0-9-]+\.googleusercontent\.com|i\.scdn\.co|mosaic\.scdn\.co|image-cdn-[a-z0-9-]+\.spotifycdn\.com)/[^\s\"'<>]+")


def foto_segura(url) -> str:
    return url if isinstance(url, str) and PHOTO_HOSTS.fullmatch(url) else ""


def _enrich(items: list[dict], title_key: str | None, album_key: str, kind_key: str) -> list[dict]:
    for item in items:
        item["versao"] = classificar(item.get(title_key, "") if title_key else "", item.get(album_key, ""), item.get(kind_key, ""))
        for key in ("capa", "foto"):
            if key in item:
                item[key] = foto_segura(item[key])
    return items


def buscar_albuns(artista: str) -> list[dict]:
    return _enrich(_adapter("albuns", _text(artista, "o nome do artista")), None, "titulo", "tipo")


def edicoes(artista: str, album: str) -> list[dict]:
    return _enrich(_adapter("edicoes", _text(artista, "o nome do artista"), _text(album, "o nome do álbum")), None, "titulo", "tipo")


def buscar_musicas(texto: str) -> list[dict]:
    return _enrich(_adapter("musicas", _text(texto, "o que procurar")), "titulo", "album", "tipo_album")


def faixas(url: str) -> list[dict]:
    url = _text(url, "um link do Spotify")
    if not re.fullmatch(r"https://open\.spotify\.com/(?:intl-[a-z-]+/)?(track|album|playlist)/[A-Za-z0-9]+(?:\?\S*)?", url):
        raise ValueError("Use um link do Spotify de música, álbum ou playlist")
    return _enrich(_adapter("faixas", url, timeout=300), "titulo", "album", "tipo_album")


def _key(artist: str, title: str) -> str:
    return normalized(artist) + "\x1f" + normalized(REMASTER.sub("", title))


def indice_local(pasta: Path) -> set[str]:
    """Artista + título de tudo o que já existe na pasta, para marcar "já tenho"."""
    keys = set()
    for folder, dirs, names in os.walk(pasta, followlinks=False):
        for name in names:
            path = Path(folder) / name
            if path.suffix.casefold() not in (".mp3", ".flac") or name.startswith(TEMP_PREFIXES):
                continue
            try:
                tags = MutagenFile(path, easy=True)
            except Exception:
                tags = None
            titles = list((tags or {}).get("title", []))
            artists = list((tags or {}).get("artist", [])) + list((tags or {}).get("albumartist", []))
            for artist in artists:
                for title in titles:
                    keys.add(_key(artist, title))
            if " - " in path.stem:
                artist, title = path.stem.split(" - ", 1)
                keys.add(_key(artist, title))
    return keys


def ja_tenho(lista: list[dict], indice: set[str]) -> list[dict]:
    for item in lista:
        artists = [item.get("artista", "")] + list(item.get("artistas") or [])
        item["ja_tenho"] = any(_key(a, item.get("titulo", "")) in indice for a in artists if a)
    return lista


def genero_por_regra(artista: str, album: str, regras: list[dict] | None = None) -> str:
    """Mesma prioridade da análise: regra do álbum, depois regra do artista; só se for única."""
    regras = rules(BASE / "genre_rules.csv") if regras is None else regras
    by_artist = [r for r in regras if normalized(r["artista"]) == normalized(artista)]
    specific = [r for r in by_artist if r.get("album") and normalized(r["album"]) == normalized(album_base(album))]
    chosen = specific or [r for r in by_artist if not r.get("album")]
    options = {r["genero"].strip() for r in chosen if r.get("genero", "").strip()}
    return next(iter(options)) if len(options) == 1 else ""


def _fill_genre(path: Path, genre: str) -> bool:
    if not genre or path.suffix.casefold() != ".mp3":
        return False
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    if any(str(v).strip() for f in tags.getall("TCON") for v in f.text):
        return False
    tags.add(TCON(encoding=3, text=[genre]))
    tags.save(path, v2_version=tags.version[1] if tags.version[1] in (3, 4) else 4)
    return True


def _valid_audio(path: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg não encontrado; ele é necessário para o spotDL e para validar o áudio")
    # Sem -xerror o ffmpeg termina com 0 mesmo com erros de decodificação.
    process = subprocess.run([ffmpeg, "-v", "error", "-xerror", "-nostdin", "-i", str(path), "-map", "0:a:0", "-f", "null", "-"], capture_output=True, timeout=180)
    return process.returncode == 0


def _publish(source: Path, final: Path) -> None:
    """Coloca o arquivo pronto no destino sem nunca substituir um que já exista."""
    fd, name = tempfile.mkstemp(prefix=".acervo-baixando-", suffix=final.suffix, dir=final.parent)
    temp = Path(name)
    try:
        os.fchmod(fd, 0o644)  # mkstemp cria 0600; players e outros programas precisam ler
        with os.fdopen(fd, "wb") as handle, source.open("rb") as origin:
            shutil.copyfileobj(origin, handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, final)  # atômico e exclusivo
        except FileExistsError:
            raise
        except OSError:
            # exFAT/FAT e alguns compartilhamentos não têm hard links.
            out = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                with os.fdopen(out, "wb") as handle, source.open("rb") as origin:
                    shutil.copyfileobj(origin, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                final.unlink(missing_ok=True)
                raise
    finally:
        temp.unlink(missing_ok=True)


def _baixar_uma(item: dict, pasta: Path, executable: str, regras: list[dict], bitrate: str, letras: Letras | None = None) -> dict:
    url = item.get("url", "")
    if not re.fullmatch(r"https://open\.spotify\.com/track/[A-Za-z0-9]+", url.split("?")[0]):
        return {**item, "estado": "erro", "mensagem": "Link de faixa inválido"}
    with tempfile.TemporaryDirectory(prefix="acervo-download-") as work:
        work = Path(work)
        # Template relativo com cwd na pasta: o spotDL sanitiza caminhos absolutos.
        command = [executable, "download", url, "--output", TEMPLATE, "--format", "mp3", "--bitrate", bitrate,
                   "--overwrite", "skip", "--threads", "1", "--simple-tui", "--print-errors"]
        try:
            process = subprocess.run(command, cwd=work, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            return {**item, "estado": "erro", "mensagem": "Tempo esgotado"}
        produced = [p for p in work.iterdir() if p.suffix.casefold() == ".mp3" and p.is_file() and not p.is_symlink()]
        lines = [line for line in (process.stdout + process.stderr).splitlines() if line.strip()]
        if process.returncode or len(produced) != 1:
            reason = lines[-1][:300] if lines else f"spotDL terminou com código {process.returncode}"
            return {**item, "estado": "erro", "mensagem": reason if process.returncode or not produced else "O spotDL gerou mais de um arquivo"}
        audio = produced[0]
        if not _valid_audio(audio):
            return {**item, "estado": "erro", "mensagem": "O áudio baixado está corrompido ou incompleto"}
        genre = genero_por_regra(item.get("artista_album") or item.get("artista", ""), item.get("album", ""), regras)
        filled = _fill_genre(audio, genre)
        lyrics = False
        if letras:
            try:
                lyrics = embutir_texto(audio, letras.buscar(item.get("artista", ""), item.get("titulo", ""), item.get("album", ""), item.get("duracao")))
            except RuntimeError:
                lyrics = False  # sem letra o download continua valendo
        final = pasta / audio.name
        try:
            _publish(audio, final)
        except FileExistsError:
            return {**item, "estado": "ja_existia", "arquivo": str(final), "mensagem": "Já existe um arquivo com esse nome na pasta"}
        return {**item, "estado": "baixado", "arquivo": str(final), "genero_preenchido": genre if filled else "", "letra": lyrics}


def baixar(lista: list[dict], pasta: Path, progresso=None, bitrate: str = "320k", letras: Letras | bool | None = True) -> list[dict]:
    """Baixa cada faixa direto em `pasta`, sem nunca substituir um arquivo existente.

    O spotDL trabalha numa pasta temporária fora da coleção; o arquivo só aparece
    no destino depois de o spotDL terminar com sucesso e o áudio ser validado.
    Erros ficam registrados por faixa e o resultado parcial é sempre devolvido.
    """
    pasta = Path(pasta).expanduser()
    if not pasta.is_dir():
        raise ValueError(f"A pasta de destino não existe: {pasta}")
    if bitrate not in ("128k", "192k", "256k", "320k"):
        raise ValueError("Bitrate inválido")
    progresso = progresso or (lambda *args: None)
    regras = rules(BASE / "genre_rules.csv")
    executable = spotdl_executable()
    letras = Letras() if letras is True else (letras or None)
    results = []
    for index, item in enumerate(lista):
        label = f"{item.get('artista', '')} - {item.get('titulo', '')}"
        progresso(f"Baixando {index + 1} de {len(lista)} · {label}", round(index / len(lista) * 100))
        try:
            results.append(_baixar_uma(item, pasta, executable, regras, bitrate, letras))
        except Exception as error:
            results.append({**item, "estado": "erro", "mensagem": f"{type(error).__name__}: {error}"[:300]})
            # Sem ffmpeg ou sem espaço/escrita no destino, as próximas falhariam igual.
            if isinstance(error, RuntimeError) or (isinstance(error, OSError) and error.errno in (errno.ENOSPC, errno.EROFS, errno.EDQUOT, errno.EACCES)):
                results.extend({**rest, "estado": "nao_baixado", "mensagem": "Interrompido após o erro anterior"} for rest in lista[index + 1:])
                break
    progresso("Downloads concluídos", 100)
    return results
