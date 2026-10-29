#!/usr/bin/env python3
"""Menu no terminal para escolher e baixar músicas com o spotDL."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

import baixador
from pastas import exigir_pasta, pasta_musicas_padrao


def parse_selection(text: str, items: list[dict]) -> list[int]:
    """Aceita "1-3,5", "todas" ou "faltantes" (vazio = faltantes). Devolve índices de 0."""
    text = text.strip().casefold()
    if text in ("", "faltantes", "f"):
        return [i for i, item in enumerate(items) if not item.get("ja_tenho")]
    if text in ("todas", "t", "*"):
        return list(range(len(items)))
    chosen = []
    for part in text.replace(" ", "").split(","):
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError(f"Não entendi “{part}”. Use números como 1-3,5")
        start, end = int(match.group(1)), int(match.group(2) or match.group(1))
        if start < 1 or end > len(items) or start > end:
            raise ValueError(f"Escolha números entre 1 e {len(items)}")
        chosen.extend(i - 1 for i in range(start, end + 1) if i - 1 not in chosen)
    return chosen


def ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        raise SystemExit(0)


def choose_one(items: list[dict], show) -> dict | None:
    for number, item in enumerate(items, 1):
        print(f"  {number:>2}) {show(item)}")
    while True:
        answer = ask("Número (enter para voltar): ").strip()
        if not answer:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            return items[int(answer) - 1]
        print(f"  Escolha um número entre 1 e {len(items)}.")


def minutes(seconds) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds else ""


class Menu:
    def __init__(self, folder: Path):
        self.folder = folder
        self.index = None

    def known(self, items: list[dict]) -> list[dict]:
        if self.index is None:
            print("Conferindo o que já existe na pasta…")
            self.index = baixador.indice_local(self.folder)
        return baixador.ja_tenho(items, self.index)

    def pick_and_download(self, items: list[dict], with_album: bool) -> None:
        if not items:
            print("Nada encontrado.")
            return
        self.known(items)
        for number, item in enumerate(items, 1):
            mark = "  [já tenho]" if item["ja_tenho"] else ""
            album = f"  · {item['album']}" + (f" ({item['ano']})" if item.get("ano") else "") if with_album else ""
            print(f"  {number:>2}) {item['artista']} - {item['titulo']}  {minutes(item.get('duracao'))}{album}{mark}")
        missing = sum(not i["ja_tenho"] for i in items)
        while True:
            answer = ask(f"Quais baixar? [enter = {missing} que faltam · todas · 1-3,5 · v = voltar]: ")
            if answer.strip().casefold() == "v":
                return
            try:
                chosen = parse_selection(answer, items)
                break
            except ValueError as error:
                print(f"  {error}")
        if not chosen:
            print("Nada para baixar: você já tem todas.")
            return
        if ask(f"Baixar {len(chosen)} faixa(s) em {self.folder}? [S/n] ").strip().casefold() not in ("", "s", "sim"):
            return
        results = baixador.baixar([items[i] for i in chosen], self.folder, lambda text, percent: print(f"  [{percent:>3}%] {text}"))
        self.index = None  # a pasta mudou
        done = [r for r in results if r["estado"] == "baixado"]
        print(f"\nBaixadas: {len(done)} de {len(results)}")
        for result in results:
            if result["estado"] == "baixado":
                genre = f"  (gênero: {result['genero_preenchido']})" if result.get("genero_preenchido") else ""
                print(f"  ✓ {Path(result['arquivo']).name}{genre}")
            else:
                print(f"  ✗ {result['artista']} - {result['titulo']}: {result.get('mensagem', result['estado'])}")

    def by_artist(self) -> None:
        artist = ask("Artista: ").strip()
        if not artist:
            return
        print("Procurando álbuns…")
        albums = baixador.buscar_albuns(artist)
        if not albums:
            print("Nenhum álbum encontrado. Tente o nome exato do artista ou procure por música.")
            return
        album = choose_one(albums, lambda a: f"{a['titulo']}" + (f" ({a['ano']})" if a.get("ano") else ""))
        if not album:
            return
        print("Procurando edições…")
        editions = baixador.edicoes(artist, album["titulo"])
        if not editions:
            print("Esse álbum não foi encontrado no catálogo do spotDL. Tente procurar por música.")
            return
        edition = editions[0] if len(editions) == 1 else choose_one(editions, lambda e: f"{e['titulo']}  · {e['artista']}")
        if edition:
            print("Carregando faixas…")
            self.pick_and_download(baixador.faixas(edition["url"]), with_album=False)

    def by_song(self) -> None:
        text = ask("Música (ex.: Kyuss Gardenia): ").strip()
        if text:
            print("Procurando…")
            self.pick_and_download(baixador.buscar_musicas(text), with_album=True)

    def by_link(self) -> None:
        url = ask("Link do Spotify (música, álbum ou playlist): ").strip()
        if url:
            print("Carregando…")
            self.pick_and_download(baixador.faixas(url), with_album=True)

    def run(self) -> None:
        actions = {"1": self.by_artist, "2": self.by_song, "3": self.by_link}
        while True:
            print(f"\nDestino: {self.folder}\n  1) Procurar artista e escolher álbum\n  2) Procurar música\n  3) Colar link do Spotify\n  0) Sair")
            choice = ask("Opção: ").strip()
            if choice in ("0", "q", "sair"):
                return
            if choice in actions:
                try:
                    actions[choice]()
                except (ValueError, RuntimeError) as error:
                    print(f"Erro: {error}")
                except KeyboardInterrupt:
                    print("\nCancelado.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pasta", type=Path, default=pasta_musicas_padrao(), help="Onde salvar (padrão: a pasta de músicas do sistema, ex.: ~/Música)")
    args = parser.parse_args()
    try:
        folder = exigir_pasta(args.pasta)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    try:
        baixador.spotdl_executable()
        Menu(folder).run()
    except RuntimeError as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
