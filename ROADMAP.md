# Plano de evolução do Acervo

As 15 funcionalidades abaixo fazem parte do escopo planejado do projeto.
As caixas indicam a entrega completa e validada de cada item; recursos que já
tenham suporte parcial devem ser aproveitados e ampliados.

**Prioridade atual (29/10/2026):** após concluir os itens 6 e 13, pausar novos
itens deste roadmap. O usuário priorizou um seletor simples de músicas com
spotDL, no terminal e no navegador, salvando diretamente na pasta escolhida.
A organização de metadados existentes continua disponível.

## Regras para todas as etapas

- Manter a biblioteca nos caminhos atuais, sem criar pastas por gênero ou álbum.
- Armazenar informações musicais nos metadados dos arquivos, com compatibilidade
  com players. Filtros, preferências e tarefas podem usar o banco local do app.
- Apresentar uma prévia e exigir aprovação antes de modificar a coleção.
- Fazer backup verificável e permitir restauração das alterações.
- Preservar capas, tags válidas, faixas distintas e versões legítimas.
- Não excluir, renomear ou reconverter arquivos automaticamente. Downloads e
  substituições devem ser escolhidos explicitamente.
- Encaminhar identificações ambíguas para revisão e registrar suas fontes.
- Manter caminhos configuráveis e permitir o uso sem Spotify Premium.

## Funcionalidades

- [ ] **1. Álbuns incompletos**
  Comparar a coleção com a lista de faixas da edição escolhida, mostrando
  presentes, ausentes e correspondências duvidosas. Permitir selecionar e baixar
  somente as faixas faltantes, sem substituir as existentes por padrão.

- [ ] **2. Comparador de edições**
  Comparar original, remaster, deluxe e versões ao vivo por discos, faixas,
  duração e identificadores disponíveis. Mostrar faixas extras e diferenças
  antes da unificação, preservando a identificação da edição e da gravação.
  Compartilhar um Release Group não basta para tratar gravações como iguais.

- [ ] **3. Identificação pelo áudio**
  Usar impressão digital acústica como evidência adicional para músicas com
  nomes ou tags incorretos. Exibir candidatos, fonte e confiança; resultados
  ambíguos exigem revisão. Informar quando uma consulta enviar a impressão
  digital a um serviço externo.

- [ ] **4. Possíveis duplicatas**
  Distinguir arquivos idênticos por hash, possíveis codificações diferentes da
  mesma gravação e versões alternativas. Oferecer comparação e reprodução,
  sem apagar automaticamente nem concluir duplicidade somente por título ou álbum.

- [ ] **5. Origem de cada informação**
  Registrar por campo o valor atual, a sugestão, a fonte, a data da consulta e
  as evidências da identificação. Diferenciar regras locais, escolhas manuais
  e dados externos; explicar a confiança sem inventar precisão numérica.

- [x] **6. Campos protegidos**
  Permitir proteger campos de uma faixa ou de um conjunto de faixas, como
  gênero e edição do álbum. Persistir a escolha e respeitá-la em análises,
  enriquecimentos e substituições, até o usuário desbloquear o campo.
  Disponível na CLI, nos detalhes da faixa e em lote para as faixas atuais de
  um álbum. A política local é separada por biblioteca e revalidada ao gravar
  ou restaurar, inclusive para planos antigos. Campos vazios também podem ser
  protegidos. Não há reassociação automática quando um arquivo muda de caminho.
  Testes Python/HTTP e Chromium em desktop/celular validam persistência,
  desbloqueio e leitura da biblioteca mesmo com a política ilegível.

- [ ] **7. Gêneros personalizados**
  Oferecer edição de regras por artista, álbum e faixa, com múltiplos gêneros.
  Documentar a prioridade entre regras, respeitar campos protegidos e validar
  a leitura de TCON e GENRE nos players considerados compatíveis.

- [ ] **8. Coleções inteligentes**
  Salvar filtros combinados por gênero, período, artista, formato e pendências,
  incluindo álbuns incompletos e músicas sem capa. Atualizar os resultados
  conforme a biblioteca muda, sem mover arquivos.

