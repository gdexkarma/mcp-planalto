---
name: legislacao-planalto-mcp
description: 'Legislação federal brasileira atualizada pelo MCP planalto (texto compilado do Planalto + alterações registradas pelo Senado): redação vigente de artigo, parágrafo, inciso ou alínea; vigência futura e revogações com data; quem alterou cada dispositivo e quando; se a norma está em vigor; se o compilado está atualizado; novidades legislativas; acervo de um tema com planilha. Use SEMPRE que o usuário citar ou perguntar sobre lei, LC, decreto, decreto-lei, MP, emenda ou a Constituição/ADCT ("art. 74 da Lei 9.430", "LC 214", "RIR", "CTN"), pedir a redação atual ou original, perguntar se algo mudou, foi revogado ou desde quando vale, qual alíquota, prazo ou percentual a lei fixa, o que saiu de legislação sobre um tema, quiser levantar a legislação de IRPJ, CSLL, PIS/Cofins, IRRF, IBS/CBS ou outro tributo federal, ou conferir citações de lei num parecer. Não use para atos da RFB, CARF, tribunais nem normas estaduais ou municipais.'
---

# Legislação federal pelo MCP `planalto`

O servidor lê o **texto compilado do Planalto** (planalto.gov.br/ccivil_03) na hora e cruza com os
**Dados Abertos do Senado** (quem alterou cada dispositivo, situação da norma, indexação temática).
Ele existe porque a memória do modelo e a busca na web erram exatamente onde o advogado mais precisa
de certeza: redação alterada há poucos meses, regra com vigência futura, dispositivo revogado,
compilado do Planalto ainda não atualizado.

**Se as ferramentas `planalto` (`ler_norma`, `historico_alteracoes`…) não estiverem na sessão**, diga
isso ao usuário logo no início e só então responda pela web ou pela memória, avisando que o texto
pode estar desatualizado e sem a checagem de vigência.

## Ferramentas

| Ferramenta | Para quê |
|---|---|
| `ler_norma(referencia, dispositivo, termo, modo, pagina)` | Texto vigente (ou histórico, com as redações riscadas) da norma ou de um dispositivo; busca por termo dentro dela. Traz **alertas** de vigência futura, revogação marcada e alteração não incorporada. |
| `estrutura_norma(referencia)` | Sumário (títulos, capítulos, seções e artigos de cada um) e anexos. Primeiro passo em normas grandes. |
| `consultar_norma(referencia)` | Ficha: ementa, data, situação (revogada, convertida, sem eficácia), nº de alteradoras, última alteração, regulamentos. |
| `historico_alteracoes(referencia, dispositivo, desde, direcao)` | Quem alterou a norma ou o dispositivo, com data e ação; `direcao="feitas"` mostra o que a norma alterou. |
| `verificar_atualizacao(referencia)` | Confere se o compilado do Planalto já incorpora todas as alterações conhecidas e lista efeitos e revogações futuras. |
| `buscar_normas(consulta, tipos, ano_inicio, ano_fim)` | Busca no catálogo por ementa, apelido e indexação do Senado. **Não lê o texto das normas.** |
| `novidades_legislativas(dias, desde, tema, tipos)` | O que foi publicado no período, com filtro temático. |
| `mapear_tema(tema, normas_extras, termos_extras, ...)` | Acervo de um tema em camadas (núcleo, alteradoras, relacionadas), com planilha. |
| `listar_temas()`, `status_indice()`, `sincronizar_catalogo(...)` | Temas prontos; estado e carga do catálogo local. |

`referencia` aceita citações usuais: "Lei 9.430/1996", "Lei nº 12.973, de 2014", "LC 214/2025",
"Decreto 9.580/2018", "RIR/2018", "CTN", "CF", "ADCT", "MP 2.158-35/2001", "DL 1.598/77",
"EC 132/2023" e também o dispositivo junto ("art. 74, § 12 da Lei 9.430/96"). Sem o ano, a
ferramenta lista os candidatos; escolha o certo, não presuma.

## Regras que fazem a resposta ser confiável

