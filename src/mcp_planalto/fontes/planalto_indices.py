"""Leitura dos quadros (índices) de legislação do Planalto.

Os quadros listam, por tipo e ano, cada norma com o link para o texto e a ementa.
São a forma mais confiável de descobrir a URL exata do texto compilado, porque
o padrão de caminhos do Planalto muda conforme a época.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
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
_RE_DATA = re.compile(r"(\d{1,2})[º°]?\s*[./]{1,2}\s*(\d{1,2})\s*[./]{1,2}\s*(\d{4}|\d{2})(?!\d)")
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
    reedicoes: list[str] = field(default_factory=list)  # MPs anteriores a 2001: edições da família


def _limpo(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _data(texto: str, ano_max: int | None = None) -> str | None:
    """Data "27.12.96" ou "27 de dezembro de 1996". Anos de 2 dígitos usam o intervalo do quadro
    (num quadro de 1901-1929, "20" é 1920, não 2020)."""
    m = _RE_DATA.search(texto)
    if m:
        d, mth, bruto = int(m.group(1)), int(m.group(2)), m.group(3)
        a = ano_completo(int(bruto))
        if len(bruto) == 2 and ano_max and a > ano_max:
            a -= 100
    else:
        m = _RE_DATA_EXTENSO.search(texto)
        if not m or m.group(2).lower() not in _MESES:
            return None
        d, mth, a = int(m.group(1)), _MESES[m.group(2).lower()], int(m.group(3))
    try:
        return dt.date(a, mth, d).isoformat()
    except ValueError:
        return None


def _ano_de_data_invalida(texto: str, ano_max: int | None) -> int | None:
    """Data malformada no quadro ("31.4.69"): o dia não existe, mas o ano é aproveitável."""
    m = _RE_DATA.search(texto)
    if not m:
        return None
    bruto = m.group(3)
    a = ano_completo(int(bruto))
    if len(bruto) == 2 and ano_max and a > ano_max:
        a -= 100
    return a


def _numero_do_arquivo(url: str | None) -> int | None:
    if not url:
        return None
    nome = url.rsplit("/", 1)[-1]
    m = re.search(r"(\d{1,3}(?:\.\d{3})+(?=\D)|\d+)", nome)  # "L10.978.htm" usa ponto de milhar
    return int(m.group(1).replace(".", "")) if m else None


_RE_LIXO_EMENTA = re.compile(
    r"\s*(Mensagem de [Vv]eto( (total|parcial))?|Vide texto compilado|Texto compilado|\((Vide|Ver)\b[^)]*\)|"
    r"Regulamento|Vig[êe]ncia)\s*(?=$|\s(Mensagem|Vide|Texto|\())"
)
_RE_SITUACAO_MP = re.compile(
    r"(?:(?<=[.;)])\s*|\s+(?=Convertid[ao]\s+(?:na\s+|em\s+)?Lei\b))"
    r"((Em Tramita[çc][ãa]o|Convertid[ao]|Revogad[ao]|Rejeitad[ao]|Sem efic[áa]cia|Prejudicad[ao]|"
    r"Perdeu|Vig[êe]ncia encerrada|Encerrad[ao])\b.*)$",
    re.I,
)


def _links_texto(celula, base_url: str) -> list[str]:
    out = []
    for a in celula.iter("a"):
        h = (a.get("href") or "").strip()
        if not h or h.startswith(("#", "mailto:", "javascript:")):
            continue
        hl = h.lower()
        if re.search(r"\.(pdf|doc|docx)$", hl) or "/msg/" in hl or "mensagem" in hl or "/exm/" in hl:
            continue
        if re.search(r"\.html?(#.*)?$", hl):
            u = urljoin(base_url, h.split("#")[0])
            if u not in out:
                out.append(u)
    return out


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


def ler_quadro(conteudo: str, tipo: str, base_url: str, ano_ini: int | None = None,
               ano_fim: int | None = None) -> list[EntradaIndice]:
    """Extrai as normas de um quadro do Planalto (ano_ini/ano_fim: anos cobertos pelo quadro)."""
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
        # Primeira célula: "Lei nº 15.321, de 31.12.2025 Publicada no DOU..." ou "9.430, de 27.12.96".
        # Há números quebrados por tags: "8.2 53 , de 31 .10.91".
        partes = re.split(r"public", primeira, maxsplit=1, flags=re.I)
        cab = re.sub(r"(?<=[\d.])\s+(?=[\d.])", "", partes[0])
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
        data = _data(cab, ano_fim)
        ano = int(data[:4]) if data else _ano_de_data_invalida(cab, ano_fim)
        if ano is None and len(partes) > 1:
            # sem data de assinatura legível: usa o ano da publicação no DOU, se couber no quadro
            pub = _data(partes[1], ano_fim)
            if pub and (not ano_ini or int(pub[:4]) >= ano_ini) and (not ano_fim or int(pub[:4]) <= ano_fim):
                ano = int(pub[:4])
        if ano is None and ano_ini and ano_ini == ano_fim:
            ano = ano_ini
        base_num = int(re.match(r"\d+", numero).group(0))
        links = _links_texto(celulas[0], base_url) + _links_texto(celulas[1], base_url)
        # entre os links da linha, o que traz o número da norma no nome do arquivo
        url = next((u for u in links if _numero_do_arquivo(u) == base_num), None)
        if not url and links:
            n_arq = _numero_do_arquivo(links[0])
            if n_arq is None:
                url = links[0]
            else:
                log.info("Quadro %s: link de %s %s aponta para outro número (%s); link descartado", base_url, tipo,
                         numero, links[0])
        for a in list(celulas[1].iter("a")):  # links de mensagem de veto/exposição de motivos/compilado
            h = (a.get("href") or "").lower()
            if re.search(r"/msg/|vep|mensagem|/exm/|\.pdf$|compilad", h) or \
                    re.match(r"\s*(mensagem|vide|texto compilado)", a.text_content(), re.I):
                a.drop_tree()
        ementa = _limpo(celulas[1].text_content())
        for _ in range(3):
            ementa = _RE_LIXO_EMENTA.sub("", ementa).strip()
        situacao = _limpo(celulas[2].text_content()) if len(celulas) > 2 else None
        if tipo == "MPV" and (ms := _RE_SITUACAO_MP.search(ementa)):
            ementa = ementa[: ms.start()].strip()
            if not situacao or re.match(r"Origin[áa]ria", situacao):
                situacao = (situacao + " | " if situacao else "") + ms.group(1)
        reedicoes: list[str] = []
        if tipo == "MPV" and len(celulas) > 2 and situacao and re.match(r"Origin[áa]ria", situacao):
            # "Originária: 1.636 Edições: 1.636-1, ..., 2.189-48" (quadros anteriores à EC 32/2001).
            # Só os links contam: o texto pode citar "Del nº 2.474, 1988 - Transformado em MPV nº 2".
            for a in celulas[2].iter("a"):
                txt = _limpo(a.text_content()).strip(" ,;.")
                if re.fullmatch(r"\d{1,3}(?:\.\d{3})*(?:-\d+)?", txt):
                    num = txt.replace(".", "").lstrip("0")
                    if num and num != numero and num not in reedicoes:
                        reedicoes.append(num)
            final = situacao.rsplit(" | ", 1)[-1] if " | " in situacao else ""
            situacao = f"Última reedição de família com {len(reedicoes)} edições anteriores" + (f"; {final}" if final else "")
        chave = f"{numero}:{ano}"
        if chave in vistos:
            log.info("Quadro %s: linha repetida %s %s descartada", base_url, tipo, chave)
            continue
        vistos.add(chave)
        saida.append(EntradaIndice(tipo, numero, ano, data, url, ementa, situacao or None, reedicoes))
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
    rx = r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)"
    anos_rot = [int(a) for a in re.findall(rx, rotulo)]
    if "anterior" in (rotulo + url).lower():
        anos = anos_rot or [int(a) for a in re.findall(rx, url)]
        return (None, max(anos) if anos else None)
    if anos_rot:
        return (min(anos_rot), max(anos_rot))
    # URL: a pasta do ano ("/_ato2019-2022/2021/lei/") é mais específica que a do mandato
    m = re.search(r"/(1[89]\d\d|20\d\d)/", url)
    if m:
        return (int(m.group(1)), int(m.group(1)))
    anos = [int(a) for a in re.findall(rx, url)]
    if re.search(r"_?(leis|decretos|quadro)[-_]?(\d{4})\.htm", url, re.I) and anos:
        return (anos[-1], anos[-1])
    return (min(anos), max(anos)) if anos else (None, None)


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

    def ler(self, url: str, tipo: str, max_idade: float | None = 0, ano_ini: int | None = None,
            ano_fim: int | None = None) -> list[EntradaIndice]:
        r = self.http.get(url, max_idade=max_idade)
        return ler_quadro(r.texto(), tipo, r.url, ano_ini, ano_fim)

    def entradas(self, tipo: str, ano_inicio: int | None = None, ano_fim: int | None = None,
                 max_idade: float | None = 0, falhas: list[str] | None = None) -> list[EntradaIndice]:
        """`falhas`, se informada, recebe as URLs dos quadros que não puderam ser lidos."""
        out: list[EntradaIndice] = []
        for rot, url, a0, a1 in self.quadros(tipo):
            if ano_inicio and a1 and a1 < ano_inicio:
                continue
            if ano_fim and a0 and a0 > ano_fim:
                continue
            try:
                out.extend(self.ler(url, tipo, max_idade=max_idade, ano_ini=a0, ano_fim=a1))
            except ErroHTTP as e:
                log.warning("Quadro indisponível (%s): %s", rot, e)
                if falhas is not None:
                    falhas.append(url)
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
                for e in self.ler(url, tipo, max_idade=6 * 3600, ano_ini=_a0, ano_fim=_a1):
                    # Linha sem ano só serve quando o pedido também não tem ano: nunca se imputa o ano
                    # pedido a uma linha (isso criava normas inexistentes, como "Lei 41/1950").
                    if e.numero == numero and (e.ano == ano or ano is None):
                        return e
            except ErroHTTP as e:
                log.warning("Quadro indisponível: %s", e)
        return None
