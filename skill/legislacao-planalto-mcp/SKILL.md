---
name: legislacao-planalto-mcp
description: 'Legislação federal brasileira atualizada pelo MCP planalto (texto compilado do Planalto + alterações registradas pelo Senado): redação vigente de artigo, parágrafo, inciso ou alínea; vigência futura e revogações com data; quem alterou cada dispositivo e quando; se a norma está em vigor; se o compilado está atualizado; novidades legislativas; acervo de um tema com planilha. Use SEMPRE que o usuário citar ou perguntar sobre lei, LC, decreto, decreto-lei, MP, emenda ou a Constituição/ADCT ("art. 74 da Lei 9.430", "LC 214", "RIR", "CTN"), pedir a redação atual ou original, perguntar se algo mudou, foi revogado ou desde quando vale, qual alíquota, prazo ou percentual a lei fixa, o que saiu de legislação sobre um tema, quiser levantar a legislação de IRPJ, CSLL, PIS/Cofins, IRRF, IBS/CBS ou outro tributo federal, ou conferir citações de lei num parecer. Não use para atos da RFB, CARF, tribunais nem normas estaduais ou municipais.'
---

# Legislação federal pelo MCP `planalto`

As descrições das ferramentas `planalto` já explicam o que cada uma faz e os alertas que trazem. Esta skill
acrescenta só três coisas: **usar o servidor em vez da memória**, **um checklist antes de responder** e
**fluxos de várias etapas** (correção, conferência de parecer, levantamento de tema).

Se as ferramentas `planalto` não estiverem na sessão, diga isso no início e avise que a resposta virá da web
ou da memória, sem a checagem de vigência.

## 1. Texto de lei vem do servidor, não da memória

A memória do modelo e a busca na web erram exatamente onde mais importa: redação alterada há poucos meses,
regra com vigência futura que o Planalto já mostra como vigente, revogação com data marcada (ex.: LC 214/2025,
art. 542, que revoga a legislação do PIS/Cofins em 1º/1/2027), artigo revogado que o compilado ainda não
riscou. Por isso, mesmo para lei "conhecida" (Lei 9.430, Lei 9.249, CTN), leia o dispositivo com `ler_norma`
antes de afirmar o texto, a alíquota, o prazo ou a vigência. Responder só de memória é o principal erro que
esta skill existe para evitar.

## 2. Checklist antes de responder

Percorra estes pontos; cada um corresponde a um erro real observado em teste:

1. **Li o dispositivo pedido** (`ler_norma(norma, dispositivo=...)`), não a lei inteira, e o `Contexto`
   (caput/parágrafo a que o inciso se liga)?
2. **Há ALERTA ou AVISO no cabeçalho?** Se houver, a resposta começa por ele e separa "o que vale hoje" de
   "o que valerá a partir de [data]".
3. **A redação ou a norma é recente (últimos ~2 anos)?** Então leia também a **lei que a trouxe**:
   - a **cláusula de vigência** dela (`ler_norma(lei_nova, termo="vigor")` ou o último artigo) — "entra em
     vigor na data da publicação" não basta se houver "produz efeitos a partir de";
   - as **regras de transição** (escalonamentos por ano, "até 31 de dezembro de...", "a partir do exercício")
     — `ler_norma(lei_nova, termo="a partir de")` ajuda a achar.
   Foi aqui que as respostas do teste perderam pontos: dar a data de vigência sem o escalonamento, ou a
   alteração sem a data de efeitos.
4. **A conclusão depende de o texto estar atualizado?** (parecer, cálculo, prazo, alíquota) Rode
   `verificar_atualizacao` da norma e trate ATENÇÃO / A CONFERIR / efeitos futuros antes de concluir.
5. **É MP?** `consultar_norma`: convertida (em qual lei), sem eficácia ou vigência encerrada, com a **data**
   e o ato (ato declaratório do Congresso). MP anterior à EC 32/2001 "em tramitação" vale pelo art. 2º da
   emenda.
6. **A pergunta é sobre alíquota, presunção, base ou benefício?** Uma lei posterior pode mudar a aplicação
   sem alterar o texto do dispositivo (ex.: LC 224/2025 sobre os percentuais do art. 15 da Lei 9.249).
   Procure com `novidades_legislativas`/`buscar_normas` pelo tema e diga ao usuário que procurou.

## 3. Quando o usuário contesta ou pede para conferir

"Confere isso", "tem certeza?", "acho que mudou": trate como uma **nova verificação na fonte**, não como
revisão do próprio texto. Refazer a resposta de memória só troca um erro por outro.

1. Releia o dispositivo com `ler_norma` (e `modo="historico"` se a dúvida for "desde quando").
2. Rode `historico_alteracoes` do dispositivo e `verificar_atualizacao` da norma.
3. Abra as leis que aparecerem (cláusula de vigência e transição, item 3 do checklist).
4. Diga o que mudou na resposta e **por quê** (qual trecho da fonte), citando literalmente o trecho decisivo
   e o link do Planalto. Se a primeira resposta estava certa, diga isso e mostre a evidência.
5. Se a dúvida estiver fora do servidor (IN, solução de consulta, jurisprudência, norma estadual), diga e
   use a skill própria.

## 4. Fluxos de várias etapas

- **Conferir as citações de um parecer**: para cada citação, `ler_norma` do dispositivo → o texto diz o que
  se afirma? → há ALERTA? → a nota de origem bate com a data alegada? Monte uma tabela citação × resultado.
  Use junto a skill `memo-tributario-verificadora` para a disciplina de auditoria.
- **Levantar a legislação de um tema**: `listar_temas` → `mapear_tema("IRPJ")` (combina: "IRPJ/CSLL").
  Tema livre: informe `normas_extras` com as leis-base (ex.: JCP → `["Lei 9.249/1995, art. 9"]`), senão o
  resultado depende só da ementa. Explique as camadas e entregue a planilha. Escopos dos temas:
  `references/temas.md`.
- **O que saiu de novo**: `novidades_legislativas(dias=30, tema=...)`; abra o texto de cada norma relevante
  antes de resumir; se a resposta disser que não revalidou os quadros, avise.

## 5. Como responder

- Norma + dispositivo + nota de origem ("na redação dada pela Lei X/AAAA"; sem nota, "redação original") e o
  link do Planalto.
- Separe texto (da fonte) de interpretação (sua).
- Diga o que não foi possível conferir (Senado fora, cópia local, alerta "não verificado").
- Em conclusão sensível: "texto compilado do Planalto em [data]; o oficial é o publicado no DOU".

Significado de cada campo das respostas e armadilhas conhecidas das fontes:
`references/campos-e-armadilhas.md`.

## Fora do alcance

Atos da RFB → `normas-rfb-mcp`; CARF/CSRF → `carf-jurisprudencia-mcp`; STF, STJ, TRFs →
`tribunais-federais`; tributos estaduais e municipais → `tribunais-estaduais`.