1. **Texto de lei vem da ferramenta, nunca da memória.** Mesmo leis "conhecidas" mudam (a Lei 9.430,
   a 10.833 e o CTN foram alterados em 2025–2026). Cite só o que `ler_norma` devolveu.
2. **Leia o dispositivo, não a lei inteira.** `ler_norma("Lei 10.833/2003", dispositivo="art. 3º, IX")`
   é rápido e traz o contexto (o caput a que o inciso se liga). Em norma grande (LC 214, RIR, CLT,
   Código Civil), use `estrutura_norma` ou `termo=` para achar o artigo antes de ler. Ordem
   invertida também funciona ("inciso II do § 1º do art. 3º"); ADCT: "art. 76 do ADCT".
3. **Leia o cabeçalho da resposta antes do texto e repasse ao usuário o que ele disser.** Os campos
   abaixo existem para impedir a resposta errada; ignorá-los anula o valor da ferramenta:
   - `ALERTA`: redação mostrada que **só produz efeitos no futuro** (com a data e a "Redação que ainda
     se aplica"), revogação com data marcada (ex.: LC 214, art. 542, sobre PIS/Cofins a partir de
     1º/1/2027), alteração registrada pelo Senado e ainda não incorporada ao compilado, ou redação que
     veio de MP que perdeu a eficácia. **Havendo ALERTA, a resposta ao usuário deve começar por ele**
     e distinguir "o que vale hoje" de "o que valerá a partir de".
   - `AVISO` de rede (cópia local de data X), de edição (MP intermediária: o texto é o da última
     reedição), de duplicidade (o Planalto deixou duas redações sem risco), de situação (MP com prazo
     vencido): mencione-os quando afetarem a conclusão.
   - `Contexto`: o caput/parágrafo de que o inciso depende; leia-o para não tirar o inciso de contexto.
   - Notas "(Redação dada pela Lei X, de AAAA)" no fim de cada linha: dizem a origem e a data daquela
     redação. Use-as para responder "desde quando".
   Ver `references/campos-e-armadilhas.md`.
4. **Antes de afirmar que uma regra está vigente e atualizada**, quando a conclusão depender disso
   (parecer, cálculo, prazo, alíquota), rode `verificar_atualizacao` da norma e trate a conclusão:
   - `ATENÇÃO`: há lei alteradora que o compilado ainda não mostra. Leia a alteradora
     (`ler_norma` da lei listada) antes de concluir.
   - `A CONFERIR`: alteração só em "Vide"/cabeçalho (em geral vigência futura), incorporação parcial
     ou registro duvidoso do Senado. Confira os itens listados no ponto que interessa ao caso.
   - `efeitos_e_revogacoes_futuras`: redações e revogações com data; diga ao usuário até quando a regra
     atual vale.
   - "reflete as alterações": pode afirmar com a ressalva padrão (texto do Planalto, não do DOU).
5. **Situação da norma**: para "está em vigor?", use `consultar_norma` e confirme no próprio texto
   (`ler_norma`): norma toda riscada = revogada/sem eficácia. MP antiga "em tramitação" anterior à
   EC 32/2001 continua valendo por força do art. 2º da emenda; a ficha já diz isso.
6. **Busca não é prova de inexistência.** `buscar_normas` olha ementa, apelido e indexação; zero
   resultados não significa que não haja regra. Para um assunto **dentro** de uma lei, use
   `ler_norma(lei_principal, termo="...")`. Para o acervo completo, `mapear_tema`.
7. **Regras autônomas não aparecem no dispositivo.** Uma lei posterior pode mudar a aplicação de um
   artigo sem alterar o texto dele (ex.: o acréscimo de percentuais de presunção da LC 224/2025 sobre o
   art. 15 da Lei 9.249). Quando o tema for sensível a isso (alíquotas, presunções, benefícios),
   procure também com `novidades_legislativas`/`buscar_normas` pelo tema e diga ao usuário que fez.

## Fluxos

### "Qual a redação vigente de X?" / "Qual a alíquota/prazo/percentual?"
`ler_norma(norma, dispositivo)` → leia ALERTA, avisos e contexto → responda com o texto (citação
literal do trecho decisivo), a nota de origem e a data. Se houver ALERTA de efeitos futuros, dê as
duas redações com as datas. Se a pergunta depender de atualidade recente, acrescente
`verificar_atualizacao`.

