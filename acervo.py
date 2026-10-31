#!/usr/bin/env python3
"""Organizador local: analisar, aprovar alterações, aplicar com backup, restaurar e exportar playlists."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from library_core import apply_plan, restore
from library_plan import create_plan
from musicbrainz_client import MusicBrainz
from folders import exigir_pasta, pasta_musicas_padrao


PROGRAM = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="comando", required=True)
    analyze = commands.add_parser("analisar", help="Gerar inventário e prévia; não modifica músicas")
    analyze.add_argument("--pasta", type=Path, required=True)
    analyze.add_argument("--saida", type=Path, default=Path("relatorios") / datetime.now().strftime("previa_%Y%m%d_%H%M%S"))
    analyze.add_argument("--normalizar-albuns", action="store_true")
    analyze.add_argument("--generos", type=Path)
    analyze.add_argument("--albuns", type=Path)
    analyze.add_argument("--musicbrainz", action="store_true")
    analyze.add_argument("--contato", default="")
    analyze.add_argument("--max-consultas", type=int, default=200)
    analyze.add_argument("--dados", type=Path, default=PROGRAM, help="Pasta de dados do programa (campos protegidos)")
    apply = commands.add_parser("aplicar", help="Aplicar somente linhas com aprovar=sim")
    apply.add_argument("--plano", type=Path, required=True)
    apply.add_argument("--aprovacoes", type=Path, required=True)
    apply.add_argument("--dados", type=Path, default=PROGRAM, help="Pasta de dados do programa (campos protegidos e backups)")
    apply.add_argument("--backups", type=Path, help="Padrão: <dados>/backups")
    undo = commands.add_parser("restaurar", help="Restaurar arquivos exatos de uma execução")
    undo.add_argument("--manifesto", type=Path, required=True)
    undo.add_argument("--dados", type=Path, help="Padrão: a pasta que contém backups/<execução>")
    for command, text in (("proteger", "Proteger campos de faixas contra sugestões e gravações"), ("desproteger", "Remover a proteção de campos")):
        lock = commands.add_parser(command, help=text)
        lock.add_argument("--pasta", type=Path, required=True)
        lock.add_argument("--arquivo", type=Path, action="append", required=True, help="Faixa da coleção; repita para várias")
        lock.add_argument("--campos", required=True, help="Lista separada por vírgulas, ex.: genero,album")
        lock.add_argument("--dados", type=Path, default=PROGRAM)
    locks = commands.add_parser("protecoes", help="Listar campos protegidos")
    locks.add_argument("--pasta", type=Path, required=True)
    locks.add_argument("--dados", type=Path, default=PROGRAM)
    playlist = commands.add_parser("playlist", help="Exportar uma playlist M3U8 portátil; não modifica músicas")
    playlist.add_argument("--pasta", type=Path, help="Coleção a ler (obrigatório ao exportar)")
    playlist.add_argument("--saida", type=Path, required=True, help="Arquivo .m3u8 a criar, ou a validar com --validar")
    playlist.add_argument("--nome")
    playlist.add_argument("--genero")
    playlist.add_argument("--artista", help="Artista ou artista do álbum")
    playlist.add_argument("--album")
    playlist.add_argument("--sobrescrever", action="store_true")
    playlist.add_argument("--validar", action="store_true", help="Somente conferir os caminhos de uma playlist existente")
    serve = commands.add_parser("interface", help="Abrir o menu HTML local")
    serve.add_argument("--pasta", type=Path, default=pasta_musicas_padrao(), help="Padrão: a pasta de músicas do sistema (ex.: ~/Música)")
    serve.add_argument("--porta", type=int, default=8765)
    serve.add_argument("--dados", type=Path, help="Pasta externa para relatórios e backups")
    serve.add_argument("--abrir", action="store_true", help="Abrir no navegador padrão")
    args = parser.parse_args()
    try:
        if args.comando == "analisar":
            online = MusicBrainz(Path(__file__).resolve().parent / ".biblioteca/cache/musicbrainz", args.contato, args.max_consultas) if args.musicbrainz else None
            def progress(done, total, name):
                if done % 100 == 0 or done == total:
                    print(f"[{done}/{total}] {name}", file=sys.stderr)
            result = create_plan(args.pasta, args.saida, args.normalizar_albuns, args.generos, args.albuns, online, progress, args.dados.expanduser())
        elif args.comando == "aplicar":
            data = args.dados.expanduser()
            result = apply_plan(args.plano.expanduser().resolve(), args.aprovacoes.expanduser().resolve(), args.backups or data / "backups", data)
        elif args.comando == "restaurar":
            result = restore(args.manifesto.expanduser(), args.dados.expanduser() if args.dados else None)
        elif args.comando in ("proteger", "desproteger"):
            import protections
            change = protections.protect if args.comando == "proteger" else protections.unprotect
            files = [str(p.expanduser().resolve()) for p in args.arquivo]
            result = change(args.dados.expanduser(), args.pasta.expanduser().resolve(strict=True), files, [c.strip() for c in args.campos.split(",") if c.strip()])
        elif args.comando == "protecoes":
            import protections
            root = args.pasta.expanduser().resolve(strict=True)
            result = {"politica": str(protections.policy_path(args.dados.expanduser(), root)), "faixas": protections.listing(protections.load(args.dados.expanduser(), root))}
        elif args.comando == "playlist":
            from playlists import album_order, collect, matches, validate_m3u8, write_m3u8
            if args.validar:
                result = validate_m3u8(args.saida)
            else:
                if not args.pasta:
                    raise ValueError("Informe --pasta para exportar")
                def progress(done, total, name):
                    if done % 100 == 0 or done == total:
                        print(f"[{done}/{total}] {name}", file=sys.stderr)
                tracks, errors = collect(args.pasta.expanduser().resolve(strict=True), progress)
                chosen = sorted((t for t in tracks if matches(t, "genero", args.genero) and matches(t, "album", args.album) and (matches(t, "artista", args.artista) or matches(t, "artista_album", args.artista))), key=album_order)
                result = write_m3u8(args.saida, chosen, args.nome, args.sobrescrever)
                result["erros_de_leitura"] = errors
        else:
            from server import serve
            args.pasta = exigir_pasta(args.pasta)
            serve(args.pasta, args.porta, args.dados, args.abrir)
            return 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("resumo", {}).get("erros") or result.get("ausentes") else 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
