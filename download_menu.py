#!/usr/bin/env python3
"""Terminal menu to pick and download songs with spotDL."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

import downloader
from messages import _, register, use_environment_language
from folders import require_folder, default_music_folder


register({
    'Could not understand “{part}”. Use numbers such as 1-3,5': 'Não entendi “{part}”. Use números como 1-3,5',
    'Choose numbers between 1 and {count}': 'Escolha números entre 1 e {count}',
    'Number (enter to go back): ': 'Número (enter para voltar): ',
    'Choose a number between 1 and {count}.': 'Escolha um número entre 1 e {count}.',
    'Checking what is already in the folder…': 'Conferindo o que já existe na pasta…',
    'Nothing found.': 'Nada encontrado.',
    'already have': 'já tenho',
    'Which to download? [enter = {missing} missing · all · 1-3,5 · b = back]: ': 'Quais baixar? [enter = {missing} que faltam · todas · 1-3,5 · v = voltar]: ',
    'Nothing to download: you already have them all.': 'Nada para baixar: você já tem todas.',
    'Download {count} track(s) to {folder}? [Y/n] ': 'Baixar {count} faixa(s) em {folder}? [S/n] ',
    'Downloaded: {done} of {total}': 'Baixadas: {done} de {total}',
    'Artist: ': 'Artista: ',
    'Searching albums…': 'Procurando álbuns…',
    "No album found. Try the artist's exact name or search for a song.": 'Nenhum álbum encontrado. Tente o nome exato do artista ou procure por música.',
    'Searching editions…': 'Procurando edições…',
    "That album was not found in spotDL's catalog. Try searching for a song.": 'Esse álbum não foi encontrado no catálogo do spotDL. Tente procurar por música.',
    'Loading tracks…': 'Carregando faixas…',
    'Song (e.g. Kyuss Gardenia): ': 'Música (ex.: Kyuss Gardenia): ',
    'Searching…': 'Procurando…',
    'Spotify link (song, album or playlist): ': 'Link do Spotify (música, álbum ou playlist): ',
    'Loading…': 'Carregando…',
    'Option: ': 'Opção: ',
    'Error: {error}': 'Erro: {error}',
    'Cancelled.': 'Cancelado.',
    'Destination: {folder}': 'Destino: {folder}',
    'Search an artist and pick an album': 'Procurar artista e escolher álbum',
    'Search a song': 'Procurar música',
    'Paste a Spotify link': 'Colar link do Spotify',
    'Quit': 'Sair',
    'Where to save (default: the system music folder, e.g. ~/Music)': 'Onde salvar (padrão: a pasta de músicas do sistema, ex.: ~/Música)',
    'Terminal menu to pick and download songs with spotDL.': 'Menu no terminal para escolher e baixar músicas com o spotDL.',
})


def parse_selection(text: str, items: list[dict]) -> list[int]:
    """Accept "1-3,5", "todas" or "faltantes" (empty = faltantes). Return 0-based indexes."""
    text = text.strip().casefold()
    if text in ("", "missing", "faltantes", "f"):
        return [i for i, item in enumerate(items) if not item.get("ja_tenho")]
    if text in ("all", "todas", "t", "*"):
        return list(range(len(items)))
    chosen = []
    for part in text.replace(" ", "").split(","):
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError(_("Could not understand “{part}”. Use numbers such as 1-3,5", part=part))
        start, end = int(match.group(1)), int(match.group(2) or match.group(1))
        if start < 1 or end > len(items) or start > end:
            raise ValueError(_("Choose numbers between 1 and {count}", count=len(items)))
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
        answer = ask(_("Number (enter to go back): ")).strip()
        if not answer:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            return items[int(answer) - 1]
        print("  " + _("Choose a number between 1 and {count}.", count=len(items)))


def minutes(seconds) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds else ""


class Menu:
    def __init__(self, folder: Path):
        self.folder = folder
        self.index = None

    def known(self, items: list[dict]) -> list[dict]:
        if self.index is None:
            print(_("Checking what is already in the folder…"))
            self.index = downloader.local_index(self.folder)
        return downloader.mark_owned(items, self.index)

    def pick_and_download(self, items: list[dict], with_album: bool) -> None:
        if not items:
            print(_("Nothing found."))
            return
        self.known(items)
        for number, item in enumerate(items, 1):
            mark = "  [" + _("already have") + "]" if item["ja_tenho"] else ""
            album = f"  · {item['album']}" + (f" ({item['ano']})" if item.get("ano") else "") if with_album else ""
            print(f"  {number:>2}) {item['artista']} - {item['titulo']}  {minutes(item.get('duracao'))}{album}{mark}")
        missing = sum(not i["ja_tenho"] for i in items)
        while True:
            answer = ask(_("Which to download? [enter = {missing} missing · all · 1-3,5 · b = back]: ", missing=missing))
            if answer.strip().casefold() in ("b", "v"):
                return
            try:
                chosen = parse_selection(answer, items)
                break
            except ValueError as error:
                print(f"  {error}")
        if not chosen:
            print(_("Nothing to download: you already have them all."))
            return
        if ask(_("Download {count} track(s) to {folder}? [Y/n] ", count=len(chosen), folder=self.folder)).strip().casefold() not in ("", "y", "yes", "s", "sim"):
            return
        results = downloader.download([items[i] for i in chosen], self.folder, lambda text, percent: print(f"  [{percent:>3}%] {text}"))
        self.index = None  # the folder changed
        done = [r for r in results if r["estado"] == "baixado"]
        print("\n" + _("Downloaded: {done} of {total}", done=len(done), total=len(results)))
        for result in results:
            if result["estado"] == "baixado":
                genre = f"  (gênero: {result['genero_preenchido']})" if result.get("genero_preenchido") else ""
                print(f"  ✓ {Path(result['arquivo']).name}{genre}")
            else:
                print(f"  ✗ {result['artista']} - {result['titulo']}: {result.get('mensagem', result['estado'])}")

    def by_artist(self) -> None:
        artist = ask(_("Artist: ")).strip()
        if not artist:
            return
        print(_("Searching albums…"))
        albums = downloader.search_albums(artist)
        if not albums:
            print(_("No album found. Try the artist's exact name or search for a song."))
            return
        album = choose_one(albums, lambda a: f"{a['titulo']}" + (f" ({a['ano']})" if a.get("ano") else ""))
        if not album:
            return
        print(_("Searching editions…"))
        editions = downloader.editions(artist, album["titulo"])
        if not editions:
            print(_("That album was not found in spotDL's catalog. Try searching for a song."))
            return
        edition = editions[0] if len(editions) == 1 else choose_one(editions, lambda e: f"{e['titulo']}  · {e['artista']}")
        if edition:
            print(_("Loading tracks…"))
            self.pick_and_download(downloader.tracks(edition["url"]), with_album=False)

    def by_song(self) -> None:
        text = ask(_("Song (e.g. Kyuss Gardenia): ")).strip()
        if text:
            print(_("Searching…"))
            self.pick_and_download(downloader.search_songs(text), with_album=True)

    def by_link(self) -> None:
        url = ask(_("Spotify link (song, album or playlist): ")).strip()
        if url:
            print(_("Loading…"))
            self.pick_and_download(downloader.tracks(url), with_album=True)

    def run(self) -> None:
        actions = {"1": self.by_artist, "2": self.by_song, "3": self.by_link}
        while True:
            print("\n" + _("Destination: {folder}", folder=self.folder) + "\n  1) " + _("Search an artist and pick an album") + "\n  2) " + _("Search a song") + "\n  3) " + _("Paste a Spotify link") + "\n  0) " + _("Quit"))
            choice = ask(_("Option: ")).strip()
            if choice in ("0", "q", "sair"):
                return
            if choice in actions:
                try:
                    actions[choice]()
                except (ValueError, RuntimeError) as error:
                    print(_("Error: {error}", error=error))
                except KeyboardInterrupt:
                    print("\n" + _("Cancelled."))


def main() -> int:
    use_environment_language()
    parser = argparse.ArgumentParser(description=_(__doc__))
    parser.add_argument("--folder", "--pasta", dest="folder", type=Path, default=default_music_folder(), help=_("Where to save (default: the system music folder, e.g. ~/Music)"))
    args = parser.parse_args()
    try:
        folder = require_folder(args.folder)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    try:
        downloader.spotdl_executable()
        Menu(folder).run()
    except RuntimeError as error:
        print(_("Error: {error}", error=error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
