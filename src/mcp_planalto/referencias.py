"""Tipos de norma e interpretação de citações ("Lei 9.430/96", "LC 214/2025", "RIR/2018")."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class TipoNorma:
    sigla: str  # código interno (= sigla do Senado)
    nome: str
    urn: str  # tipo na URN LexML
    feminino: bool = True


TIPOS: dict[str, TipoNorma] = {
    t.sigla: t
    for t in [
        TipoNorma("CF", "Constituição Federal", "constituicao"),
        TipoNorma("EMC", "Emenda Constitucional", "emenda.constitucional"),
        TipoNorma("LCP", "Lei Complementar", "lei.complementar"),
        TipoNorma("LEI", "Lei", "lei"),
        TipoNorma("LDL", "Lei Delegada", "lei.delegada"),
        TipoNorma("MPV", "Medida Provisória", "medida.provisoria"),
        TipoNorma("DEL", "Decreto-Lei", "decreto.lei", feminino=False),
        TipoNorma("DEC", "Decreto", "decreto", feminino=False),
    ]
}

# Ordem importa: padrões mais específicos primeiro.
_PADROES_TIPO: list[tuple[str, str]] = [
    (r"emenda\s+constitucional|\bemc\b|\bec\b", "EMC"),
    (r"lei\s+complementar|\blcp?\b", "LCP"),
    (r"lei\s+delegada|\bldl\b", "LDL"),
    (r"medida\s+provis[oó]ria|\bmpv?\b", "MPV"),
    (r"decreto[\s-]*lei|\bdl\b|\bdel\b", "DEL"),
    (r"decreto|\bdec\b|\bd\b", "DEC"),
    (r"\blei\b|\bl\b", "LEI"),
    (r"constitui[cç][aã]o|\bcf\b|\bcrfb\b", "CF"),
]

# Apelidos usuais na prática tributária.
APELIDOS: dict[str, tuple[str, str, int]] = {
    "cf": ("CF", "1988", 1988),
    "cf/88": ("CF", "1988", 1988),
    "crfb": ("CF", "1988", 1988),
    "constituicao": ("CF", "1988", 1988),
    "constituicao federal": ("CF", "1988", 1988),
    "ctn": ("LEI", "5172", 1966),
    "codigo tributario nacional": ("LEI", "5172", 1966),
    "rir": ("DEC", "9580", 2018),
    "rir/2018": ("DEC", "9580", 2018),
    "rir/18": ("DEC", "9580", 2018),
    "regulamento do imposto de renda": ("DEC", "9580", 2018),
    "rir/99": ("DEC", "3000", 1999),
    "rir/1999": ("DEC", "3000", 1999),
    "ripi": ("DEC", "7212", 2010),
    "regulamento do ipi": ("DEC", "7212", 2010),
    "riof": ("DEC", "6306", 2007),
    "regulamento do iof": ("DEC", "6306", 2007),
    "regulamento aduaneiro": ("DEC", "6759", 2009),
    "lei das s.a.": ("LEI", "6404", 1976),
    "lei das sa": ("LEI", "6404", 1976),
    "lsa": ("LEI", "6404", 1976),
    "codigo civil": ("LEI", "10406", 2002),
    "cc": ("LEI", "10406", 2002),
    "cpc": ("LEI", "13105", 2015),
    "codigo de processo civil": ("LEI", "13105", 2015),
    "clt": ("DEL", "5452", 1943),
    "lef": ("LEI", "6830", 1980),
    "lei de execucao fiscal": ("LEI", "6830", 1980),
    "pat": ("DEC", "70235", 1972),
    "processo administrativo fiscal": ("DEC", "70235", 1972),
    "simples nacional": ("LCP", "123", 2006),
    "estatuto da microempresa": ("LCP", "123", 2006),
    "lei do bem": ("LEI", "11196", 2005),
    "lei kandir": ("LCP", "87", 1996),
    "lei do iss": ("LCP", "116", 2003),
    "lei do ibs": ("LCP", "214", 2025),
    "lei do ibs/cbs": ("LCP", "214", 2025),
    "lgpd": ("LEI", "13709", 2018),
    "lei de recuperacao judicial": ("LEI", "11101", 2005),
}


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalizar(s: str) -> str:
    return re.sub(r"\s+", " ", sem_acento(s).lower()).strip()


def ano_completo(ano: int) -> int:
    if ano >= 100:
        return ano
    corrente = dt.date.today().year % 100
    return 2000 + ano if ano <= corrente else 1900 + ano


def formatar_numero(numero: str) -> str:
    """9430 -> 9.430 ; 2158-35 -> 2.158-35"""
    base, _, reed = numero.partition("-")
    if base.isdigit() and len(base) > 3:
        base = f"{int(base):,}".replace(",", ".")
    return f"{base}-{reed}" if reed else base


@dataclass(frozen=True)
class Referencia:
    tipo: str
    numero: str  # sem pontos; MP reeditada: "2158-35"
    ano: int | None = None

    @property
    def chave(self) -> str:
        return f"{self.tipo}:{self.numero}:{self.ano or ''}"

    @property
    def nome(self) -> str:
        t = TIPOS[self.tipo]
        if self.tipo == "CF":
            return "Constituição Federal de 1988"
        s = f"{t.nome} nº {formatar_numero(self.numero)}"
        return f"{s}/{self.ano}" if self.ano else s

    def com_ano(self, ano: int) -> "Referencia":
        return Referencia(self.tipo, self.numero, ano)

    @staticmethod
    def de_chave(chave: str) -> "Referencia":
        tipo, numero, ano = chave.split(":")
        return Referencia(tipo, numero, int(ano) if ano else None)


_RE_NUM = re.compile(
    r"(?:n[º°o.]*\s*)?(?P<num>\d{1,3}(?:\.\d{3})+|\d+)(?P<reed>-[a-z]|-\d+)?"
    r"(?:\s*(?:/|,?\s*de\s+(?:\d{1,2}(?:º|°)?\s+de\s+[a-zç]+\s+de\s+|\d{1,2}[./-]\d{1,2}[./-])?|\s+)(?P<ano>\d{4}|\d{2})\b)?",
    re.I,
)


def interpretar(texto: str) -> Referencia:
    """Converte uma citação em `Referencia`. Levanta ValueError se não reconhecer."""
    bruto = texto.strip()
    n = normalizar(bruto).rstrip(".")
    if n in APELIDOS:
        t, num, ano = APELIDOS[n]
        return Referencia(t, num, ano)
    # chave interna "LEI:9430:1996"
    m = re.fullmatch(r"([A-Za-z]{2,3}):([\d-]+):(\d{4})?", bruto)
    if m and m.group(1).upper() in TIPOS:
        return Referencia(m.group(1).upper(), m.group(2), int(m.group(3)) if m.group(3) else None)
    # nome de arquivo do Planalto: l9430.htm, lcp123.htm, del1598.htm, d9580.htm, mpv1303.htm
    m = re.fullmatch(r"(lcp|del|mpv|emc|l|d)(\d+)(?:compilad[oa]|cons)?(?:\.html?)?", n)
    if m:
        t = {"lcp": "LCP", "del": "DEL", "mpv": "MPV", "emc": "EMC", "l": "LEI", "d": "DEC"}[m.group(1)]
        return Referencia(t, str(int(m.group(2))))

    tipo = None
    resto = n
    for padrao, sigla in _PADROES_TIPO:
        mt = re.search(padrao, n)
        if mt:
            tipo = sigla
            resto = n[mt.end():]
            break
    if tipo == "CF":
        return Referencia("CF", "1988", 1988)
    if tipo is None:
        raise ValueError(
            f"Não reconheci o tipo de norma em {texto!r}. Ex.: 'Lei 9.430/1996', 'LC 214/2025', "
            "'Decreto 9.580/2018', 'MP 2.158-35/2001', 'DL 1.598/1977', 'EC 132/2023'."
        )
    m = _RE_NUM.search(resto)
    if not m:
        raise ValueError(f"Não encontrei o número da norma em {texto!r}.")
    numero = m.group("num").replace(".", "").lstrip("0") or "0"
    if m.group("reed") and m.group("reed")[1:].isdigit():
        numero += m.group("reed")
    ano = ano_completo(int(m.group("ano"))) if m.group("ano") else None
    return Referencia(tipo, numero, ano)


def urn_lexml(ref: Referencia, data_assinatura: str | None) -> str | None:
    """data_assinatura no formato AAAA-MM-DD."""
    if not data_assinatura:
        return None
    t = TIPOS[ref.tipo]
    if ref.tipo == "CF":
        return "urn:lex:br:federal:constituicao:1988-10-05;1988"
    return f"urn:lex:br:federal:{t.urn}:{data_assinatura};{ref.numero}"


# ------------------------------------------------------------------ dispositivos

_ROMANOS = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def romano_para_int(s: str) -> int | None:
    s = s.lower()
    if not s or any(c not in _ROMANOS for c in s):
        return None
    total = 0
    for i, c in enumerate(s):
        v = _ROMANOS[c]
        total += -v if i + 1 < len(s) and _ROMANOS[s[i + 1]] > v else v
    return total


def int_para_romano(n: int) -> str:
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
            (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, r in vals:
        while n >= v:
            out += r
            n -= v
    return out


def chave_dispositivo(texto: str) -> tuple:
    """Normaliza uma referência a dispositivo para comparação.

    "Art. 42, § 3, Inciso 2" (Senado) e "art. 42, § 3º, II" (usuário) produzem
    a mesma chave: (('art','42'),('par','3'),('inc','2')).
    """
    n = normalizar(texto).replace("º", "").replace("°", "")
    n = re.sub(r"\[.*?\]", "", n)  # "[Lei nº 9.430 de 27/12/1996]" no Senado
    m = re.search(r"\bart(?:igo)?s?\.?\s*(\d{1,3}(?:\.\d{3})+|\d+)(?:\s*-\s*([a-z])\b|([a-z])\b)?", n)
    if not m:
        return ()
    partes: list[tuple[str, str]] = [("art", m.group(1).replace(".", "") + (m.group(2) or m.group(3) or ""))]
    for tok in re.split(r"[,;]", n[m.end():]):
        tok = tok.strip().strip(".").strip()
        if not tok or tok == "caput" or tok.startswith("caput"):
            tok = tok[5:].strip() if tok.startswith("caput") else ""
            if not tok:
                continue
        if re.fullmatch(r"(?:paragrafo|par\.?)\s+unico", tok):
            partes.append(("par", "unico"))
        elif mm := re.fullmatch(r"(?:§+|paragrafo)\s*(\d+)(?:\s*-\s*([a-z])|([a-z]))?", tok):
            partes.append(("par", mm.group(1) + (mm.group(2) or mm.group(3) or "")))
        elif mm := re.fullmatch(r"(?:inciso\s+)?(\d+|[ivxlc]+)(?:\s*-\s*([a-z]))?", tok):
            if any(k in ("inc", "ali") for k, _ in partes) and not tok.startswith("inciso"):
                # depois de inciso/alínea, número solto é item
                partes.append(("item", mm.group(1)))
                continue
            v = mm.group(1)
            num = v if v.isdigit() else str(romano_para_int(v) or v)
            partes.append(("inc", num + (mm.group(2) or "")))
        elif mm := re.fullmatch(r"(?:alinea\s+)?[\"']?([a-z])[\"']?\)?", tok):
            partes.append(("ali", mm.group(1)))
        elif mm := re.fullmatch(r"item\s+(\d+)", tok):
            partes.append(("item", mm.group(1)))
    return tuple(partes)


def dispositivo_contem(pai: tuple, filho: tuple) -> bool:
    """True se `filho` é o próprio `pai` ou está dentro dele (art. 2 contém art. 2, § 1º)."""
    return bool(pai) and filho[: len(pai)] == pai


def rotulo_dispositivo(chave: tuple) -> str:
    out = []

    def _num(v: str) -> str:
        m = re.fullmatch(r"(\d+)([a-z]?)", v)
        if not m:
            return v
        base = m.group(1) + ("º" if int(m.group(1)) < 10 else "")
        return f"{base}-{m.group(2).upper()}" if m.group(2) else base

    for k, v in chave:
        if k == "art":
            out.append(f"art. {_num(v)}")
        elif k == "par":
            out.append("parágrafo único" if v == "unico" else f"§ {_num(v)}")
        elif k == "inc":
            out.append(int_para_romano(int(v)) if v.isdigit() else v.upper())
        elif k == "ali":
            out.append(f'alínea "{v}"')
        elif k == "item":
            out.append(f"item {v}")
    return ", ".join(out)
