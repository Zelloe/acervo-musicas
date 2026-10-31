# Como contribuir

[English](CONTRIBUTING.md) · **Português**

Obrigado pelo interesse no Acervo. Este guia resume o que é preciso saber antes
de abrir uma contribuição.

## Princípio central

O Acervo lida com a coleção de música de outras pessoas. Toda mudança precisa
respeitar estas garantias:

- Nada é gravado na coleção sem uma prévia e uma aprovação explícita.
- Antes de qualquer gravação existe um backup completo e verificável, e a
  restauração devolve o arquivo original byte por byte.
- Nada é excluído, renomeado, movido ou reconvertido automaticamente.
- Capas, tags não aprovadas, versões legítimas (ao vivo, demo, remix, bônus) e
  o fluxo de áudio são preservados.
- Identificações ambíguas vão para revisão, com a fonte registrada.
- O uso não depende de Spotify Premium nem de serviços pagos.

Uma contribuição que enfraqueça qualquer uma dessas garantias não será aceita,
mesmo que deixe o programa mais rápido ou mais simples.

## Ambiente

Requisitos: Python 3.10 ou superior e `ffmpeg` (usado pelos testes para gerar
áudio temporário).

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

A interface é HTML, CSS e JavaScript puros, sem npm e sem etapa de compilação.
Para conferir a sintaxe dos scripts:

```bash
for f in web/*.js; do node --check "$f"; done
```

## Idiomas

Código, comentários e o texto-fonte de todas as mensagens ficam em inglês. O
Acervo é distribuído em inglês e em português do Brasil:

- **Python:** escreva mensagens como `_("Text with {name}", name=valor)` (de
  `messages.py`, nunca com f-string dentro de `_()`) e acrescente o português
  no catálogo `register({...})` do módulo. O núcleo sempre produz inglês; só os
  pontos de entrada escolhem o idioma (o servidor por requisição, a linha de
  comando pelo `LANG`).
- **Interface web:** textos para o usuário passam por `tr()` e precisam de uma
  entrada em `web/i18n.js`. Títulos, artistas, álbuns, caminhos e letras são
  dados e nunca são traduzidos.
- **Dados gravados:** as chaves dos arquivos que o Acervo grava (manifestos,
  planos, proteções, lotes) mantêm os nomes atuais, muitos em português, para
  que backups antigos continuem restauráveis. O `api_contract.py` traduz essas
  chaves para a API HTTP em inglês.

## Testes

- **Nunca use a sua coleção real nos testes.** Gere arquivos temporários com
  ffmpeg, como a suíte existente faz em `tests/test_library.py`.
- Toda mudança em gravação, importação ou restauração precisa de testes que
  demonstrem a preservação do que não foi aprovado e a restauração do original.
- Consultas a serviços externos (MusicBrainz, LRCLIB, spotDL, yt-dlp) devem ser
  simuladas nos testes. O CI não acessa a rede para buscar músicas.

## Organização do código

| Arquivo | Responsabilidade |
| --- | --- |
| `acervo.py` | Linha de comando: `analyze`, `apply`, `restore`, `serve`, `playlist`, `protect`, `unprotect`, `protections` (os nomes em português também funcionam) |
| `library_scan.py` | Leitura e inventário, sem gravar |
| `library_plan.py` | Regras, propostas e plano de aprovação |
| `library_core.py` | Gravação validada, backups e restauração |
| `library_import.py` | Importação transacional e desfazer |
| `staged_downloads.py` | Fluxo avançado de downloads (área de revisão) |
| `downloader.py`, `download_menu.py`, `spotdl_search.py` | Download direto com spotDL (núcleo, terminal e adaptador) |
| `download_picker.py` | Página Baixar do servidor |
| `search.py` | Ranking da biblioteca e sugestões globais |
| `versions.py` | Classificação estúdio/ao vivo/acústico/demo/remix |
| `lyrics.py` | Letras do arquivo e do LRCLIB; gravação com backup |
| `protections.py` | Campos protegidos |
| `playlists.py` | Playlists M3U8 |
| `messages.py` | Catálogo de mensagens e escolha de idioma |
| `api_contract.py` | Traduz as chaves dos dados gravados para a API HTTP em inglês |
| `musicbrainz_client.py` | Cliente MusicBrainz opcional, com cache e limite |
| `server.py` e `web/` | Servidor local e interface |

O planejamento das próximas funcionalidades está em [ROADMAP.pt-BR.md](ROADMAP.pt-BR.md).
Só marque um item como concluído depois de cumprir o critério descrito lá.

## Regras de gênero e álbum

`genre_rules.csv` e `album_rules.csv` são regras de curadoria local.
Ao propor novas regras:

- Prefira gêneros específicos e úteis (por exemplo, `Thrash Metal` em vez de
  `Rock`).
- Use regras por álbum quando a discografia do artista muda de estilo.
- Não apresente uma regra local como identificação confirmada da gravação.

## Pull requests

- Uma mudança por pull request, com descrição do problema e de como foi testada.
- Não inclua arquivos de áudio, capas extraídas, relatórios, backups, cookies,
  credenciais nem caminhos da sua máquina.
- Mensagens de commit curtas, no estilo `tipo: descrição` (`feat`, `fix`,
  `docs`, `test`, `refactor`).

## Licença

Ao contribuir, você concorda em licenciar sua contribuição sob a
[licença MIT](LICENSE) do projeto.
