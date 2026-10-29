# Acervo

Gerenciador local de bibliotecas musicais. O Acervo lê a sua coleção de MP3 e
FLAC, sugere correções de metadados (gênero, nome de álbum e outros campos),
mostra tudo numa interface no navegador e só grava o que você aprovar, sempre
com backup completo e restauração exata.

A organização fica **dentro dos arquivos**, nas tags que qualquer player lê.
O Acervo não cria pastas por gênero ou álbum, não renomeia, não move e não
exclui músicas.

> **Estado do projeto:** em desenvolvimento. O editor de metadados com backup e
> restauração é a parte mais madura. Downloads e importação ainda estão sendo
> testados. Veja o [ROADMAP](ROADMAP.md).

## O que ele faz

- **Inventário:** lê MP3 (ID3v2.3/v2.4) e FLAC (Vorbis Comments) recursivamente,
  com tags, capas, características técnicas e hashes.
- **Sugestões de gênero:** a partir de regras locais editáveis por artista e por
  álbum (`generos_artistas.csv`) e, opcionalmente, do MusicBrainz. Gêneros já
  preenchidos são preservados por padrão.
- **Unificação de álbuns:** regras conservadoras (`albuns_excecoes.csv`) que
  exigem artista, edição, disco, faixa e título compatíveis. O nome anterior
  fica guardado numa tag própria. Versões ao vivo, demos e bônus não são fundidas
  só por semelhança de nome.
- **Aprovação por alteração:** cada mudança aparece com o valor atual e o
  sugerido, e só as linhas aprovadas são gravadas.
- **Backup e restauração:** antes de gravar, o arquivo original inteiro é
  copiado e verificado. A restauração devolve o original byte por byte e recusa
  sobrescrever edições feitas depois por outro programa.
- **Interface local:** biblioteca com busca e filtros, reprodução, revisão de
  propostas, álbuns, histórico e restauração. Roda só em `127.0.0.1`.
- **Downloads (em teste):** preparar faixas faltantes de um álbum com spotDL ou
  yt-dlp, numa área separada da coleção, para revisar antes de importar.

## Instalação

Requisitos:

