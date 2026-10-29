# Como contribuir

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

## Testes

- **Nunca use a sua coleção real nos testes.** Gere arquivos temporários com
  ffmpeg, como a suíte existente faz em `tests/test_biblioteca.py`.
- Toda mudança em gravação, importação ou restauração precisa de testes que
  demonstrem a preservação do que não foi aprovado e a restauração do original.
- Consultas a serviços externos (MusicBrainz, spotDL, yt-dlp) devem ser
  simuladas nos testes. O CI não acessa a rede para buscar músicas.

## Organização do código

| Arquivo | Responsabilidade |
| --- | --- |
| `corrigir_tags.py` | Linha de comando: `analisar`, `aplicar`, `restaurar`, `interface` |
| `diagnostico_biblioteca.py` | Leitura e inventário, sem gravar |
| `biblioteca_plano.py` | Regras, propostas e plano de aprovação |
| `biblioteca_core.py` | Gravação validada, backups e restauração |
| `biblioteca_downloads.py` | Catálogos, comparação e downloads fora da coleção |
| `biblioteca_importacao.py` | Importação transacional e desfazer |
| `musicbrainz_local.py` | Cliente MusicBrainz opcional, com cache e limite |
| `servidor.py` e `web/` | Servidor local e interface |

O planejamento das próximas funcionalidades está em [ROADMAP.md](ROADMAP.md).
Só marque um item como concluído depois de cumprir o critério descrito lá.

## Regras de gênero e álbum

`generos_artistas.csv` e `albuns_excecoes.csv` são regras de curadoria local.
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
