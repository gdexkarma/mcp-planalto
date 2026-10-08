"""Tipos de norma e interpretação de citações ("Lei 9.430/96", "LC 214/2025", "RIR/2018")."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field


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

# Padrões de tipo. Na citação vale o tipo que aparece PRIMEIRO no texto e que é seguido de número;
# em empate de posição, o padrão mais longo ("lei complementar" vence "lei").
_PADROES_TIPO: list[tuple[str, str]] = [
    (r"emenda\s+constitucional|emenda|\bemc\b|\bec\b", "EMC"),
    (r"lei\s+complementar(\s+federal)?|lei\s+compl\.?|\blcp?\b|\bl\.\s?c\.?", "LCP"),
    (r"lei\s+delegada|\bldl\b", "LDL"),
    (r"medida\s+provisoria|\bmpv?\b|\bm\.\s?p\.?", "MPV"),
    (r"decreto[\s-]*lei|\bdec\.?\s*-?\s*lei|\bdl\b|\bdel\b|\bd\.\s?l\.?", "DEL"),
    (r"decreto(\s+federal)?|\bdecr?\b\.?|\bd\.", "DEC"),
    (r"\blei(\s+federal|\s+ordinaria)?\b|\bl\.", "LEI"),
]
_RE_CF = re.compile(r"constituicao(\s+(federal|da republica)[\w\s]*)?|\bcf\b|\bcrfb\b")
# Atos que não são normas federais tratadas aqui. Recusados quando aparecem ANTES da norma encontrada
# ("Portaria MF 343 e Lei 9.430" -> recusa) ou são o próprio tipo ("Decreto Legislativo nº 2"); depois
# dela são só contexto ("Lei 9.430/96 e Portaria MF 343/2015" -> Lei 9.430).
_BLOQUEIO = re.compile(
    r"\b(projeto|pl|plp|pec|plv|portaria|instrucao normativa|in (?:rfb|srf|sn|cgsn|conjunta)|resolucao|"
    r"ato declaratorio|ade|solucao de (?:consulta|divergencia)|sc (?:cosit|disit)|parecer normativo|pn|"
    r"decreto legislativo|ato complementar|lei constitucional|emenda constitucional de revisao|ecr|"
    r"decreto do conselho|sumula|acordao|convenio|deliberacao|lei organica|adi|adc|adpf)\b"
)
# Qualificadores colados à norma: "Lei Estadual 6.374", "Constituição do Estado de São Paulo".
_QUALIFICADOR = re.compile(r"[^,;()]{0,25}?\b(estadual|municipal|distrital|do estado|do municipio|do distrito)\b")
_NUMERO_APOS = r"\s*(?:n\.?\s*[º°o]?s?\.?\s*)?(?=\d)"

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
    "lei das s.a": ("LEI", "6404", 1976),
    "lei das s/a": ("LEI", "6404", 1976),
    "lei das sa": ("LEI", "6404", 1976),
    "lei das sociedades anonimas": ("LEI", "6404", 1976),
    "lsa": ("LEI", "6404", 1976),
    "codigo civil": ("LEI", "10406", 2002),
    "cc": ("LEI", "10406", 2002),
    "cc/2002": ("LEI", "10406", 2002),
    "cpc": ("LEI", "13105", 2015),
    "cpc/2015": ("LEI", "13105", 2015),
    "cpc/15": ("LEI", "13105", 2015),
    "cpc/73": ("LEI", "5869", 1973),
    "codigo penal": ("DEL", "2848", 1940),
    "cdc": ("LEI", "8078", 1990),
    "codigo de defesa do consumidor": ("LEI", "8078", 1990),
    "lindb": ("DEL", "4657", 1942),
    "lrf": ("LCP", "101", 2000),
    "lei de responsabilidade fiscal": ("LCP", "101", 2000),
    "rps": ("DEC", "3048", 1999),
    "regulamento da previdencia social": ("DEC", "3048", 1999),
    "rir/94": ("DEC", "1041", 1994),
    "rir/1994": ("DEC", "1041", 1994),
    "codigo civil de 1916": ("LEI", "3071", 1916),
    "cf/1988": ("CF", "1988", 1988),
    "adct": ("CF", "1988", 1988),
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
    "lei anticorrupcao": ("LEI", "12846", 2013),
    "marco civil da internet": ("LEI", "12965", 2014),
    "marco civil": ("LEI", "12965", 2014),
    "lei de falencias": ("LEI", "11101", 2005),
    "lei de licitacoes": ("LEI", "14133", 2021),
    "nova lei de licitacoes": ("LEI", "14133", 2021),
    "lei do simples": ("LCP", "123", 2006),
    "lei de improbidade": ("LEI", "8429", 1992),
    "lei de improbidade administrativa": ("LEI", "8429", 1992),
    "lei do mandado de seguranca": ("LEI", "12016", 2009),
    "lei de introducao as normas do direito brasileiro": ("DEL", "4657", 1942),
    "codigo de processo penal": ("DEL", "3689", 1941),
    "cpp": ("DEL", "3689", 1941),
    "lei da transacao": ("LEI", "13988", 2020),
    "lei das estatais": ("LEI", "13303", 2016),
    "lei de lavagem de dinheiro": ("LEI", "9613", 1998),
    "lei dos crimes contra a ordem tributaria": ("LEI", "8137", 1990),
    "lei de crimes contra a ordem tributaria": ("LEI", "8137", 1990),
    "lei dos juizados especiais federais": ("LEI", "10259", 2001),
    "cc/02": ("LEI", "10406", 2002),
    "lei de recuperacao judicial": ("LEI", "11101", 2005),
}


def sem_acento(s: str) -> str:
    # NFKC antes: "Ｌｅｉ ９.４３０" (largura total) vira "Lei 9.430"; º e ª são preservados
    s = unicodedata.normalize("NFKC", s.replace("º", "\x00o").replace("ª", "\x00a"))
    s = s.replace("\x00o", "º").replace("\x00a", "ª")
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
    numero: str  # sem pontos; MP reeditada: "2158-35"; com letra: "4235-B"
    ano: int | None = None
    ano_ambiguo: bool = field(default=False, compare=False)  # citado com 2 dígitos ("/24")

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

    def com_ano(self, ano: int | None) -> "Referencia":
        return Referencia(self.tipo, self.numero, ano)

    @staticmethod
    def de_chave(chave: str) -> "Referencia":
        tipo, numero, ano = chave.split(":")
        return Referencia(tipo, numero, int(ano) if ano else None)


_RE_NUM = re.compile(
    r"(?:n\.?\s*[º°o]?s?\.?\s*)?(?P<num>\d{1,3}(?:\.\d{3})+|\d+)(?P<reed>-[a-z](?![a-z])|-\d+)?"
    r"(?:\s*(?:/|,?\s*de\s+(?:\d{1,2}(?:º|°)?\s+de\s+[a-zç]+\s+de\s+|\d{1,2}[./-]\d{1,2}[./-])?|\s+)"
    # ano: "1996", "96", "1.996"; nunca o dia de "de 27 de dezembro"
    r"(?P<ano>[12]\.\d{3}|\d{4}|\d{2})(?!\d)(?!\s*[º°]?\s+de\s+[a-z]))?",
)


def _apelidos_regex() -> re.Pattern:
    chaves = sorted(APELIDOS, key=len, reverse=True)
    return re.compile("|".join(r"(?<![\w/])" + re.escape(k) + r"(?![\w/])" for k in chaves))


_RE_APELIDOS: re.Pattern | None = None


def _localizar(n: str) -> tuple[int, int, str, Referencia | None] | None:
    """Primeira norma citada em `n` (normalizado): (início, fim, tipo, ref pronta se for apelido)."""
    global _RE_APELIDOS
    if _RE_APELIDOS is None:
        _RE_APELIDOS = _apelidos_regex()
    candidatos = []
    for padrao, sigla in _PADROES_TIPO:
        for mt in re.finditer(f"(?:{padrao}){_NUMERO_APOS}", n):
            candidatos.append((mt.start(), -(mt.end() - mt.start()), mt.end(), sigla, None))
    for ma in _RE_APELIDOS.finditer(n):
        t, num, ano = APELIDOS[ma.group(0)]
        candidatos.append((ma.start(), -(ma.end() - ma.start()), ma.end(), t, Referencia(t, num, ano)))
    mc = _RE_CF.search(n)
    if mc:
        candidatos.append((mc.start(), -(mc.end() - mc.start()), mc.end(), "CF", Referencia("CF", "1988", 1988)))
    if not candidatos:
        return None
    ini, _neg, fim, tipo, ref = min(candidatos, key=lambda c: (c[0], c[1]))
    return ini, fim, tipo, ref


def _preparar(texto: str) -> str:
    n = normalizar(texto)
    n = re.sub(r"\blei-complementar\b", "lei complementar", n)
    n = re.sub(r"(?<=[a-z])-(?=\d)", " ", n)  # "lc-214" -> "lc 214"
    n = re.sub(r"(?<=[a-z])(?=\d)", " ", n)  # "lei9430" -> "lei 9430"
    n = re.sub(r"\s*/\s*", "/", n)  # "9.430 / 96" -> "9.430/96"
    # espaço como separador de milhar logo após o tipo: "lei 9 430/96" -> "lei 9430/96"
    n = re.sub(r"(\b(?:lei|decreto|lc|lcp|mp|mpv|dl|del|ec|n[º°o]?\.?)\s+\d{1,3}) (\d{3})\b", r"\1\2", n)
    return n


def interpretar_citacao(texto: str) -> tuple[Referencia, str | None]:
    """Como `interpretar`, mas também devolve o dispositivo citado junto ("art. 74 da Lei 9.430/96")."""
    bruto = texto.strip()
    if not bruto:
        raise ValueError("Informe a norma (ex.: 'Lei 9.430/1996').")
    # chave interna "LEI:9430:1996" ou "LEI:5467-A:1968"
    m = re.fullmatch(r"([A-Za-z]{2,3}):([\dA-Za-z-]+):(\d{4})?", bruto)
    if m and m.group(1).upper() in TIPOS:
        return Referencia(m.group(1).upper(), m.group(2).upper(), int(m.group(3)) if m.group(3) else None), None
    n = _preparar(bruto)
    # nome de arquivo do Planalto: l9430.htm, lcp123.htm, del1598.htm, d9580.htm, mpv1303.htm
    m = re.fullmatch(r"(lcp|del|mpv|emc|l|d) ?(\d+)(?:compilad[oa]|cons)?(?:\.html?)?", n)
    if m and (m.group(0).endswith((".htm", ".html")) or " " not in m.group(0)):
        t = {"lcp": "LCP", "del": "DEL", "mpv": "MPV", "emc": "EMC", "l": "LEI", "d": "DEC"}[m.group(1)]
        return Referencia(t, str(int(m.group(2)))), None
    if n in APELIDOS or n.rstrip(".") in APELIDOS:
        t, num, ano = APELIDOS[n if n in APELIDOS else n.rstrip(".")]
        return Referencia(t, num, ano), ("ADCT" if n.startswith("adct") else None)

    achado = _localizar(n)
    bloqueio = _BLOQUEIO.search(n)
    qualif = _QUALIFICADOR.match(n, achado[0]) if achado else _QUALIFICADOR.search(n)
    if bloqueio and (not achado or bloqueio.start() <= achado[0]):
        motivo = bloqueio.group(0)
    elif qualif:
        motivo = qualif.group(1)
    else:
        motivo = None
    if motivo:
        raise ValueError(
            f"'{motivo}' não é norma federal coberta por este servidor (leis, LCs, decretos, "
            "decretos-leis, MPs, emendas e a Constituição de 1988)."
        )
    if not achado:
        raise ValueError(
            f"Não reconheci a norma em {texto!r}. Ex.: 'Lei 9.430/1996', 'LC 214/2025', "
            "'Decreto 9.580/2018', 'MP 2.158-35/2001', 'DL 1.598/1977', 'EC 132/2023', 'CTN'."
        )
    ini, fim, tipo, ref = achado
    antes = n[:ini].strip(" ,;-")
    if ref is None:
        m = _RE_NUM.match(n, fim)
        numero = m.group("num").replace(".", "").lstrip("0") or "0"
        reed = m.group("reed")
        bruto_ano = m.group("ano")
        if reed and tipo != "MPV" and reed[1:].isdigit() and not bruto_ano and len(reed) in (3, 5):
            bruto_ano, reed = reed[1:], None  # "Lei 9.430-96": o sufixo é o ano
        if reed:
            numero += reed.upper() if reed[1:].isalpha() else reed
        ano, ambiguo = None, False
        if bruto_ano:
            bruto_ano = bruto_ano.replace(".", "")
            ano = ano_completo(int(bruto_ano))
            ambiguo = len(bruto_ano) == 2
        ref = Referencia(tipo, numero, ano, ambiguo)
        depois = n[m.end():]
    else:
        depois = n[fim:]
        if tipo == "CF" and re.match(r"\s*(/|de\s+)(19[0-7]\d|[0-7]\d)\b", depois):
            raise ValueError("Só a Constituição de 1988 é coberta (constituições anteriores não).")
        depois = re.sub(r"^\s*(/|de\s+)(1988|88)\b", "", depois)
    # dispositivo citado antes ("art. 74 da Lei...") ou depois ("Lei 9.430/96, art. 74")
    disp = None
    m_antes = re.match(r"(.*?(?:\b(?:arts?|artigos?|paragrafos?|incisos?|alineas?|caput)\b|§).*?)\s*,?\s*"
                       r"(d[aoe]s?|n[ao]s?)?\s*$", antes)
    m_depois = re.match(r"\s*(?:\([^)]*\)\s*)?[(:\-–,]?\s*(?:em seu\s+|no\s+)?(?=arts?\b|artigos?\b|§)", depois)
    if m_antes and re.search(r"\bart|§|paragrafo|inciso|alinea", antes):
        disp = m_antes.group(1).strip(" ,")
    elif m_depois:
        disp = re.split(r"[;]|\s+(?:c/c|e da|e do)\s+", depois[m_depois.end():])[0]
        disp = disp.replace(")", " ").replace("(", " ").strip(" ,.;:")
        disp = re.sub(r"\s+", " ", disp)
    if disp and "adct" in n[ini:fim + 6]:
        disp = f"{disp} do ADCT"
    return ref, disp


def interpretar(texto: str) -> Referencia:
    """Converte uma citação em `Referencia`. Levanta ValueError se não reconhecer."""
    return interpretar_citacao(texto)[0]


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
    a mesma chave: (('art','42'),('par','3'),('inc','2')). "art. 22, caput" termina com ('caput','').
    """
    n = normalizar(texto).replace("º", "").replace("°", "")
    n = re.sub(r"\[.*?\]", "", n)  # "[Lei nº 9.430 de 27/12/1996]" no Senado
    m = re.search(r"\bart(?:igo)?s?\.?\s*(\d{1,3}(?:\.\d{3})+|\d+)(?:\s*-\s*([a-z])\b|([a-z])\b)?", n)
    if not m:
        return ()
    partes: list[tuple[str, str]] = [("art", m.group(1).replace(".", "") + (m.group(2) or m.group(3) or ""))]
    caput_final = False
    for tok in re.split(r"[,;]", n[m.end():]):
        tok = tok.strip().strip(".").strip()
        caput_final = False
        if tok.startswith("caput"):
            tok = tok[5:].strip()
            if not tok:
                caput_final = True
                continue
        if not tok:
            continue
        ja_inc = any(k in ("inc", "par") for k, _ in partes)
        if re.fullmatch(r"(?:paragrafo|par\.?|§)\s*unico", tok):
            partes.append(("par", "unico"))
        elif mm := re.fullmatch(r"(?:§+|paragrafo)\s*(\d+)(?:\s*-\s*([a-z])|([a-z]))?", tok):
            partes.append(("par", mm.group(1) + (mm.group(2) or mm.group(3) or "")))
        elif (mm := re.fullmatch(r"(?:alinea\s+)?[\"'“”]?([a-z]{1,2})[\"'“”]?\)?", tok)) and \
                (tok.startswith("alinea") or (ja_inc and any(k == "inc" for k, _ in partes)) or
                 not re.fullmatch(r"[ivxlc]+", mm.group(1)) or mm.group(1) in ("c", "l")):
            # letra depois de inciso é alínea ("art. 1, I, c"), mesmo que pareça romano
            partes.append(("ali", mm.group(1)))
        elif mm := re.fullmatch(r"(?:inciso\s+|inc\.?\s*)?(\d+|[ivxlc]+)(?:\s*-\s*([a-z]))?", tok):
            if any(k in ("inc", "ali") for k, _ in partes) and not re.match(r"inc", tok):
                partes.append(("item", mm.group(1)))  # depois de inciso/alínea, número solto é item
                continue
            v = mm.group(1)
            num = v if v.isdigit() else str(romano_para_int(v) or v)
            partes.append(("inc", num + (mm.group(2) or "")))
        elif mm := re.fullmatch(r"item\s+(\d+)", tok):
            partes.append(("item", mm.group(1)))
    if caput_final and len(partes) == 1:
        partes.append(("caput", ""))
    return tuple(partes)


def dispositivo_casa(alvo: tuple, k: tuple) -> bool:
    """`k` (dispositivo de uma alteração) está no escopo de `alvo` (pedido do usuário), ou o contém."""
    if not alvo or not k:
        return False
    if alvo[-1][0] == "caput":
        base = alvo[:-1]
        return k[: len(base)] == base and not any(x[0] == "par" for x in k)
    return dispositivo_contem(alvo, k) or dispositivo_contem(k, alvo)


def dispositivo_contem(pai: tuple, filho: tuple) -> bool:
    """True se `filho` é o próprio `pai` ou está dentro dele (art. 2 contém art. 2, § 1º)."""
    return bool(pai) and filho[: len(pai)] == pai


def rotulo_dispositivo(chave: tuple) -> str:
    out = []

    def _num(v: str) -> str:
        m = re.fullmatch(r"(\d+)([a-z]?)", v)
        if not m:
            return v
        num = int(m.group(1))
        base = (f"{num:,}".replace(",", ".") if num >= 1000 else str(num)) + ("º" if num < 10 else "")
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
        elif k == "caput":
            out.append("caput")
    return ", ".join(out)
