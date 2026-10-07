"""Temas tributários pré-configurados para o mapeamento de acervo.

Cada tema tem:
- termos: buscas no catálogo (ementa, apelido e indexação do Senado). Termos separados
  por espaço são combinados com E; use aspas para frase exata.
- nucleo: normas estruturantes do tema. O mapeamento parte delas e segue o grafo de
  alterações do Senado para achar todas as normas que as alteraram, revogaram ou
  regulamentaram, inclusive as de ementa genérica ("Altera a Lei nº ...").

As listas são ponto de partida, não verdade fechada: o usuário pode acrescentar termos
e normas em cada chamada.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .referencias import normalizar


@dataclass(frozen=True)
class Tema:
    sigla: str
    nome: str
    termos: tuple[str, ...]
    nucleo: tuple[str, ...]
    apelidos: tuple[str, ...] = field(default_factory=tuple)


TEMAS: dict[str, Tema] = {t.sigla: t for t in [
    Tema(
        "IRPJ", "Imposto sobre a Renda das Pessoas Jurídicas",
        termos=(
            '"imposto de renda" "pessoa juridica"', '"imposto sobre a renda" "pessoa juridica"',
            '"imposto de renda" "pessoas juridicas"', '"imposto sobre a renda das pessoas juridicas"',
            "IRPJ", '"lucro real"', '"lucro presumido"', '"lucro arbitrado"', '"lucro da exploracao"',
            '"juros sobre o capital proprio"', '"precos de transferencia"', "subcapitalizacao",
            '"lucros auferidos no exterior"',
        ),
        nucleo=(
            "DL 1.598/1977", "Lei 4.506/1964", "Lei 8.981/1995", "Lei 9.065/1995", "Lei 9.249/1995",
            "Lei 9.430/1996", "Lei 9.532/1997", "Lei 9.718/1998", "MP 2.158-35/2001", "Lei 11.196/2005",
            "Lei 12.249/2010", "Lei 12.973/2014", "Lei 14.596/2023", "Lei 14.789/2023", "Decreto 9.580/2018",
        ),
        apelidos=("imposto de renda pessoa juridica", "imposto sobre a renda das pessoas juridicas", "ir pj"),
    ),
    Tema(
        "CSLL", "Contribuição Social sobre o Lucro Líquido",
        termos=('"contribuicao social sobre o lucro liquido"', "CSLL", '"contribuicao social sobre o lucro"'),
        nucleo=("Lei 7.689/1988", "Lei 8.981/1995", "Lei 9.249/1995", "Lei 9.430/1996", "Lei 11.727/2008",
                "Lei 12.973/2014", "Lei 13.169/2015", "Lei 15.079/2024"),
        apelidos=("contribuicao social sobre o lucro", "contribuicao social sobre o lucro liquido"),
    ),
    Tema(
        "PIS/COFINS", "Contribuições para o PIS/Pasep e a Cofins",
        termos=("PIS", "PASEP", "COFINS", '"contribuicao para o financiamento da seguridade social"',
                '"programa de integracao social"', '"nao cumulatividade" contribuicao'),
        nucleo=("LC 7/1970", "LC 70/1991", "Lei 9.715/1998", "Lei 9.718/1998", "Lei 10.147/2000",
                "Lei 10.485/2002", "Lei 10.637/2002", "Lei 10.833/2003", "Lei 10.865/2004", "Lei 12.973/2014"),
        apelidos=("pis", "cofins", "pis cofins", "pis/pasep", "contribuicoes sobre a receita"),
    ),
    Tema(
        "IRPF", "Imposto sobre a Renda das Pessoas Físicas",
        termos=('"imposto de renda" "pessoa fisica"', '"imposto sobre a renda" "pessoa fisica"',
                '"imposto de renda" "pessoas fisicas"', "IRPF", '"declaracao de ajuste anual"',
                '"rendimentos tributaveis"'),
        nucleo=("Lei 7.713/1988", "Lei 8.134/1990", "Lei 9.250/1995", "Lei 11.482/2007", "Lei 14.754/2023",
                "Lei 15.270/2025", "Decreto 9.580/2018"),
        apelidos=("imposto de renda pessoa fisica", "imposto sobre a renda das pessoas fisicas"),
    ),
    Tema(
        "IRRF", "Imposto sobre a Renda Retido na Fonte",
        termos=('"imposto de renda retido na fonte"', '"retencao na fonte"', "IRRF", '"imposto de renda na fonte"',
                '"imposto sobre a renda retido na fonte"'),
        nucleo=("Lei 7.713/1988", "Lei 8.981/1995", "Lei 9.249/1995", "Lei 9.779/1999", "MP 2.158-35/2001",
                "Lei 10.833/2003", "Lei 11.033/2004", "Lei 14.754/2023", "Lei 15.270/2025"),
        apelidos=("retencao na fonte", "imposto de renda na fonte"),
    ),
    Tema(
        "IPI", "Imposto sobre Produtos Industrializados",
        termos=('"imposto sobre produtos industrializados"', "IPI"),
        nucleo=("Lei 4.502/1964", "Lei 7.798/1989", "Lei 9.779/1999", "Decreto 7.212/2010", "Decreto 11.158/2022"),
        apelidos=("imposto sobre produtos industrializados",),
    ),
    Tema(
        "IOF", "Imposto sobre Operações Financeiras",
        termos=('"imposto sobre operacoes financeiras"', "IOF", '"operacoes de credito cambio e seguro"'),
        nucleo=("Lei 5.143/1966", "Lei 8.894/1994", "Lei 9.779/1999", "Decreto 6.306/2007"),
        apelidos=("imposto sobre operacoes financeiras",),
    ),
    Tema(
        "SIMPLES", "Simples Nacional",
        termos=('"simples nacional"', "microempresa", '"empresa de pequeno porte"', '"microempreendedor individual"'),
        nucleo=("LC 123/2006", "LC 147/2014", "LC 155/2016"),
        apelidos=("simples nacional", "simples", "microempresa", "mei"),
    ),
    Tema(
        "IBS/CBS", "Reforma Tributária do consumo: IBS, CBS e Imposto Seletivo",
        termos=('"imposto sobre bens e servicos"', "IBS", '"contribuicao social sobre bens e servicos"', "CBS",
                '"imposto seletivo"', '"reforma tributaria"'),
        nucleo=("EC 132/2023", "LC 214/2025"),
        apelidos=("reforma tributaria", "ibs", "cbs", "imposto seletivo", "ibs cbs"),
    ),
    Tema(
        "PRECOS DE TRANSFERENCIA", "Preços de transferência",
        termos=('"precos de transferencia"', '"pessoa vinculada"', '"regime fiscal privilegiado"',
                '"tributacao favorecida"'),
        nucleo=("Lei 9.430/1996", "Lei 14.596/2023"),
        apelidos=("transfer pricing", "precos de transferencia", "tp"),
    ),
    Tema(
        "TRIBUTACAO INTERNACIONAL", "Lucros no exterior, CFC, offshores e trusts",
        termos=('"lucros auferidos no exterior"', '"controlada no exterior"', '"coligada no exterior"',
                '"bases universais"', "offshore", "trust", '"entidade controlada"'),
        nucleo=("Lei 9.249/1995", "MP 2.158-35/2001", "Lei 12.973/2014", "Lei 14.596/2023", "Lei 14.754/2023"),
        apelidos=("lucros no exterior", "cfc", "offshores", "tributacao em bases universais"),
    ),
    Tema(
        "PREVIDENCIARIAS", "Contribuições previdenciárias",
        termos=('"contribuicao previdenciaria"', '"contribuicoes previdenciarias"', '"folha de salarios"',
                '"contribuicao previdenciaria sobre a receita bruta"', "desoneracao"),
        nucleo=("Lei 8.212/1991", "Lei 12.546/2011", "Lei 14.973/2024", "Decreto 3.048/1999"),
        apelidos=("contribuicoes previdenciarias", "inss", "cprb", "desoneracao da folha"),
    ),
    Tema(
        "PROCESSO FISCAL", "Processo administrativo fiscal, CARF, consulta e transação",
        termos=('"processo administrativo fiscal"', "CARF", '"conselho administrativo de recursos fiscais"',
                '"transacao tributaria"', '"processo de consulta"', '"voto de qualidade"'),
        nucleo=("Decreto 70.235/1972", "Lei 9.430/1996", "Lei 11.941/2009", "Lei 13.988/2020", "Lei 14.689/2023"),
        apelidos=("pat", "processo administrativo tributario", "carf", "contencioso administrativo"),
    ),
    Tema(
        "CTN", "Normas gerais de direito tributário",
        termos=('"codigo tributario nacional"', '"normas gerais de direito tributario"'),
        nucleo=("Lei 5.172/1966", "LC 104/2001", "LC 118/2005"),
        apelidos=("codigo tributario nacional", "normas gerais"),
    ),
    Tema(
        "ICMS/ISS", "Leis complementares de ICMS e ISS",
        termos=("ICMS", '"imposto sobre servicos de qualquer natureza"', "ISS",
                '"circulacao de mercadorias"'),
        nucleo=("LC 87/1996", "LC 116/2003", "LC 157/2016", "LC 160/2017", "LC 190/2022", "LC 194/2022"),
        apelidos=("icms", "iss", "issqn"),
    ),
]}


def localizar_tema(nome: str) -> Tema | None:
    n = normalizar(nome).replace("-", " ").strip()
    for t in TEMAS.values():
        if n == normalizar(t.sigla) or n == normalizar(t.nome) or n in (normalizar(a) for a in t.apelidos):
            return t
    sem_barra = n.replace("/", " ").replace("  ", " ")
    for t in TEMAS.values():
        if sem_barra == normalizar(t.sigla).replace("/", " "):
            return t
    return None
