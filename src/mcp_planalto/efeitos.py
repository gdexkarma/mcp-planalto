"""Datas de vigência e produção de efeitos escritas por extenso nas cláusulas de vigência.

Ex.: "a partir de 1º de janeiro de 2027", "a partir do primeiro dia do quarto mês subsequente ao de sua
publicação", "após decorridos noventa dias de sua publicação oficial", "na data de sua publicação".
"""

from __future__ import annotations

import calendar
import datetime as dt
import re

from .referencias import normalizar

MESES = {m: i for i, m in enumerate(
    ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
     "novembro", "dezembro"], 1)}

_UNIDADES = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
             "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "quatorze": 14, "catorze": 14,
             "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20,
             "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70, "oitenta": 80,
             "noventa": 90, "cem": 100, "cento": 100, "duzentos": 200, "trezentos": 300, "quatrocentos": 400}
_ORDINAIS = {"primeiro": 1, "segundo": 2, "terceiro": 3, "quarto": 4, "quinto": 5, "sexto": 6, "setimo": 7,
             "oitavo": 8, "nono": 9, "decimo": 10, "decimo primeiro": 11, "decimo segundo": 12,
             "decimo terceiro": 13}


def _numero(texto: str) -> int | None:
    texto = texto.strip()
    if m := re.match(r"(\d+)", texto):
        return int(m.group(1))
    total, achou = 0, False
    for p in re.split(r"\s+e\s+|\s+", texto):
        if p in _UNIDADES:
            total += _UNIDADES[p]
            achou = True
        elif p:
            break
    return total if achou else None


def _somar_meses(d: dt.date, meses: int) -> dt.date:
    a, m = divmod(d.month - 1 + meses, 12)
    ano, mes = d.year + a, m + 1
    return dt.date(ano, mes, min(d.day, calendar.monthrange(ano, mes)[1]))


def data_por_extenso(texto: str) -> dt.date | None:
    """Primeira data escrita "1º de janeiro de 2027" / "01/01/2027" no texto."""
    n = normalizar(texto).replace("º", "").replace("°", "")
    m = re.search(r"\b(\d{1,2})o?\s+de\s+(" + "|".join(MESES) + r")\s+de\s+(\d{4})", n)
    if m:
        try:
            return dt.date(int(m.group(3)), MESES[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", n)
    if m:
        try:
            return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def data_de_publicacao(epigrafe: str | None) -> dt.date | None:
    """ "LEI Nº 15.525, DE 28 DE SETEMBRO DE 2026" -> 2026-09-28."""
    return data_por_extenso(epigrafe or "")


def data_efeitos(texto: str, publicacao: dt.date | None) -> dt.date | None:
    """Data a partir da qual a cláusula manda produzir efeitos (a mais tardia que conseguir calcular)."""
    n = normalizar(texto).replace("º", "").replace("°", "")
    candidatos: list[tuple[int, dt.date]] = []
    m = re.search(r"\b(?:a partir de|desde|em|apos)\s+(\d{1,2})o?\s+de\s+(" + "|".join(MESES) + r")\s+de\s+(\d{4})", n)
    if m:
        try:
            candidatos.append((m.start(), dt.date(int(m.group(3)), MESES[m.group(2)], int(m.group(1)))))
        except ValueError:
            pass
    if publicacao:
        ords = "|".join(sorted(_ORDINAIS, key=len, reverse=True))
        m = re.search(rf"\bprimeiro dia (?:util )?do ({ords}) mes (?:subsequente|seguinte|posterior)", n)
        if m:
            d = _somar_meses(publicacao.replace(day=1), _ORDINAIS[m.group(1)])
            candidatos.append((m.start(), d))
        m = re.search(r"\bprimeiro dia (?:util )?do mes (?:subsequente|seguinte)", n)
        if m:
            candidatos.append((m.start(), _somar_meses(publicacao.replace(day=1), 1)))
        m = re.search(r"\b(?:apos|depois de|decorridos?)\s+(?:decorridos?\s+)?([\w\s]+?)\s*(?:\([^)]*\))?\s*"
                      r"(dias|meses|anos)\b[^.;]{0,40}?\bpublicacao", n)
        if m and (q := _numero(m.group(1))):
            un = m.group(2)
            d = (publicacao + dt.timedelta(days=q) if un == "dias" else
                 _somar_meses(publicacao, q if un == "meses" else 12 * q))
            candidatos.append((m.start(), d))
        m = re.search(r"\b(?:primeiro dia|1o? de janeiro) do (?:ano|exercicio(?: financeiro)?) (?:subsequente|seguinte)", n)
        if m:
            candidatos.append((m.start(), dt.date(publicacao.year + 1, 1, 1)))
        m = re.search(r"\bna data de (?:sua )?publicacao", n)
        if m and not candidatos:  # "entra em vigor na publicação e produz efeitos a partir de...": vale o efeito
            candidatos.append((m.start(), publicacao))
    if not candidatos:
        return None
    # cláusula com várias datas (vigência e efeitos): a mais tardia é a que ainda pode estar no futuro
    return max(d for _p, d in candidatos)
