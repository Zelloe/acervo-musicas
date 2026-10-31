#!/usr/bin/env python3
"""Acervo command line: analyze, approve, apply with backup, restore and export playlists."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from library_core import apply_plan, restore
from library_plan import create_plan
from messages import _, register
from musicbrainz_client import MusicBrainz
from folders import require_folder, default_music_folder


PROGRAM = Path(__file__).resolve().parent

register({
    "Acervo command line: analyze, approve, apply with backup, restore and export playlists.":
        "Linha de comando do Acervo: analisar, aprovar, aplicar com backup, restaurar e exportar playlists.",
    "Create an inventory and a preview; never modifies music": "Gerar inventário e prévia; não modifica músicas",
    "Apply only the rows approved with aprovar=sim": "Aplicar somente linhas com aprovar=sim",
    "Restore the exact files of a run": "Restaurar arquivos exatos de uma execução",
    "Protect track fields from suggestions and writes": "Proteger campos de faixas contra sugestões e gravações",
    "Remove the protection from fields": "Remover a proteção de campos",
    "List protected fields": "Listar campos protegidos",
    "Export a portable M3U8 playlist; never modifies music": "Exportar uma playlist M3U8 portátil; não modifica músicas",
    "Open the local web interface": "Abrir a interface web local",
    "Music collection folder": "Pasta da coleção de músicas",
    "Program data folder (protected fields, backups, reports)": "Pasta de dados do programa (campos protegidos, backups, relatórios)",
    "Default: <data>/backups": "Padrão: <dados>/backups",
    "Default: the folder that contains backups/<run>": "Padrão: a pasta que contém backups/<execução>",
    "Collection track; repeat for several": "Faixa da coleção; repita para várias",
    "Comma-separated list, e.g. genero,album": "Lista separada por vírgulas, ex.: genero,album",
    "Collection to read (required when exporting)": "Coleção a ler (obrigatório ao exportar)",
    ".m3u8 file to create, or to check with --validate": "Arquivo .m3u8 a criar, ou a conferir com --validate",
    "Artist or album artist": "Artista ou artista do álbum",
    "Only check the paths of an existing playlist": "Somente conferir os caminhos de uma playlist existente",
    "Default: the system music folder (e.g. ~/Music)": "Padrão: a pasta de músicas do sistema (ex.: ~/Música)",
    "Open in the default browser": "Abrir no navegador padrão",
    "Use --folder to export": "Informe --folder para exportar",
    "Error: {error}": "Erro: {error}",
})

# Portuguese names keep working as aliases of the English ones.
COMMANDS = {
    "analyze": ["analisar"], "apply": ["aplicar"], "restore": ["restaurar"], "protect": ["proteger"],
    "unprotect": ["desproteger"], "protections": ["protecoes"], "playlist": [], "serve": ["interface"],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=_(__doc__))
    commands = parser.add_subparsers(dest="command", required=True, metavar="{" + ",".join(COMMANDS) + "}")

    def command(name, help_text):
        sub = commands.add_parser(name, aliases=COMMANDS[name], help=_(help_text))
        sub.set_defaults(command=name)
        return sub

    def data_option(sub, default=PROGRAM, help_text="Program data folder (protected fields, backups, reports)"):
        sub.add_argument("--data", "--dados", dest="data", type=Path, default=default, help=_(help_text))

    analyze = command("analyze", "Create an inventory and a preview; never modifies music")
    analyze.add_argument("--folder", "--pasta", dest="folder", type=Path, required=True, help=_("Music collection folder"))
    analyze.add_argument("--output", "--saida", dest="output", type=Path, default=Path("relatorios") / datetime.now().strftime("previa_%Y%m%d_%H%M%S"))
    analyze.add_argument("--normalize-albums", "--normalizar-albuns", dest="normalize_albums", action="store_true")
    analyze.add_argument("--genre-rules", "--generos", dest="genre_rules", type=Path)
    analyze.add_argument("--album-rules", "--albuns", dest="album_rules", type=Path)
    analyze.add_argument("--musicbrainz", action="store_true")
    analyze.add_argument("--contact", "--contato", dest="contact", default="")
    analyze.add_argument("--max-queries", "--max-consultas", dest="max_queries", type=int, default=200)
    data_option(analyze)

    apply = command("apply", "Apply only the rows approved with aprovar=sim")
    apply.add_argument("--plan", "--plano", dest="plan", type=Path, required=True)
    apply.add_argument("--approvals", "--aprovacoes", dest="approvals", type=Path, required=True)
    data_option(apply)
    apply.add_argument("--backups", type=Path, help=_("Default: <data>/backups"))

    undo = command("restore", "Restore the exact files of a run")
    undo.add_argument("--manifest", "--manifesto", dest="manifest", type=Path, required=True)
    data_option(undo, None, "Default: the folder that contains backups/<run>")

    for name, text in (("protect", "Protect track fields from suggestions and writes"), ("unprotect", "Remove the protection from fields")):
        lock = command(name, text)
        lock.add_argument("--folder", "--pasta", dest="folder", type=Path, required=True, help=_("Music collection folder"))
        lock.add_argument("--file", "--arquivo", dest="file", type=Path, action="append", required=True, help=_("Collection track; repeat for several"))
        lock.add_argument("--fields", "--campos", dest="fields", required=True, help=_("Comma-separated list, e.g. genero,album"))
        data_option(lock)

    locks = command("protections", "List protected fields")
    locks.add_argument("--folder", "--pasta", dest="folder", type=Path, required=True, help=_("Music collection folder"))
    data_option(locks)

    playlist = command("playlist", "Export a portable M3U8 playlist; never modifies music")
    playlist.add_argument("--folder", "--pasta", dest="folder", type=Path, help=_("Collection to read (required when exporting)"))
    playlist.add_argument("--output", "--saida", dest="output", type=Path, required=True, help=_(".m3u8 file to create, or to check with --validate"))
    playlist.add_argument("--name", "--nome", dest="name")
    playlist.add_argument("--genre", "--genero", dest="genre")
    playlist.add_argument("--artist", "--artista", dest="artist", help=_("Artist or album artist"))
    playlist.add_argument("--album")
    playlist.add_argument("--overwrite", "--sobrescrever", dest="overwrite", action="store_true")
    playlist.add_argument("--validate", "--validar", dest="validate", action="store_true", help=_("Only check the paths of an existing playlist"))

    serve = command("serve", "Open the local web interface")
    serve.add_argument("--folder", "--pasta", dest="folder", type=Path, default=default_music_folder(), help=_("Default: the system music folder (e.g. ~/Music)"))
    serve.add_argument("--port", "--porta", dest="port", type=int, default=8765)
    data_option(serve, None)
    serve.add_argument("--open", "--abrir", dest="open", action="store_true", help=_("Open in the default browser"))

    args = parser.parse_args()

    def progress(done, total, name):
        if done % 100 == 0 or done == total:
            print(f"[{done}/{total}] {name}", file=sys.stderr)

    try:
        if args.command == "analyze":
            online = MusicBrainz(PROGRAM / ".biblioteca/cache/musicbrainz", args.contact, args.max_queries) if args.musicbrainz else None
            result = create_plan(args.folder, args.output, args.normalize_albums, args.genre_rules, args.album_rules, online, progress, args.data.expanduser())
        elif args.command == "apply":
            data = args.data.expanduser()
            result = apply_plan(args.plan.expanduser().resolve(), args.approvals.expanduser().resolve(), args.backups or data / "backups", data)
        elif args.command == "restore":
            result = restore(args.manifest.expanduser(), args.data.expanduser() if args.data else None)
        elif args.command in ("protect", "unprotect"):
            import protections
            change = protections.protect if args.command == "protect" else protections.unprotect
            files = [str(p.expanduser().resolve()) for p in args.file]
            result = change(args.data.expanduser(), args.folder.expanduser().resolve(strict=True), files, [c.strip() for c in args.fields.split(",") if c.strip()])
        elif args.command == "protections":
            import protections
            root = args.folder.expanduser().resolve(strict=True)
            result = {"politica": str(protections.policy_path(args.data.expanduser(), root)), "faixas": protections.listing(protections.load(args.data.expanduser(), root))}
        elif args.command == "playlist":
            from playlists import album_order, collect, matches, validate_m3u8, write_m3u8
            if args.validate:
                result = validate_m3u8(args.output)
            else:
                if not args.folder:
                    raise ValueError(_("Use --folder to export"))
                tracks, errors = collect(args.folder.expanduser().resolve(strict=True), progress)
                chosen = sorted((t for t in tracks if matches(t, "genero", args.genre) and matches(t, "album", args.album) and (matches(t, "artista", args.artist) or matches(t, "artista_album", args.artist))), key=album_order)
                result = write_m3u8(args.output, chosen, args.name, args.overwrite)
                result["erros_de_leitura"] = errors
        else:
            from server import serve
            serve(require_folder(args.folder), args.port, args.data, args.open)
            return 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("resumo", {}).get("erros") or result.get("ausentes") else 0
    except (OSError, ValueError, RuntimeError) as error:
        print(_("Error: {error}", error=error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
