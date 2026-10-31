# Acervo

[English](README.md) · **Português**

Gerenciador local de bibliotecas musicais. O Acervo lê a sua coleção de MP3 e
FLAC, sugere correções de metadados (gênero, nome de álbum e outros campos),
mostra tudo numa interface no navegador e só grava o que você aprovar, sempre
com backup completo e restauração exata.

A organização fica **dentro dos arquivos**, nas tags que qualquer player lê.
O Acervo não cria pastas por gênero ou álbum, não renomeia, não move e não
exclui músicas.

> **Estado do projeto:** em desenvolvimento ativo. Veja o [ROADMAP](ROADMAP.md).
>
> A interface está em português e em inglês (seletor no topo; por padrão segue
> o idioma do navegador).

## O que ele faz

- **Inventário:** lê MP3 (ID3v2.3/v2.4) e FLAC (Vorbis Comments) recursivamente,
  com tags, capas, características técnicas e hashes.
- **Sugestões de gênero:** a partir de regras locais editáveis por artista e por
  álbum (`genre_rules.csv`) e, opcionalmente, do MusicBrainz. Gêneros já
  preenchidos são preservados por padrão.
- **Unificação de álbuns:** regras conservadoras (`album_rules.csv`) que
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
- **Downloads:** escolher artista, álbum, edição e faixas e baixar com spotDL
  direto numa pasta, pelo navegador ou pelo terminal.
- **Busca:** na sua biblioteca ou no catálogo global, com sugestões enquanto
  você digita, filtros por tipo de gravação (estúdio, ao vivo, acústico…),
  capas e fotos de artistas.
- **Letras:** mostra a letra (sincronizada quando existir) e permite gravá-la
  no arquivo, com backup e desfazer.

## Instalação

Requisitos:

- Linux (testado em Arch/EndeavourOS e no fluxo do Ubuntu/Mint).
- Python 3.10 ou superior, com o módulo `venv`. No Ubuntu/Mint:
  `sudo apt install python3 python3-venv`.
