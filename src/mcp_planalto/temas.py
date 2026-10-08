"""Temas tributários pré-configurados para o mapeamento de acervo.

Cada tema tem:
- termos: buscas no catálogo (ementa, apelido e indexação do Senado). Termos separados
  por espaço são combinados com E; use aspas para frase exata.
- nucleo: normas estruturantes do tema. O mapeamento parte delas e segue o grafo de
  alterações do Senado para achar as normas que as alteraram, revogaram ou regulamentaram,
  inclusive as de ementa genérica ("Altera a Lei nº ...").
  Normas que tratam de vários assuntos podem ter ESCOPO por dispositivo
  ("Lei 9.430/1996, arts. 18 a 24-C"): só alterações nesses artigos contam para o tema.

As listas são ponto de partida, não verdade fechada: o usuário pode acrescentar termos
e normas em cada chamada.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

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
            '"lucros auferidos no exterior"', '"depreciacao acelerada"',
        ),
        nucleo=(
            "DL 1.598/1977", "Lei 4.506/1964", "Lei 8.541/1992", "Lei 8.981/1995, exceto arts. 7 a 24",
            "Lei 9.065/1995", "Lei 9.249/1995, exceto art. 34", "Lei 9.316/1996",
            "Lei 9.430/1996, arts. 1 a 30, 32 a 43, 45 a 47, 51 a 60 e 78", "Lei 9.532/1997, arts. 1 a 18 e 36",
            "Lei 9.718/1998, arts. 9 a 11, 13, 14 e 16",
            "MP 2.158-35/2001, arts. 20 a 23, 30, 34, 41, 59 a 62, 74, 82 e 83",
            "Lei 11.196/2005, arts. 17 a 26, 31, 32 e 34 a 37", "Lei 12.249/2010, arts. 24 a 26", "Lei 12.973/2014",
            "Lei 14.596/2023", "Lei 14.789/2023", "LC 224/2025", "Decreto 9.580/2018",
        ),
        apelidos=("imposto de renda pessoa juridica", "imposto sobre a renda das pessoas juridicas", "ir pj"),
    ),
    Tema(
        "CSLL", "Contribuição Social sobre o Lucro Líquido",
        termos=('"contribuicao social sobre o lucro liquido"', "CSLL", '"contribuicao social sobre o lucro"'),
        nucleo=("Lei 7.689/1988", "Lei 8.981/1995, arts. 57 a 59", "Lei 9.065/1995", "Lei 9.249/1995, exceto art. 34",
                "Lei 9.316/1996", "Lei 9.430/1996, arts. 28 a 30", "Lei 10.684/2003, art. 22",
                "Lei 11.727/2008, art. 17", "Lei 12.973/2014",
                "Lei 13.169/2015", "Lei 15.079/2024", "LC 224/2025"),
        apelidos=("contribuicao social sobre o lucro", "contribuicao social sobre o lucro liquido"),
    ),
    Tema(
        "PIS/COFINS", "Contribuições para o PIS/Pasep e a Cofins",
        termos=('"contribuicao para o pis/pasep"', '"contribuicao para o pis"', "COFINS",
                '"contribuicao para o financiamento da seguridade social"', '"pis/pasep" cofins',
                '"nao cumulatividade" contribuicao'),
        nucleo=("LC 7/1970", "LC 70/1991", "Lei 9.715/1998", "Lei 9.718/1998, arts. 1 a 8-B", "Lei 10.147/2000",
                "MP 2.158-35/2001, arts. 1 a 5, 13 a 20, 31, 42, 43 e 54", "Lei 10.485/2002", "Lei 10.637/2002",
                "Lei 10.833/2003", "Lei 10.865/2004", "Lei 10.925/2004", "Lei 11.033/2004, arts. 13 a 16",
                "Lei 11.196/2005, arts. 1 a 16", "Lei 11.488/2007, arts. 1 a 5", "Lei 12.973/2014, arts. 52 a 57"),
        apelidos=("pis", "cofins", "pis cofins", "pis/pasep", "contribuicoes sobre a receita"),
    ),
    Tema(
        "IRPF", "Imposto sobre a Renda das Pessoas Físicas",
        termos=('"imposto de renda" "pessoa fisica"', '"imposto sobre a renda" "pessoa fisica"',
                '"imposto de renda" "pessoas fisicas"', "IRPF", '"declaracao de ajuste anual"',
                '"rendimentos tributaveis"'),
        nucleo=("Lei 7.713/1988", "Lei 8.134/1990", "Lei 8.981/1995, arts. 7 a 24", "Lei 9.250/1995",
                "Lei 9.532/1997, arts. 22 a 25", "Lei 11.053/2004", "Lei 11.482/2007", "Lei 14.754/2023",
                "Lei 15.270/2025", "Decreto 9.580/2018"),
        apelidos=("imposto de renda pessoa fisica", "imposto sobre a renda das pessoas fisicas"),
    ),
    Tema(
        "IRRF", "Imposto sobre a Renda Retido na Fonte",
        termos=('"imposto de renda retido na fonte"', '"retencao na fonte"', "IRRF", '"imposto de renda na fonte"',
                '"imposto sobre a renda retido na fonte"', '"imposto de renda incidente na fonte"'),
        nucleo=("Lei 7.713/1988", "Lei 8.981/1995, arts. 60 a 82", "Lei 9.249/1995, arts. 9 a 11 e 28",
                "Lei 9.481/1997", "Lei 9.779/1999, arts. 1 a 9", "MP 2.158-35/2001, arts. 9, 26 a 29, 55, 63 e 65",
                "Lei 11.033/2004", "Lei 12.431/2011", "Lei 14.754/2023", "Lei 15.270/2025",
                "Decreto 9.580/2018"),
        apelidos=("retencao na fonte", "imposto de renda na fonte"),
    ),
    Tema(
        "IPI", "Imposto sobre Produtos Industrializados",
        termos=('"imposto sobre produtos industrializados"', "IPI"),
        nucleo=("Lei 4.502/1964", "DL 34/1966", "Lei 7.798/1989", "Lei 9.779/1999, arts. 11 e 12", "Decreto 7.212/2010",
                "Decreto 11.158/2022"),
        apelidos=("imposto sobre produtos industrializados",),
    ),
    Tema(
        "IOF", "Imposto sobre Operações Financeiras",
        termos=('"imposto sobre operacoes financeiras"', "IOF", '"operacoes de credito cambio e seguro"'),
        nucleo=("Lei 5.143/1966", "DL 1.783/1980", "Lei 7.766/1989", "Lei 8.033/1990", "Lei 8.894/1994",
                "Lei 9.779/1999, art. 13", "Decreto 6.306/2007"),
        apelidos=("imposto sobre operacoes financeiras",),
    ),
    Tema(
        "SIMPLES", "Simples Nacional",
        termos=('"simples nacional"', '"microempresa e empresa de pequeno porte" tributario',
                '"microempreendedor individual" tribut*', '"estatuto nacional da microempresa"'),
        nucleo=("LC 123/2006, arts. 1 a 41 e 77 a 89", "LC 128/2008", "LC 139/2011", "LC 147/2014", "LC 155/2016"),
        apelidos=("simples nacional", "simples", "mei"),
    ),
    Tema(
        "IBS/CBS", "Reforma Tributária do consumo: IBS, CBS e Imposto Seletivo",
        termos=('"imposto sobre bens e servicos"', "IBS", '"contribuicao social sobre bens e servicos"', "CBS",
                '"imposto seletivo"', "CGIBS"),
        nucleo=("EC 132/2023", "LC 214/2025", "LC 227/2026", "Decreto 12.955/2026"),
        apelidos=("reforma tributaria", "ibs", "cbs", "imposto seletivo", "ibs cbs"),
    ),
    Tema(
        "PRECOS DE TRANSFERENCIA", "Preços de transferência",
        termos=('"precos de transferencia"', '"pessoa vinculada"', '"regime fiscal privilegiado"',
                '"tributacao favorecida"'),
        nucleo=("Lei 9.430/1996, arts. 18 a 24-C", "Lei 12.249/2010, arts. 24 a 26",
                "Lei 14.596/2023"),
        apelidos=("transfer pricing", "precos de transferencia", "tp"),
    ),
    Tema(
        "TRIBUTACAO INTERNACIONAL", "Lucros no exterior, CFC, offshores e trusts",
        termos=('"lucros auferidos no exterior"', '"controlada no exterior"', '"coligada no exterior"',
                '"bases universais"', '"entidades controladas no exterior"', '"aplicacoes financeiras no exterior"'),
        nucleo=("Lei 9.249/1995, arts. 25 a 27", "Lei 9.532/1997, art. 1", "MP 2.158-35/2001, art. 74",
                "Lei 12.249/2010, arts. 24 a 26", "Lei 12.973/2014, arts. 76 a 92",
                "Lei 14.596/2023", "Lei 14.754/2023"),
        apelidos=("lucros no exterior", "cfc", "offshores", "tributacao em bases universais"),
    ),
    Tema(
        "PREVIDENCIARIAS", "Contribuições previdenciárias",
        termos=('"contribuicao previdenciaria"', '"contribuicoes previdenciarias"', '"folha de salarios"',
                '"contribuicao previdenciaria sobre a receita bruta"', '"desoneracao da folha"'),
        nucleo=("Lei 8.212/1991", "Lei 10.666/2003", "Lei 11.457/2007", "Lei 12.546/2011", "Lei 13.670/2018",
                "Lei 14.973/2024", "Decreto 3.048/1999"),
        apelidos=("contribuicoes previdenciarias", "contribuicao previdenciaria", "inss", "cprb",
                  "desoneracao da folha"),
    ),
    Tema(
        "PROCESSO FISCAL", "Processo administrativo fiscal, CARF, consulta e transação",
        termos=('"processo administrativo fiscal"', "CARF", '"conselho administrativo de recursos fiscais"',
                '"transacao tributaria"', '"transacao resolutiva"', '"processo de consulta"', '"empate" julgamento',
                '"defesa do contribuinte"'),
        nucleo=("Decreto 70.235/1972", "Lei 9.430/1996, arts. 44, 48 a 50 e 73 a 74-A", "Lei 9.784/1999",
                "Lei 10.522/2002", "Lei 11.941/2009, exceto arts. 15 a 24", "Decreto 7.574/2011", "Lei 13.988/2020", "Lei 14.689/2023", "LC 225/2026",
                "LC 227/2026", "LC 236/2026"),
        apelidos=("pat", "processo administrativo tributario", "carf", "contencioso administrativo"),
    ),
    Tema(
        "CTN", "Normas gerais de direito tributário",
        termos=('"codigo tributario nacional"', '"normas gerais de direito tributario"'),
        nucleo=("Lei 5.172/1966, arts. 1 a 82-A e 96 a 218", "LC 104/2001", "LC 118/2005", "LC 225/2026", "LC 236/2026"),
        apelidos=("codigo tributario nacional", "normas gerais"),
    ),
    Tema(
        "ICMS/ISS", "Leis complementares de ICMS e ISS",
        termos=("ICMS", '"imposto sobre servicos de qualquer natureza"', "ISSQN",
                '"circulacao de mercadorias"'),
        nucleo=("LC 24/1975", "DL 406/1968", "LC 87/1996", "LC 116/2003", "LC 157/2016", "LC 160/2017",
                "LC 175/2020", "LC 190/2022", "LC 192/2022", "LC 194/2022"),
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


def localizar_temas(nome: str) -> list[Tema]:
    """Aceita combinações: "IRPJ/CSLL", "IRPJ e CSLL", "PIS/COFINS e IPI"."""
    t = localizar_tema(nome)
    if t:
        return [t]
    partes = [p for p in re.split(r"\s*(?:/|,|\+|\be\b)\s*", normalizar(nome)) if p]
    achados = []
    for p in partes:
        tp = localizar_tema(p)
        if not tp:
            return []
        if tp not in achados:
            achados.append(tp)
    return achados


def escopo_de(dispositivos: str | None) -> Callable[[str], bool] | None:
    """Converte "arts. 1 a 60, exceto art. 44" num filtro de artigos (chave "44", "24c")."""
    if not dispositivos:
        return None
    n = normalizar(dispositivos)
    incluir, excluir = n, ""
    if "exceto" in n:
        incluir, excluir = n.split("exceto", 1)

    def faixas(texto: str) -> list[tuple[tuple[int, str], tuple[int, str]]]:
        out = []
        texto = re.sub(r"arts?\.?", " ", texto)
        for parte in re.split(r",|;|\be\b", texto):
            m = re.match(r"\s*(\d+)(?:-?([a-z]))?[º°o]?\s*(?:a|ao|ate)\s+(\d+)(?:-?([a-z]))?", parte)
            if m:
                out.append(((int(m.group(1)), m.group(2) or ""), (int(m.group(3)), m.group(4) or "zz")))
                continue
            m = re.match(r"\s*(\d+)(?:-?([a-z]))?", parte)
            if m:
                ini = (int(m.group(1)), m.group(2) or "")
                out.append((ini, (ini[0], m.group(2) or "zz")))
        return out

    inc, exc = faixas(incluir), faixas(excluir)

    def ordem(art: str) -> tuple[int, str]:
        m = re.match(r"(\d+)([a-z]*)", art or "")
        return (int(m.group(1)), m.group(2)) if m else (0, "")

    def dentro(art: str) -> bool:
        o = ordem(art)
        ok = any(a <= o <= b for a, b in inc) if inc else True
        return ok and not any(a <= o <= b for a, b in exc)

    return dentro