- Python 3.11 ou superior.
- [Mutagen](https://mutagen.readthedocs.io/) (instalado pelo `requirements.txt`).
- Opcional, para downloads: `ffmpeg` e [spotDL](https://github.com/spotDL/spotify-downloader)
  ou [yt-dlp](https://github.com/yt-dlp/yt-dlp).

```bash
git clone https://github.com/Zelloe/acervo-musicas.git
cd acervo-musicas
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Não há dependências de JavaScript nem etapa de compilação.

## Uso pela interface

```bash
./iniciar.sh --pasta "/caminho/para/suas/musicas"
```

O navegador abre em <http://127.0.0.1:8765>. Mantenha o terminal aberto
enquanto usa a interface. Ctrl+C encerra o servidor.

Sem abrir o navegador automaticamente, ou com outra porta:

```bash
.venv/bin/python corrigir_tags.py interface \
  --pasta "/caminho/para/suas/musicas" \
  --porta 8765 \
  --dados "/caminho/para/dados-do-acervo"
```

`--dados` define onde ficam relatórios, backups e downloads em revisão. Deve ser
uma pasta fora da coleção. O padrão é o diretório do programa.

Abrir a interface, ouvir músicas e gerar análises **não modifica** a coleção.
A primeira análise lê todos os arquivos e calcula hashes, e pode levar alguns
minutos em coleções grandes.

## Uso pela linha de comando

1. Gerar uma prévia (não grava nada):

   ```bash
   .venv/bin/python corrigir_tags.py analisar \
     --pasta "/caminho/para/suas/musicas" \
     --normalizar-albuns \
     --saida relatorios/minha_previa
   ```

2. Abrir `relatorios/minha_previa/alteracoes.csv` e trocar a coluna `aprovar`
   para `sim` nas linhas desejadas. Não edite outras colunas: o plano é
   conferido antes da aplicação.

3. Aplicar:

   ```bash
   .venv/bin/python corrigir_tags.py aplicar \
     --plano relatorios/minha_previa/plano.json \
     --aprovacoes relatorios/minha_previa/alteracoes.csv
   ```

4. Desfazer, se necessário, com o manifesto informado na aplicação:

   ```bash
   .venv/bin/python corrigir_tags.py restaurar \
     --manifesto backups/ID_DA_EXECUCAO/manifesto.json
   ```

Opções da análise:

| Opção | Efeito |
| --- | --- |
| `--generos ARQUIVO` | Usar outro CSV de regras de gênero |
| `--albuns ARQUIVO` | Usar outro CSV de regras de álbum |
| `--musicbrainz --contato "e-mail ou URL"` | Consultar o MusicBrainz para faixas sem regra local |
| `--max-consultas N` | Limite de consultas ao MusicBrainz (padrão 200) |

### Playlists M3U8

Exportar uma seleção como playlist portátil (só lê a coleção):

```bash
.venv/bin/python corrigir_tags.py playlist \
  --pasta "/caminho/para/suas/musicas" \
  --genero "Progressive Rock" \
  --nome "Progressivo" \
  --saida "/caminho/para/suas/musicas/Playlists/Progressivo.m3u8"
```

Os filtros `--genero`, `--artista` e `--album` comparam o valor inteiro, sem
diferenciar maiúsculas. Sem filtros, a playlist inclui a coleção toda, ordenada
por artista do álbum, álbum, disco e faixa.

O arquivo é UTF-8, sem BOM, e os caminhos são relativos à pasta da playlist.
Se a playlist ficar dentro da pasta da coleção e as duas forem copiadas juntas
para outro disco, os caminhos continuam válidos. Uma playlist existente não é
substituída sem `--sobrescrever`. Para conferir se todos os caminhos ainda
resolvem:

```bash
.venv/bin/python corrigir_tags.py playlist --validar --saida Progressivo.m3u8
```

## Como as gravações são protegidas

Cada arquivo é editado numa cópia temporária. Antes de substituir o original no
mesmo caminho, o Acervo confere:

- que o fluxo de áudio comprimido é idêntico (sem recodificação);
- que capas, tags não aprovadas, frames desconhecidos e blocos FLAC continuam lá;
- que a versão do ID3v2 e o ID3v1 existente foram mantidos;
- que as tags gravadas são exatamente as aprovadas.

Uma aplicação recusa arquivos que mudaram desde a prévia. Arquivos com links
simbólicos ou hard links ficam para revisão manual. Uma execução interrompida
pode ser restaurada pelo manifesto.

O espaço dos backups é igual ao tamanho dos arquivos alterados. Os backups não
são apagados automaticamente.

## MusicBrainz

A consulta é opcional e desligada por padrão. Envia apenas nomes de artista e
álbum, nunca áudio. Exige um contato real para identificar o cliente, como pede
a [política de uso](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting),
respeita o intervalo mínimo entre requisições e guarda cache por 30 dias.
Artistas homônimos ou sem correspondência suficiente ficam pendentes.

Nomes, tags e duração são evidências parciais. O Acervo ainda não faz
identificação pelo áudio (impressão digital acústica).

## Downloads

O fluxo de downloads separa o download da entrada na coleção:

1. Informar um link ou busca e ver o catálogo da fonte.
2. Comparar com os arquivos locais e escolher as faixas.
3. Baixar numa área de revisão, fora da coleção.
4. Ouvir, revisar tags e escolher, por faixa: manter a coleção, aplicar só as
   tags, adicionar como nova versão ou substituir um MP3 local (com backup).

O Acervo recusa substituir FLAC por MP3. A opção de bitrate define a taxa de
saída e **não prova** a qualidade da fonte. O spotDL usa metadados do Spotify e
busca o áudio em outras fontes. Não é preciso ter Spotify Premium.

Baixe apenas conteúdo que você tem o direito de baixar no seu país.

## Testes

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Os testes geram pequenos arquivos temporários com `ffmpeg` e nunca tocam numa
coleção real.

## Contribuir

Veja [CONTRIBUTING.md](CONTRIBUTING.md) e o [ROADMAP](ROADMAP.md).

## Licença

[MIT](LICENSE).
