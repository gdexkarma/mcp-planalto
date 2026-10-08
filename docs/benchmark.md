# Teste de precisão: MCP planalto × pesquisa na web (2026-10-08)

**Desenho.** 16 perguntas de advogado tributarista sobre legislação federal (redação vigente, vigência futura,
revogação programada, alteração recente, situação de MP, histórico de alterações, regra autônoma). Três braços,
mesmo modelo, uma rodada cada:

- **A — MCP + skill** `legislacao-planalto-mcp` (sem web);
- **B — MCP sem skill**, só com as descrições das ferramentas (sem web);
- **C — só web** (WebSearch + WebFetch, inclusive páginas do Planalto e do Congresso).

Gabarito montado por agente independente só com fonte primária crua (HTML do Planalto com o riscado marcado,
cláusulas de vigência das leis alteradoras, API do Senado), sem usar as saídas do servidor; 16/16 com confiança
alta. Avaliação cega (respostas embaralhadas, sem o braço) contra os fatos essenciais e os erros graves do gabarito.

## Resultado

| | Corretas | Parciais | Erradas | Erros graves | Fatos essenciais | Fundamentação correta | "Armadilhas"* corretas |
|---|---|---|---|---|---|---|---|
| A — MCP + skill | 13/16 | 3 | 0 | 0 | 95,6% | 16/16 | 5/6 |
| B — MCP sem skill | 13/16 | 3 | 0 | 0 | 94,7% | 16/16 | 5/6 |
| C — só web | 10/16 | 5 | 1 | 1 | 93,9% | 15/16 | 3/6 |

\* Perguntas em que o compilado do Planalto, lido sem cuidado, engana (vigência futura já exibida como vigente,
revogação com data marcada, revogação ainda não incorporada): 2, 3, 4, 10, 12, 14.

| | Tempo por pergunta (mediana) | Chamadas por pergunta | Tokens por pergunta |
|---|---|---|---|
| A — MCP + skill | 23 s | 3,2 | ~14,9 mil |
| B — MCP sem skill | 17 s | 3,1 | ~12,0 mil |
| C — só web | 50 s | 4,1 | ~14,3 mil |

## Leitura

- MCP: 13 corretas e nenhum erro grave contra 10 corretas e 1 erro grave da web; 2,2 a 2,9 vezes mais rápido.
- As parciais dos braços MCP foram omissões de detalhe (data de encerramento da MP, escalonamento de prazos da
  lei nova, data de efeitos), não texto legal errado.
- A web acertou mais do que se esperaria porque o agente leu as próprias páginas do Planalto e do Congresso,
  gastando o dobro do tempo; errou ou ficou incompleta justamente nas armadilhas de vigência.
- A skill não mudou a precisão nesta amostra (13 × 13); custou ~25% mais tempo e tokens.
- Amostra pequena (16 perguntas, uma rodada por braço): diferenças de 2 a 3 perguntas são indicativas, não
  estatisticamente conclusivas.