- Opcional, para baixar músicas: `ffmpeg` (`sudo apt install ffmpeg`) e
  [spotDL](https://github.com/spotDL/spotify-downloader) (`pipx install spotdl`).

```bash
git clone https://github.com/Zelloe/acervo-musicas.git
cd acervo-musicas
./start.sh
```

Na primeira execução, `start.sh` e `download.sh` criam a `.venv` e instalam as
dependências sozinhos (precisa de internet). Não há dependências de JavaScript
nem etapa de compilação.

Sem `--pasta`, o Acervo usa a pasta de músicas do sistema, a mesma que o
gerenciador de arquivos mostra (por exemplo `~/Música` no Linux em português ou
`~/Music` em inglês). Se ela não existir, o programa avisa e pede `--pasta`.

## Baixar músicas

Requer [spotDL](https://github.com/spotDL/spotify-downloader) (`pipx install spotdl`)
e `ffmpeg`. Não precisa de conta de desenvolvedor do Spotify nem de Premium.
Há duas formas de usar, com o mesmo comportamento.

**No navegador:** abra a interface (`./start.sh`) e vá em **Baixar**. Escolha
a aba Artista, Música ou Link, confira a pasta de destino e marque as faixas. As
que faltam já vêm marcadas; as que você já tem aparecem desmarcadas. Quando o
destino fica dentro da coleção aberta, as músicas novas aparecem na Biblioteca
logo depois do download, sem precisar refazer a análise.

**No terminal:**

```bash
./download.sh --pasta "$HOME/Music"
```

O menu oferece três caminhos:

1. **Procurar artista:** mostra os álbuns com o ano; você escolhe o álbum, a
   edição (normal, deluxe…) quando houver mais de uma, e as faixas.
2. **Procurar música:** lista as versões encontradas com o álbum de cada uma.
3. **Colar link do Spotify:** de música, álbum ou playlist.

Na lista de faixas, as que você já tem na pasta aparecem como `[já tenho]`.
Responda com `enter` para baixar só as que faltam, `todas`, ou números como
`1-3,5`.

Cada faixa vira `Artista - Título.mp3` direto na pasta escolhida, com título,
artista, álbum, ano, número da faixa e capa vindos do spotDL. Como o spotDL sem
API oficial não informa gênero, o gênero é preenchido pelas regras de
`genre_rules.csv` quando houver uma regra única para o artista ou álbum.

O spotDL trabalha numa pasta temporária fora da coleção. O arquivo só aparece no
destino depois de o download terminar com sucesso e o áudio ser validado com
ffmpeg. Um arquivo que já exista com o mesmo nome nunca é substituído.

O spotDL usa os metadados do Spotify e busca o áudio no YouTube; `320k` é a
taxa de saída, não a qualidade da fonte. Baixe apenas o que você tem o direito
de baixar no seu país.

## Busca, filtros e letras

**Busca:** a barra no topo tem um interruptor **Sua biblioteca | Global**.
Na biblioteca, as sugestões aparecem enquanto você digita (artistas, álbuns e
músicas), sem depender de acentos, maiúsculas ou pontuação e tolerando pequenos
erros de digitação (`metalica`, `acdc`). O Enter lista as músicas por
relevância. No modo Global, as sugestões vêm do catálogo do spotDL e escolher
um artista abre a página **Baixar**; nada é baixado sem a sua escolha.

**Filtros de gravação:** Biblioteca e Baixar mostram um selo em cada música ou
álbum e filtros **Estúdio, Ao vivo, Acústico, Demo/Remix e Coletânea**. A
classificação olha só os qualificadores do título (`- Live`, `(Acoustic
Version)`) e o nome do álbum (`Holy Live`, `Live in São Paulo`), então
"Live and Learn" e "To Live Is to Die" continuam como estúdio. Um álbum gravado
ao vivo que não diz isso no nome aparece como estúdio.

**Letras:** o painel de letra do player usa, nesta ordem, a letra já gravada
no arquivo (ou um `.lrc` com o mesmo nome) e a do
[LRCLIB](https://lrclib.net), um banco aberto que não exige cadastro. Para
consultar o LRCLIB, o Acervo envia artista, título, álbum e duração. Letras
sincronizadas destacam a linha que está tocando. Depois de conferir (e, se
quiser, corrigir) o texto, **Salvar letra no arquivo** grava a letra simples
com backup completo; o Histórico desfaz a gravação. As letras consultadas ficam
em cache local, fora da coleção. Músicas baixadas pelo Acervo já chegam com a
letra simples embutida quando ela é encontrada.

## Uso pela interface

```bash
./start.sh --pasta "/caminho/para/suas/musicas"
```

O navegador abre em <http://127.0.0.1:8765>. Mantenha o terminal aberto
enquanto usa a interface. Ctrl+C encerra o servidor. Depois de atualizar o
Acervo, reinicie o servidor (a página avisa quando isso for necessário).

Sem abrir o navegador automaticamente, ou com outra porta:

```bash
.venv/bin/python acervo.py interface \
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
   .venv/bin/python acervo.py analisar \
     --pasta "/caminho/para/suas/musicas" \
     --normalizar-albuns \
     --saida relatorios/minha_previa
   ```

2. Abrir `relatorios/minha_previa/alteracoes.csv` e trocar a coluna `aprovar`
   para `sim` nas linhas desejadas. Não edite outras colunas: o plano é
   conferido antes da aplicação.

3. Aplicar:

   ```bash
   .venv/bin/python acervo.py aplicar \
     --plano relatorios/minha_previa/plano.json \
     --aprovacoes relatorios/minha_previa/alteracoes.csv
   ```

4. Desfazer, se necessário, com o manifesto informado na aplicação:

   ```bash
   .venv/bin/python acervo.py restaurar \
     --manifesto backups/ID_DA_EXECUCAO/manifesto.json
   ```

Opções da análise:

| Opção | Efeito |
| --- | --- |
| `--generos ARQUIVO` | Usar outro CSV de regras de gênero |
| `--albuns ARQUIVO` | Usar outro CSV de regras de álbum |
| `--musicbrainz --contato "e-mail ou URL"` | Consultar o MusicBrainz para faixas sem regra local |
| `--max-consultas N` | Limite de consultas ao MusicBrainz (padrão 200) |

### Campos protegidos

Um campo protegido nunca é sugerido nem gravado pelo Acervo, mesmo que esteja
vazio e mesmo que uma regra, o MusicBrainz ou um download proponham outro valor:

```bash
.venv/bin/python acervo.py proteger \
  --pasta "/caminho/para/suas/musicas" \
  --arquivo "/caminho/para/suas/musicas/Artista/faixa.mp3" \
  --campos genero,album
```

Campos aceitos: `titulo`, `artista`, `artista_album`, `album`, `genero`, `ano`,
`faixa` e `disco`. Use `desproteger` com os mesmos argumentos para liberar e
`protecoes --pasta ...` para listar. Na interface, proteja campos pelos
detalhes da faixa ou para um álbum inteiro.

A proteção é conferida de novo no momento de gravar. Um plano ou uma importação
preparados antes da proteção são recusados se tentarem mudar o campo. Uma
restauração que voltaria um campo protegido a outro valor também é recusada até
o campo ser desbloqueado, porque a restauração devolve o arquivo original
inteiro.

As proteções ficam em `<dados>/.biblioteca/protecoes/`, um arquivo por coleção,
identificadas pelo caminho da faixa relativo à pasta da coleção. Se uma faixa
for movida ou renomeada por outro programa, a proteção não a acompanha. Se esse
arquivo estiver corrompido ou pertencer a outra coleção, o Acervo recusa
qualquer gravação em vez de ignorar as proteções. Os comandos `analisar`,
`aplicar`, `restaurar` e a interface usam a mesma pasta de dados; o padrão é o
diretório do programa, e `--dados` escolhe outra.

### Playlists M3U8

Na interface, use **Exportar M3U8** na Biblioteca (exporta todas as faixas da
busca e do filtro atuais, inclusive as das outras páginas) ou no cartão de um
álbum. Antes de salvar, uma prévia mostra o destino e as primeiras faixas. A
playlist fica na pasta `Playlists/` da coleção, que é o único lugar onde a
interface cria um arquivo novo dentro da coleção; as músicas não são alteradas.

Para substituir uma playlist que já existe, é preciso marcar essa opção na
prévia. Antes da troca, a versão anterior é copiada e conferida em
`backups/playlists/`, fora da coleção, e o caminho dessa cópia aparece na
confirmação. Para voltar à versão anterior, copie esse arquivo de volta para
`Playlists/`.

Pela linha de comando, a exportação só lê a coleção:

```bash
.venv/bin/python acervo.py playlist \
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
.venv/bin/python acervo.py playlist --validar --saida Progressivo.m3u8
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
Um gênero só é sugerido quando o próprio álbum é encontrado, para que uma banda
com o mesmo nome não empreste o gênero dela. Casos sem correspondência ficam
pendentes.

Nomes, tags e duração são evidências parciais. O Acervo ainda não faz
identificação pelo áudio (impressão digital acústica).

## Downloads avançados

O menu **Baixar** usa o fluxo direto descrito em [Baixar músicas](#baixar-músicas).
Para quem prefere revisar antes de qualquer arquivo entrar na coleção, há um
fluxo avançado em **Preferências → Downloads avançados**, que separa o download
da entrada na coleção:

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

Veja [CONTRIBUTING.pt-BR.md](CONTRIBUTING.pt-BR.md) e o [ROADMAP](ROADMAP.md).

## Licença

[MIT](LICENSE).
