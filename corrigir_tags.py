#!/usr/bin/env python3
"""Organizador local: analisar, aprovar alterações, aplicar com backup e restaurar."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from biblioteca_core import apply_plan, restore
from biblioteca_plano import create_plan
from musicbrainz_local import MusicBrainz


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
    apply = commands.add_parser("aplicar", help="Aplicar somente linhas com aprovar=sim")
    apply.add_argument("--plano", type=Path, required=True)
    apply.add_argument("--aprovacoes", type=Path, required=True)
    apply.add_argument("--backups", type=Path, default=Path(__file__).resolve().parent / "backups")
    undo = commands.add_parser("restaurar", help="Restaurar arquivos exatos de uma execução")
    undo.add_argument("--manifesto", type=Path, required=True)
    serve = commands.add_parser("interface", help="Abrir o menu HTML local")
    serve.add_argument("--pasta", type=Path, default=Path.home() / "Music")
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
            result = create_plan(args.pasta, args.saida, args.normalizar_albuns, args.generos, args.albuns, online, progress)
        elif args.comando == "aplicar":
            result = apply_plan(args.plano.expanduser().resolve(), args.aprovacoes.expanduser().resolve(), args.backups)
        elif args.comando == "restaurar":
            result = restore(args.manifesto.expanduser())
        else:
            from servidor import serve
            serve(args.pasta, args.porta, args.dados, args.abrir)
            return 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("resumo", {}).get("erros") else 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