### "Isso mudou?" / "Está atualizado?" / "Desde quando vale?"
`historico_alteracoes(norma, dispositivo, desde=...)` para a linha do tempo (alterações em
`alteracoes`; ressalvas, regulamentos e conversões de MP em `outras_relacoes`; normas que o Senado
registrou sem dizer o dispositivo em `alteracoes_sem_dispositivo`, que podem ter atingido o ponto) →
`verificar_atualizacao` para saber se o compilado incorporou tudo → `ler_norma(..., modo="historico")`
se precisar mostrar a redação anterior.

### "Compare a redação original e a atual"
`ler_norma(norma, dispositivo, modo="historico")`: trechos entre `~~ ~~` são redações anteriores
(riscadas no Planalto), a última é a vigente; as notas dizem qual lei trocou cada uma.

### "A norma X está em vigor?" / "A MP foi convertida?"
`consultar_norma` (situação, conversão, revogação) → `ler_norma` se a situação vier vazia ou
ambígua. Para MP: a ficha mostra conversão/caducidade; o histórico com `direcao="feitas"` mostra o
que ela alterou (cruzado com o próprio texto, porque o registro do Senado costuma ser incompleto).

### "Levante a legislação de [tema]"
`listar_temas()` → se o tema existir, `mapear_tema("IRPJ")` (combina: "IRPJ/CSLL"). Tema livre:
**informe `normas_extras`** com as leis-base (ex.: JCP → `["Lei 9.249/1995, art. 9"]`; subvenções →
`["Lei 14.789/2023", "Lei 12.973/2014, art. 30"]`, o art. 30 já revogado, para trazer o histórico), senão o resultado depende só da ementa. Explique as
camadas (núcleo, alteradoras/regulamentadoras, relacionadas) e entregue o caminho da planilha.
Escopos e termos dos temas prontos: `references/temas.md`.

### "O que saiu de novo sobre [tema]?"
`novidades_legislativas(dias=30, tema="PIS/COFINS")`; o `motivo` diz por que cada norma entrou. Se a
resposta disser que não revalidou os quadros do Planalto, avise que normas dos últimos dias podem
faltar. Para cada norma relevante, abra o texto antes de resumir.

### Conferir citações de um texto (memorando, parecer)
Para cada citação: `ler_norma` do dispositivo → o texto diz o que se afirma? → há ALERTA? → a nota
de origem bate com a data alegada? Use a skill `memo-tributario-verificadora` para a disciplina de
auditoria; esta skill fornece o texto oficial.

## Como responder

- Cite **norma + dispositivo + nota de origem** ("art. N, § X, da Lei A/AAAA, na redação dada pela Lei
  B/AAAA"; sem nota, "redação original") e o link do Planalto que a ferramenta devolve.
- Separe o que é **texto** (da ferramenta) do que é **interpretação** (sua).
- Diga o que não foi possível conferir (Senado fora, cópia local, alerta "não verificado").
- Feche, quando a conclusão for sensível, com: "texto compilado do Planalto em [data]; o oficial é
  o publicado no DOU".

## Fora do alcance (use outra skill)

Atos da RFB (IN, SC, PN, ADI) → `normas-rfb-mcp`; CARF/CSRF → `carf-jurisprudencia-mcp`; STF, STJ,
TRFs → `tribunais-federais`; tributos estaduais e municipais, TJs, TIT → `tribunais-estaduais`.
Decretos legislativos, decretos não numerados e normas estaduais/municipais não estão no servidor
(ele recusa a citação).

## Operação

- Primeiro uso numa máquina: o catálogo se monta sozinho (alguns minutos); `status_indice` mostra o
  estado. `sincronizar_catalogo(detalhes_senado=True)` melhora busca e temas (roda em segundo plano).
- Erro "Portal indisponível": tente de novo em alguns minutos; se a resposta vier com aviso de cópia
  local, use-a dizendo a data.
- Respostas longas vêm paginadas (`pagina=2`…); a página indica quantas há.