- [ ] **9. Fila de downloads persistente**
  Salvar tarefas e seus estados para recuperar a fila após fechar o programa.
  Permitir pausar, cancelar e tentar novamente, com erros claros. Quando a
  ferramenta não permitir retomar bytes, indicar que a tentativa será reiniciada.
  Manter os downloads fora da coleção até a aprovação da importação.

- [ ] **10. Comparação antes da substituição**
  Permitir ouvir o arquivo atual e o candidato, comparando duração, formato,
  bitrate, frequência de amostragem, canais, tags e capa. Não apresentar bitrate
  maior como prova de qualidade superior. Exigir escolha explícita e backup
  para substituir um arquivo, preservando os originais até a aprovação.

- [ ] **11. Histórico visual**
  Mostrar alterações por execução, faixa e campo, com valores anteriores e
  posteriores. Permitir desfazer uma faixa ou um lote após conferir integridade
  e possíveis edições posteriores; registrar também importações e substituições.

- [ ] **12. Modo de descoberta**
  Explorar artistas pouco ouvidos, álbuns esquecidos e gêneros de interesse.
  Explicar o motivo de cada sugestão. Usar histórico de reprodução somente
  quando disponível e habilitado, sem confundir falta de dados com pouca escuta.

- [x] **13. Playlists portáteis**
  Exportar seleções e coleções em M3U8 com caminhos relativos à playlist,
  codificação UTF-8 e nomes com acentos. Validar a resolução dos caminhos
  depois de transportar a playlist junto com a coleção correspondente.
  Disponível pela CLI e pelo botão **Exportar M3U8** da Biblioteca/Álbuns.
  A interface mostra prévia, salva em `Playlists/` e exige confirmação para
  substituir uma playlist, guardando antes uma cópia verificável fora da coleção.
  Validado com acentos, múltiplas páginas, álbuns homônimos, cópia para outro
  disco e navegação em desktop/celular. A restauração da playlist anterior é
  manual, pelo caminho do backup exibido após a substituição.

- [ ] **14. Sistema de extensões**
  Definir interfaces documentadas para fontes de metadados, ferramentas de
  download e regras. Permitir extensões opcionais sem modificar o núcleo,
  mantendo aprovação, campos protegidos, validação e backup no núcleo.
  Informar que extensões executam código e devem vir de fontes confiáveis.

- [ ] **15. Modo demonstração**
  Disponibilizar uma biblioteca fictícia com áudio próprio ou de licença
  compatível, créditos e casos de metadados incompletos, edições e duplicatas.
  Permitir testar a interface e contribuir sem acessar a coleção pessoal,
  com dados e backups de demonstração isolados e opção de reiniciar a demonstração.

## Ordem proposta de entrega

| Etapa | Itens | Resultado esperado |
| --- | --- | --- |
| 1 — Decisões e rastreabilidade | 5, 6, 7 e 11 | Entender as sugestões, preservar escolhas e revisar ou desfazer alterações. |
| 2 — Identificação e álbuns | 3, 4, 2 e 1 | Comparar gravações e edições para reconhecer o que existe e o que falta. |
| 3 — Downloads e portabilidade | 9, 10 e 13 | Recuperar tarefas, revisar substituições e exportar playlists. |
| 4 — Exploração e contribuições | 8, 12, 14 e 15 | Explorar a coleção e facilitar extensões e demonstrações públicas. |

A ordem orienta prioridades, sem estabelecer datas. Funcionalidades
independentes podem ser antecipadas. A comparação de edições deve preceder a
classificação definitiva de faixas faltantes; a revisão e o backup devem estar
disponíveis antes de qualquer substituição.

## Critério de conclusão

Marcar um item somente depois de verificar seu comportamento, documentar o uso
e as limitações e validar os casos relevantes em uma biblioteca de teste.
Fluxos que gravem arquivos devem demonstrar preservação dos dados não aprovados
para alteração e restauração dos originais. O modo demonstração não deve incluir
arquivos, credenciais ou relatórios pessoais.
