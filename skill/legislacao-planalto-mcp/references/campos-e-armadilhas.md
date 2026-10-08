# Campos das respostas e armadilhas das fontes

## Sumário
1. Campos de `ler_norma`
2. Campos de `verificar_atualizacao`
3. Campos de `historico_alteracoes` e `consultar_norma`
4. Armadilhas do Planalto
5. Armadilhas do Senado
6. O que o servidor não detecta

## 1. Campos de `ler_norma`

| Campo (no cabeçalho do texto) | Significado | O que fazer |
|---|---|---|
| `ALERTA: ... só produz efeitos a partir de DD/MM/AAAA` | A redação mostrada como vigente é a nova, mas a cláusula de vigência da lei alteradora adia os efeitos. | Responder com as duas redações: a "Redação que ainda se aplica" até a data e a nova depois dela. |
| `ALERTA: a partir de DD/MM/AAAA, a [norma] (art. X) revoga dispositivos desta norma que abrangem este artigo ou partes dele` | Revogação com data marcada (ex.: LC 214/2025, arts. 542 e 543). O trecho citado lista exatamente o que cai. | Conferir se o dispositivo lido está na lista do trecho (às vezes só parágrafos do artigo caem) e dizer até quando vale. |
| `ALERTA: o Senado registra alterações recentes deste dispositivo que não aparecem nas notas` | Lei nova (até ~2 anos) que o compilado não mostra no ponto lido. | Abrir a lei alteradora com `ler_norma` antes de concluir; pode ser vigência futura ou atraso do Planalto. |
| `ALERTA: a redação ... veio da MP X, que não está mais em vigor` | Texto incluído por MP que caducou/foi rejeitada e não aparece convertido. | Tratar a redação como possivelmente sem validade; confirmar a situação da MP (`consultar_norma`). |
| `AVISO: o portal não respondeu agora; usando a cópia local de ...` | Planalto fora do ar; texto da cópia em cache. | Usar dizendo a data da cópia. |
| `AVISO: Você pediu a MP X; ... o texto é o da última edição da família` | MP reeditada (antes de 2001). | Avisar que a redação de uma edição intermediária pode ter sido outra. |
| `AVISO: O Planalto mostra mais de uma redação sem risco` | Erro de compilação do Planalto. | Em geral vale a última (nota "Redação dada" mais recente); recomendar conferir no DOU. |
| `Aviso: Não foi possível conferir agora, no Senado...` | O alerta de alteração não incorporada não pôde ser checado. | Se a atualidade importar, rodar `verificar_atualizacao` depois. |
| `Vigência da [lei], que deu a redação de ...` | Cláusula de vigência da lei recente (até ~2 anos) que deu a redação lida. | Ver qual inciso da cláusula alcança o dispositivo; procurar regras de transição na mesma lei. |
| `AVISO: a cláusula de vigência ... menciona efeitos em data futura` | A data pode valer só para parte da lei. | Confirmar na cláusula se alcança o dispositivo; se alcançar, responder com as duas redações. |
| `Contexto (dispositivo superior)` | Caput/parágrafo/inciso a que o trecho lido pertence. | Ler junto: o inciso sozinho pode inverter o sentido ("exceto", "não se aplica"). |
| `Notas de vigência da norma` | "(Vide Lei X)" recentes do cabeçalho, que valem para todo o texto. | Se citar lei recente, verificar se afeta o ponto. |
| `Situação` | Revogada, convertida, sem eficácia, em vigor por força da EC 32. | Repassar. |
| `Artigos com o termo: N: art. X, art. Y` | Resultado de `termo=`; mostra até 15 artigos inteiros. | Para mais, paginar ou refinar o termo. |
| Linhas com `~~...~~` (modo histórico) | Redações anteriores riscadas no Planalto. | A última redação não riscada é a vigente. |

## 2. Campos de `verificar_atualizacao`

- `conclusao`: ATENÇÃO / A CONFERIR / "reflete... mas há efeitos FUTUROS" / "reflete as alterações".
- `nao_refletidas_no_texto`: alteradoras (Senado, até 2 anos) cuja nota não está nos artigos
  alterados. Ler cada uma.
- `a_conferir` (com `motivo`): só em "Vide"; incorporação parcial (lista os artigos sem nota);
  dispositivo inexistente (provável registro errado do Senado); leis recentes cuja ementa diz alterar a
  norma mas o texto só cita em "Vide".
- `normas_recentes_que_citam_na_ementa`: leis de até ~13 meses que dizem alterar a norma e que o
  texto nem cita. Fortes candidatas a alteração não incorporada.
- `efeitos_e_revogacoes_futuras`: `efeitos_futuros` (dispositivo, data, cláusula, redação que ainda
  se aplica) e `revogacao_futura` (data, dispositivo revogador, trecho com a lista).
- `antigas_sem_nota`, `mps_antigas_*`: informativos (remissões antigas, MPs encerradas); em geral
  não indicam desatualização.

## 3. Campos de `historico_alteracoes` e `consultar_norma`

- `alteracoes`: grupos por norma alteradora (MP reeditada agrupada: "última de N edições").
- `outras_relacoes`: ressalvas, regulamentações, novo tratamento da matéria, conversões e reedições
  de MP, decretos registrados como "alteradores" de lei (decreto não altera lei). Não mudam o texto
  do dispositivo, mas podem mudar sua aplicação.
- `alteracoes_sem_dispositivo`: o Senado registrou alteração da norma sem dizer onde; pode ter
  atingido o dispositivo pedido.
- `citadas_em_clausulas_de_alteracao_sem_registro_no_senado` (direcao="feitas"): normas que o próprio
  texto altera/revoga e que o Senado não registrou.
- Ficha: `alterada_por` (famílias de normas, sem decretos), `ultima_alteracao`, `regulamentada_por`,
  `aviso_situacao` (MP "em tramitação" com prazo vencido), `aviso_edicao`.

## 4. Armadilhas do Planalto

- **Redação nova antes da vigência**: o compilado mostra a redação nova desde a publicação. O servidor
  resolve "(Produção de efeitos)/(Vigência)/(Vide)" e alerta; se a cláusula usar fórmula que ele não
  calcula, o alerta não sai. Em tema sensível, leia a cláusula de vigência da lei alteradora.
- **Atraso na compilação**: dias ou semanas após a publicação. `verificar_atualizacao` existe para isso.
- **Nota com número errado** (link certo) e **duas redações sem risco**: o servidor contorna/avisa.
- **Revogação deixada sem risco** em parágrafo de artigo revogado: confira o caput.
- **Anexos** em páginas separadas (ex.: anexos do RPS) não entram na leitura da norma principal.

## 5. Armadilhas do Senado

- Registra às vezes no dispositivo errado ("§ 15" que é inciso XV) ou até na norma errada; por isso
  "dispositivo inexistente" vai para "a conferir", nunca para ATENÇÃO.
- Registro de normas recentes demora; `direcao="feitas"` costuma estar incompleta em leis e MPs novas.
- Não registra por dispositivo as revogações com data futura (veja `ler_norma`/`verificar_atualizacao`).

## 6. O que o servidor não detecta

- Regra autônoma que muda a aplicação de um dispositivo sem alterar seu texto (presunções,
  alíquotas fixadas em outra lei, benefícios que excepcionam a regra geral).
- Decisões judiciais com efeito sobre a norma (ADI, RE com repercussão geral) além das notas que o
  Planalto põe no texto — use `tribunais-federais`.
- Atos infralegais da RFB — use `normas-rfb-mcp`.
