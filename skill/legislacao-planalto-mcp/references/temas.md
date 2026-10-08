# Temas pré-configurados (mapear_tema, novidades_legislativas)

Gerado de `src/mcp_planalto/temas.py`. O **núcleo** são as leis-base; o mapeamento segue o grafo de
alterações do Senado a partir delas, contando só alterações nos artigos do **escopo** (quando há).
Os **termos** buscam na ementa/indexação. Temas combinam: `"IRPJ/CSLL"`, `"PIS/COFINS e IPI"`.
Tema fora desta lista: passe `normas_extras` (leis-base, com escopo se quiser:
`"Lei 9.249/1995, art. 9"`) e `termos_extras` (frases entre aspas).

## IRPJ — Imposto sobre a Renda das Pessoas Jurídicas

- Núcleo: DL 1.598/1977; Lei 4.506/1964; Lei 8.541/1992; Lei 8.981/1995, exceto arts. 7 a 24; Lei 9.065/1995; Lei 9.249/1995, exceto art. 34; Lei 9.316/1996; Lei 9.430/1996, arts. 1 a 30, 32 a 47, 51 a 60 e 78; Lei 9.532/1997, arts. 1 a 18 e 36; Lei 9.718/1998, arts. 9 a 11, 13, 14 e 16; MP 2.158-35/2001, arts. 20 a 23, 30, 34, 41, 59 a 62, 74, 82 e 83; Lei 11.196/2005, arts. 17 a 26, 31, 32 e 34 a 37; Lei 12.249/2010, arts. 24 a 26; Lei 12.973/2014; Lei 14.596/2023; Lei 14.789/2023; LC 224/2025; Decreto 9.580/2018
- Termos: "imposto de renda" "pessoa juridica", "imposto sobre a renda" "pessoa juridica", "imposto de renda" "pessoas juridicas", "imposto sobre a renda das pessoas juridicas", IRPJ, "lucro real", "lucro presumido", "lucro arbitrado", "lucro da exploracao", "juros sobre o capital proprio", "precos de transferencia", subcapitalizacao, "lucros auferidos no exterior", "depreciacao acelerada", "deducao do imposto sobre a renda", "deducao do imposto de renda"
- Também aceita: imposto de renda pessoa juridica, imposto sobre a renda das pessoas juridicas, ir pj

## CSLL — Contribuição Social sobre o Lucro Líquido

- Núcleo: Lei 7.689/1988; Lei 8.981/1995, arts. 57 a 59; Lei 9.065/1995; Lei 9.249/1995, exceto art. 34; Lei 9.316/1996; Lei 9.430/1996, arts. 28 a 30; Lei 10.684/2003, art. 22; Lei 11.727/2008, art. 17; Lei 12.973/2014; MP 2.158-35/2001, arts. 6 a 8, 21, 22 e 83; Lei 13.169/2015; Lei 15.079/2024; LC 224/2025
- Termos: "contribuicao social sobre o lucro liquido", CSLL, "contribuicao social sobre o lucro"
- Também aceita: contribuicao social sobre o lucro, contribuicao social sobre o lucro liquido

## PIS/COFINS — Contribuições para o PIS/Pasep e a Cofins

- Núcleo: LC 7/1970; LC 70/1991; Lei 9.715/1998; Lei 9.718/1998, arts. 1 a 8-B; Lei 10.147/2000; MP 2.158-35/2001, arts. 1 a 5, 13 a 20, 31, 42, 43 e 54; Lei 10.485/2002; Lei 10.637/2002; Lei 10.833/2003; Lei 10.865/2004; Lei 10.925/2004; Lei 11.033/2004, arts. 13 a 16; Lei 11.196/2005, arts. 1 a 16, 28 a 30 e 41 a 66; Lei 11.488/2007, arts. 1 a 5; Lei 12.973/2014, arts. 52 a 57
- Termos: "contribuicao para o pis/pasep", "contribuicao para o pis", COFINS, "contribuicao para o financiamento da seguridade social", "pis/pasep" cofins, "nao cumulatividade" contribuicao
- Também aceita: pis, cofins, pis cofins, pis/pasep, contribuicoes sobre a receita

## IRPF — Imposto sobre a Renda das Pessoas Físicas

- Núcleo: Lei 7.713/1988; Lei 8.134/1990; Lei 8.981/1995, arts. 7 a 24; Lei 9.250/1995; Lei 9.532/1997, arts. 11, 17 e 21 a 25; Lei 11.053/2004; Lei 11.196/2005, arts. 38 a 40; Lei 14.973/2024, arts. 6 a 17; Lei 11.482/2007; Lei 14.754/2023; Lei 15.270/2025; Decreto 9.580/2018
- Termos: "imposto de renda" "pessoa fisica", "imposto sobre a renda" "pessoa fisica", "imposto de renda" "pessoas fisicas", IRPF, "declaracao de ajuste anual", "rendimentos tributaveis", "imposto sobre a renda" deduc*, "imposto de renda" deduc*
- Também aceita: imposto de renda pessoa fisica, imposto sobre a renda das pessoas fisicas

## IRRF — Imposto sobre a Renda Retido na Fonte

- Núcleo: Lei 7.713/1988; Lei 8.981/1995, arts. 60 a 82; Lei 9.249/1995, arts. 9 a 11 e 28; Lei 9.481/1997; Lei 9.779/1999, arts. 1 a 9; MP 2.158-35/2001, arts. 9, 26 a 29, 55, 63 e 65; Lei 11.033/2004; Lei 11.312/2006; Lei 12.249/2010, art. 60; Lei 9.430/1996, arts. 64 e 67 a 72; Lei 12.431/2011; Lei 14.754/2023; Lei 15.270/2025; Decreto 9.580/2018
- Termos: "imposto de renda retido na fonte", "retencao na fonte", IRRF, "imposto de renda na fonte", "imposto sobre a renda retido na fonte", "imposto de renda incidente na fonte", "dupla tributacao", bitributacao
- Também aceita: retencao na fonte, imposto de renda na fonte

