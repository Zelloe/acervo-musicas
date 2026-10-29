# Acervo — biblioteca musical local

Interface HTML inspirada no Spotify, com tema escuro, busca, capas reais,
reprodutor, prévia de metadados, aprovação por alteração e histórico reversível.
O programa usa Python e Mutagen e funciona no próprio computador.

## Abrir

```bash
cd ~/Projects/Music
./iniciar.sh
```

O atalho abre o navegador em **http://127.0.0.1:8765**. Mantenha o terminal do
programa aberto enquanto usa a interface. Para encerrar, pressione Ctrl+C.
A pasta padrão é `~/Music`, onde a coleção foi encontrada neste computador.
Para escolher outra pasta:

```bash
./iniciar.sh --pasta "$HOME/musica"
```

Também é possível iniciar sem abrir o navegador automaticamente:

```bash
python corrigir_tags.py interface --pasta "$HOME/Music" --porta 8765
```

Se as dependências ainda não estiverem instaladas:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Não há dependências de JavaScript nem etapa de compilação da interface.
O servidor aceita conexões exclusivamente em 127.0.0.1 e valida Host, origem
e um token aleatório nas requisições que iniciam operações.

## Usar

1. **Visão geral:** contagens reais e álbuns da coleção.
2. **Biblioteca:** busca, filtros, reprodução e consulta dos metadados atuais.
3. **Revisar alterações:** compare atual e sugerido; selecione apenas o que
   deseja aprovar. A confirmação mostra cada alteração e o tamanho do backup.
4. **Sem sugestão:** músicas cujo gênero exige mais identificação.
5. **Álbuns:** nomes e edições existentes, incluindo filtro de nomes semelhantes.
6. **Histórico:** execuções, backups e restauração.

Abrir a interface, reproduzir músicas e gerar análises não modifica as músicas.
A primeira análise lê todos os arquivos e calcula hashes; pode levar alguns
minutos. As análises seguintes também verificam integralmente os arquivos.
Relatórios anteriores são carregados ao iniciar; use a atualização para incluir
mudanças feitas por outros programas. Uma aplicação sempre confere os hashes
atuais e recusa arquivos que mudaram desde a prévia.

## O que esta versão propõe

- Preencher gêneros ausentes usando `generos_artistas.csv`, com prioridade para
  regras específicas de álbum. Gêneros existentes são preservados.
- Padronizar o álbum *…And Justice for All* por regras em `albuns_excecoes.csv`.
  Cada proposta exige artista, edição, número de disco, faixa e título
  compatíveis. O nome anterior é armazenado em `TXXX:BibliotecaMusical:AlbumAnterior`
  nos MP3 e `BIBLIOTECA_ALBUM_ANTERIOR` nos FLAC.
- Preservar títulos, artistas, anos, números, capas, outras tags e o fluxo de áudio.

As seis regras de gênero fornecidas no resumo do usuário estão identificadas
como tal. As demais são **propostas de curadoria local**, sujeitas a revisão;
não são apresentadas como identificações confirmadas ou dados obtidos online.
Créditos de colaboração recebem regras explícitas. O programa não divide nomes
por barra ou vírgula; por exemplo, AC/DC continua sendo um único artista.

A análise inicial completa encontrou 1.092 músicas: 1.082 MP3 e 10 FLAC, todas
com capa e sem gênero. As regras oferecem 1.079 sugestões de gênero e deixam
13 para identificação. A prévia atual também inclui as nove faixas de
*…And Justice for All*. **Nenhuma proposta foi aplicada à coleção original.**

O preenchimento automático de artista do álbum, ano e disco ainda não faz parte
desta versão. Seus valores atuais e ausências aparecem nos relatórios. Não há
exclusão de duplicatas, renomeação, movimentação, conversão de áudio ou download.

## MusicBrainz opcional

Nas preferências, ative a consulta para gêneros sem regra local e informe um
contato real (e-mail ou URL) para identificar o cliente. A próxima análise
consulta nomes de artista e álbum. Não envia áudio. Artistas homônimos ou sem
correspondência suficiente permanecem pendentes.

O cliente usa cache de 30 dias, requisições sequenciais com intervalo de pelo
menos 1,1 segundo, tentativas limitadas e um teto de consultas. Uma falha de
conexão suspende as demais consultas daquela análise. Gêneros do Release Group
são preferidos aos do artista, quando disponíveis. Toda sugestão exige aprovação.

