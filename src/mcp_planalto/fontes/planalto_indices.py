"""Leitura dos quadros (índices) de legislação do Planalto.

Os quadros listam, por tipo e ano, cada norma com o link para o texto e a ementa.
São a forma mais confiável de descobrir a URL exata do texto compilado, porque
o padrão de caminhos do Planalto muda conforme a época.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin

from lxml import html as lhtml

from ..http import ClienteHTTP, ErroHTTP
from ..referencias import ano_completo

log = logging.getLogger(__name__)

BASE = "https://www.planalto.gov.br/ccivil_03/"

# Páginas-raiz que apontam para os quadros anuais.
RAIZES = {
    "LEI": BASE + "leis/_lei-ordinaria.htm",
    "DEC": BASE + "decreto/_dec_ano.htm",
    "MPV": BASE + "mpv/principal.htm",
    "DEL": BASE + "decreto-lei/principal_ano.htm",
}
# Tipos cujo quadro é uma única página.
QUADROS_UNICOS = {
    "LCP": BASE + "leis/lcp/quadro_lcp.htm",
    "EMC": BASE + "constituicao/emendas/emc/quadro_emc.htm",
    "LDL": BASE + "leis/ldl/quadro_ldl.htm",
}
URL_CF = BASE + "constituicao/constituicao.htm"

# Filtro de links das páginas-raiz que levam a quadros do próprio tipo.
_FILTRO_QUADRO = {
    "LEI": re.compile(r"(_leis\d{4}|_quadro|quadro/)", re.I),
    "DEC": re.compile(r"(_decretos\d{4}|_?quadro)", re.I),
    "MPV": re.compile(r"quadro/", re.I),
    "DEL": re.compile(r"_quadro", re.I),
}

_RE_NUMERO = re.compile(
    r"(?:n[º°o]\.?\s*)?(\d{1,3}(?:\.\d{3})+|\d+)(-[A-Z](?![a-z])|-\d+)?", re.I
)
_RE_DATA = re.compile(r"(\d{1,2})[º°]?\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{4}|\d{2})(?!\d)")
_RE_DATA_EXTENSO = re.compile(r"(\d{1,2})[º°]?\s+de\s+([a-zç]+)\s+de\s+(\d{4})", re.I)
_MESES = {m: i for i, m in enumerate(
    ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
     "setembro", "outubro", "novembro", "dezembro"], 1)}
_MESES["marco"] = 3


@dataclass
class EntradaIndice:
    tipo: str
    numero: str
    ano: int | None
    data: str | None  # AAAA-MM-DD
    url: str | None
    ementa: str
    situacao: str | None = None


def _limpo(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _data(texto: str) -> str | None:
    m = _RE_DATA.search(texto)
    if m:
        d, mth, a = int(m.group(1)), int(m.group(2)), ano_completo(int(m.group(3)))
    else:
        m = _RE_DATA_EXTENSO.search(texto)
        if not m or m.group(2).lower() not in _MESES:
            return None
        d, mth, a = int(m.group(1)), _MESES[m.group(2).lower()], int(m.group(3))
    try:
        return dt.date(a, mth, d).isoformat()
    except ValueError:
        return None


def _link_texto(celula, base_url: str) -> str | None:
    for a in celula.iter("a"):
        h = (a.get("href") or "").strip()
        if not h or h.startswith(("#", "mailto:", "javascript:")):
            continue
        hl = h.lower()
        if re.search(r"\.(pdf|doc|docx)$", hl) or "/msg/" in hl or "mensagem" in hl or "/exm/" in hl:
            continue
        if re.search(r"\.html?(#.*)?$", hl):
            return urljoin(base_url, h.split("#")[0])
    return None


def ler_quadro(conteudo: str, tipo: str, base_url: str) -> list[EntradaIndice]:
    """Extrai as normas de um quadro do Planalto."""
    doc = lhtml.fromstring(conteudo)
    saida: list[EntradaIndice] = []
    vistos: set[str] = set()
    for tr in doc.iter("tr"):
        celulas = [c for c in tr if c.tag in ("td", "th")]
        if len(celulas) < 2:
            continue
        primeira = _limpo(celulas[0].text_content())
        if not primeira or primeira.lower().startswith(("nº", "n°", "número", "ano")):
            continue
        # Primeira célula: "Lei nº 15.321, de 31.12.2025 Publicada no DOU..." ou "9.430, de 27.12.96"
        cab = re.split(r"public", primeira, flags=re.I)[0]
        cab_sem_tipo = re.sub(
            r"^(lei complementar|lei delegada|lei|decreto[- ]lei|decreto|medida provis[óo]ria|emenda constitucional)\s*",
            "", cab, flags=re.I,
        )
        m = _RE_NUMERO.match(cab_sem_tipo.strip())
        if not m:
            continue
        numero = m.group(1).replace(".", "").lstrip("0") or "0"
        reed = m.group(2)
        if reed and reed[1:].isdigit():  # reedição de MP (2.158-35)
            numero += reed
        elif reed:  # lei/decreto com letra (raro): 1.234-A
            numero += reed.upper()
        data = _data(cab)
        ano = int(data[:4]) if data else None
        url = _link_texto(celulas[0], base_url) or _link_texto(celulas[1], base_url)
        for a in list(celulas[1].iter("a")):  # links de mensagem de veto/exposição de motivos
            h = (a.get("href") or "").lower()
            if re.search(r"/msg/|vep|mensagem|/exm/|\.pdf$", h) or re.match(r"\s*mensagem", a.text_content(), re.I):
                a.drop_tree()
        ementa = _limpo(celulas[1].text_content())
        situacao = _limpo(celulas[2].text_content()) if len(celulas) > 2 else None
        chave = f"{numero}:{ano}"
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append(EntradaIndice(tipo, numero, ano, data, url, ementa, situacao or None))
    return saida


def links_quadros(conteudo: str, tipo: str, base_url: str) -> list[tuple[str, str]]:
    """Da página-raiz, devolve [(rótulo, url)] dos quadros anuais."""
    doc = lhtml.fromstring(conteudo)
    filtro = _FILTRO_QUADRO[tipo]
    out, vistos = [], set()
    for a in doc.iter("a"):
        h = (a.get("href") or "").strip()
        if not h or h.startswith("http") and "planalto.gov.br/ccivil_03" not in h.lower():
            continue
        if not filtro.search(h) or re.search(r"codigos|projetos|veto|resenha", h, re.I):
            continue
        url = urljoin(base_url, h.split("#")[0])
        if url.lower() == base_url.lower() or url.lower() in vistos:
            continue
        vistos.add(url.lower())
        out.append((_limpo(a.text_content()), url))
    return out


def _ano_do_rotulo(rotulo: str, url: str) -> tuple[int | None, int | None]:
    """Intervalo de anos coberto por um quadro, a partir do rótulo ou da URL."""
    anos = [int(a) for a in re.findall(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)", rotulo + " " + url)]
    if "anterior" in (rotulo + url).lower():
        return (None, max(anos) if anos else None)
    if not anos:
        return (None, None)
    return (min(anos), max(anos))


class IndicesPlanalto:
    def __init__(self, http: ClienteHTTP):
        self.http = http

    def quadros(self, tipo: str) -> list[tuple[str, str, int | None, int | None]]:
        """[(rótulo, url, ano_ini, ano_fim)] dos quadros de um tipo."""
        if tipo in QUADROS_UNICOS:
            return [(tipo, QUADROS_UNICOS[tipo], None, None)]
        if tipo not in RAIZES:
            return []
        r = self.http.get(RAIZES[tipo], max_idade=24 * 3600)
        out = []
        for rot, url in links_quadros(r.texto(), tipo, r.url):
            a0, a1 = _ano_do_rotulo(rot, url)
            out.append((rot, url, a0, a1))
        return out

    def ler(self, url: str, tipo: str, max_idade: float | None = 0) -> list[EntradaIndice]:
        r = self.http.get(url, max_idade=max_idade)
        return ler_quadro(r.texto(), tipo, r.url)

    def entradas(self, tipo: str, ano_inicio: int | None = None, ano_fim: int | None = None,
                 max_idade: float | None = 0) -> list[EntradaIndice]:
        out: list[EntradaIndice] = []
        for rot, url, a0, a1 in self.quadros(tipo):
            if ano_inicio and a1 and a1 < ano_inicio:
                continue
            if ano_fim and a0 and a0 > ano_fim:
                continue
            try:
                out.extend(self.ler(url, tipo, max_idade=max_idade))
            except ErroHTTP as e:
                log.warning("Quadro indisponível (%s): %s", rot, e)
        if ano_inicio or ano_fim:
            out = [e for e in out if e.ano is None or
                   ((not ano_inicio or e.ano >= ano_inicio) and (not ano_fim or e.ano <= ano_fim))]
        return out

    def localizar(self, tipo: str, numero: str, ano: int | None) -> EntradaIndice | None:
        """Procura uma norma nos quadros do ano (ou em todos, se o ano for desconhecido)."""
        if tipo == "CF":
            return EntradaIndice("CF", "1988", 1988, "1988-10-05", URL_CF,
                                 "Constituição da República Federativa do Brasil de 1988.")
        candidatos = self.quadros(tipo)
        if ano:
            candidatos = [q for q in candidatos if (q[2] is None or q[2] <= ano) and (q[3] is None or q[3] >= ano)] or candidatos
        for _rot, url, _a0, _a1 in candidatos:
            try:
                for e in self.ler(url, tipo, max_idade=6 * 3600):
                    if e.numero == numero and (ano is None or e.ano is None or e.ano == ano):
                        return e
            except ErroHTTP as e:
                log.warning("Quadro indisponível: %s", e)
        return None
