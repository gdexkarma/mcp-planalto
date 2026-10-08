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

---

# 2ª rodada: skill v2, uso espontâneo e correção (2026-10-08)

Mesmas 16 perguntas e mesmo gabarito; avaliação cega por um novo avaliador (as notas entre rodadas não são
diretamente comparáveis: o avaliador da 2ª rodada foi um pouco menos rigoroso em omissões).

## Uso espontâneo (MCP e web disponíveis, sem instrução de qual usar)

| | Corretas | Parciais | Erros graves | Perguntas em que usou o MCP | Uso da web | Tokens por pergunta | Tempo total |
|---|---|---|---|---|---|---|---|
| Skill v2 disponível | 16/16 | 0 | 0 | 16/16 (79 chamadas) | 0 | ~15,2 mil | 8,1 min |
| Sem skill | 15/16 | 1 | 0 | 16/16 (68 chamadas) | 0 | ~12,5 mil | 6,9 min |

Com o servidor disponível, o modelo o escolheu sozinho em todas as perguntas, com ou sem a skill. A skill v2
(checklist de lei nova, protocolo de correção) ganhou 1 pergunta (MP 1.303: data de encerramento da vigência),
custando ~22% mais tokens e ~17% mais tempo.

## Correção ("Confere isso com a fonte e corrija o que estiver errado ou faltando")

As 12 respostas não corretas da 1ª rodada (3 do braço A, 3 do B, 6 do C):

| | Corrigidas | Continuaram parciais | Erros introduzidos | Tempo total | Chamadas |
|---|---|---|---|---|---|
| Com MCP (+ skill + web) | 11/12 | 1 | 0 | 12,2 min | 81 |
| Só web | 11/12 | 1 | 0 | 19,2 min | 109 |

O item que ficou parcial nos dois braços é o mesmo (pergunta 15: citar também o art. 3º da Lei 14.754).

## Leitura

- Pedir "confere com a fonte" funciona: 11 de 12 respostas incompletas ficaram corretas, sem nenhum erro novo.
  Com o MCP, a correção levou 37% menos tempo e 26% menos chamadas que só com a web; na precisão final, empate.
- A skill quase não muda a precisão quando o servidor já está disponível: o ganho medido foi de 1 pergunta em
  16. Seu papel é guiar os fluxos de várias etapas (conferir pareceres, levantar temas, corrigir), que este
  teste de perguntas isoladas não mede.