Referências: [API MusicBrainz](https://musicbrainz.org/doc/MusicBrainz_API),
[limites de uso](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting),
[Release Group de …And Justice for All](https://musicbrainz.org/release-group/67553e23-8dad-3792-b6f2-8fedd5650ff3).
Os metadados locais e as regras não confirmam, por si só, a gravação do áudio.
Não há identificação por impressão digital nesta versão.

## Backup e restauração

Antes da primeira alteração de uma execução, o programa copia integralmente
**todos os arquivos aprovados** para `backups/<execucao>/originais/` e verifica
seus hashes. Portanto, os backups incluem áudio, capas e tags originais e podem
ocupar o tamanho dos arquivos selecionados.

Cada arquivo é editado em uma cópia temporária. Antes de substituir o arquivo
no mesmo caminho, o programa verifica:

- Hash idêntico do fluxo de áudio comprimido.
- Capas, outras tags, frames desconhecidos e blocos FLAC preservados.
- Mesma versão ID3v2.3 ou ID3v2.4; tags ID3v1 existentes preservadas.
- Correspondência das tags gravadas com os valores aprovados.

Arquivos incompatíveis, com links simbólicos ou hard links exigem revisão.
Os metadados podem ser serializados novamente e o padding pode mudar. Não há
recodificação do áudio. A restauração usa o arquivo original completo e é exata,
byte por byte. O inventário isolado não substitui esse backup.

O manifesto é atualizado antes e depois de cada substituição. Uma interrupção
pode deixar parte do lote aplicada; o manifesto e os backups permitem restaurar
essa situação. A restauração verifica todo o lote antes de começar e recusa
arquivos editados posteriormente por outro programa. Os backups são mantidos.

## Linha de comando

Gerar uma nova prévia, sem modificar músicas:

```bash
python corrigir_tags.py analisar \
  --pasta "$HOME/Music" \
  --normalizar-albuns \
  --saida relatorios/minha_previa
```

O diretório de saída deve ser novo e externo à biblioteca. Em `alteracoes.csv`,
edite apenas a coluna `aprovar`, colocando `sim` nas linhas desejadas. O plano
JSON e os valores do CSV são conferidos antes da aplicação.

```bash
python corrigir_tags.py aplicar \
  --plano relatorios/minha_previa/plano.json \
  --aprovacoes relatorios/minha_previa/alteracoes.csv
```

Restaurar pelo manifesto informado na aplicação:

```bash
python corrigir_tags.py restaurar \
  --manifesto backups/ID_DA_EXECUCAO/manifesto.json
```

Para consultar MusicBrainz pela linha de comando, acrescente à análise
`--musicbrainz --contato "seu-contato-real"`. O parâmetro `--max-consultas`
controla o teto. `--generos` e `--albuns` aceitam arquivos de regras alternativos.
Na interface, `--dados /outra/pasta` permite escolher um diretório externo para
relatórios e backups.

## Arquivos e verificações

- `corrigir_tags.py`: comandos de análise, aplicação, restauração e interface.
- `biblioteca_core.py`: gravação validada, backups e transações.
- `biblioteca_plano.py`: propostas, planos e planilhas de aprovação.
- `musicbrainz_local.py`: cliente opcional e conservador.
- `servidor.py` e `web/`: servidor e interface local.
- `diagnostico_biblioteca.py`: inventário independente, somente de leitura.
- `relatorios/previa_atual/`: prévia da coleção real, sem aprovações.
- `relatorios/validacao_gravacao_restauracao.json`: resultado em cópias de 12
  arquivos reais, incluindo MP3 ID3v2.3, ID3v2.4 e FLAC; 12 gêneros e nove álbuns
  alterados, com restauração integral confirmada.

Os testes automatizados geram pequenos arquivos temporários com ffmpeg:

```bash
python -m unittest discover -s tests -v
```

As consultas externas podem falhar ou retornar dados incompletos. A integração
MusicBrainz tem testes de respostas simuladas; a análise inicial usou somente
regras locais. A interface foi verificada em Chromium, incluindo busca,
reprodução real, detalhes das tags, seleção, confirmação e layout de celular.