## IPI — Imposto sobre Produtos Industrializados

- Núcleo: Lei 4.502/1964; DL 34/1966; Lei 7.798/1989; Lei 9.779/1999, arts. 11 e 12; Decreto 7.212/2010; Lei 9.532/1997, arts. 37 a 57; MP 2.158-35/2001, arts. 32 a 40, 56 a 58 e 79 a 81; Decreto 11.158/2022
- Termos: "imposto sobre produtos industrializados", IPI
- Também aceita: imposto sobre produtos industrializados

## IOF — Imposto sobre Operações Financeiras

- Núcleo: Lei 5.143/1966; DL 1.783/1980; Lei 7.766/1989; Lei 8.033/1990; Lei 8.894/1994; Lei 9.779/1999, art. 13; Decreto 6.306/2007; Lei 9.532/1997, art. 58; Lei 9.718/1998, art. 15
- Termos: "imposto sobre operacoes financeiras", IOF, "operacoes de credito cambio e seguro"
- Também aceita: imposto sobre operacoes financeiras

## SIMPLES — Simples Nacional

- Núcleo: LC 123/2006, arts. 1 a 41 e 77 a 89; LC 128/2008; LC 139/2011; LC 147/2014; LC 155/2016
- Termos: "simples nacional", "microempresa e empresa de pequeno porte" tributario, "microempreendedor individual" tribut*, "estatuto nacional da microempresa"
- Também aceita: simples nacional, simples, mei

## IBS/CBS — Reforma Tributária do consumo: IBS, CBS e Imposto Seletivo

- Núcleo: EC 132/2023; LC 214/2025; LC 227/2026; Decreto 12.955/2026
- Termos: "imposto sobre bens e servicos", IBS, "contribuicao social sobre bens e servicos", CBS, "imposto seletivo", CGIBS
- Também aceita: reforma tributaria, ibs, cbs, imposto seletivo, ibs cbs

## PRECOS DE TRANSFERENCIA — Preços de transferência

- Núcleo: Lei 9.430/1996, arts. 18 a 24-C; Lei 12.249/2010, arts. 24 a 26; Lei 14.596/2023
- Termos: "precos de transferencia", "pessoa vinculada", "regime fiscal privilegiado", "tributacao favorecida"
- Também aceita: transfer pricing, precos de transferencia, tp

## TRIBUTACAO INTERNACIONAL — Lucros no exterior, CFC, offshores e trusts

- Núcleo: Lei 9.249/1995, arts. 25 a 27; Lei 9.532/1997, art. 1; MP 2.158-35/2001, art. 74; Lei 12.249/2010, arts. 24 a 26; Lei 12.973/2014, arts. 76 a 92; Lei 14.596/2023; Lei 14.754/2023
- Termos: "lucros auferidos no exterior", "controlada no exterior", "coligada no exterior", "bases universais", "entidades controladas no exterior", "aplicacoes financeiras no exterior", "dupla tributacao", bitributacao
- Também aceita: lucros no exterior, cfc, offshores, tributacao em bases universais

## PREVIDENCIARIAS — Contribuições previdenciárias

- Núcleo: Lei 8.212/1991, arts. 10 a 56 e 88 a 105; Lei 10.666/2003, arts. 1 e 4 a 10; Lei 11.457/2007; Lei 12.546/2011, arts. 7 a 10; Lei 13.670/2018; Lei 14.973/2024, arts. 1 a 5; Decreto 3.048/1999, arts. 9 a 17 e 194 a 293
- Termos: "contribuicao previdenciaria", "contribuicoes previdenciarias", "folha de salarios", "contribuicao previdenciaria sobre a receita bruta", "desoneracao da folha", "fator acidentario", "custeio da seguridade social", "salario de contribuicao"
- Também aceita: contribuicoes previdenciarias, contribuicao previdenciaria, inss, cprb, desoneracao da folha

## PROCESSO FISCAL — Processo administrativo fiscal, CARF, consulta e transação

- Núcleo: Decreto 70.235/1972; Lei 9.430/1996, arts. 44, 47 a 50, 61, 63, 73 a 74-A e 83; Lei 9.784/1999; Lei 9.532/1997, arts. 64 e 64-A; Lei 10.522/2002; Lei 11.941/2009, exceto arts. 15 a 24; Decreto 7.574/2011; Lei 13.988/2020; Lei 14.689/2023; LC 225/2026; LC 227/2026; LC 236/2026
- Termos: "processo administrativo fiscal", CARF, "conselho administrativo de recursos fiscais", "transacao tributaria", "transacao resolutiva", "processo de consulta", "voto de qualidade", "empate" carf, "defesa do contribuinte"
- Também aceita: pat, processo administrativo tributario, carf, contencioso administrativo

## CTN — Normas gerais de direito tributário

- Núcleo: Lei 5.172/1966, arts. 1 a 82-A e 96 a 218; LC 104/2001; LC 118/2005; LC 225/2026; LC 236/2026
- Termos: "codigo tributario nacional", "normas gerais de direito tributario"
- Também aceita: codigo tributario nacional, normas gerais

## ICMS/ISS — Leis complementares de ICMS e ISS

- Núcleo: LC 24/1975; DL 406/1968; LC 87/1996; LC 116/2003; LC 157/2016; LC 160/2017; LC 175/2020; LC 190/2022; LC 192/2022; LC 194/2022
- Termos: ICMS, "imposto sobre servicos de qualquer natureza", ISSQN, "circulacao de mercadorias"
- Também aceita: icms, iss, issqn
