"""Cliente da API de Dados Abertos do Senado Federal (legislação federal).

Fornece o que o Planalto não tem em forma estruturada:
- indexação temática (tesauro do Senado) e catalogação;
- "vides": quais normas posteriores alteraram/revogaram/regulamentaram cada dispositivo;
- "edivs": quais normas anteriores esta norma alterou.
Documentação: https://legis.senado.leg.br/dadosabertos/v3/api-docs
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import quote

from lxml import etree

from ..db import Norma, Relacao
from ..http import ClienteHTTP, ErroHTTP
from ..referencias import Referencia, interpretar

log = logging.getLogger(__name__)

BASE = "https://legis.senado.leg.br/dadosabertos/legislacao"
ID_CF = "579494"  # código da Constituição de 1988 na base do Senado (a URL CON/1988/1988 não funciona)
ID_ADCT = "604119"  # o ADCT é um documento separado

# Sigla do Senado (antes do hífen) -> tipo interno
_TIPO_SENADO = {
    "LEI": "LEI", "LCP": "LCP", "DEC": "DEC", "DEL": "DEL", "MPV": "MPV",
    "EMC": "EMC", "LDL": "LDL", "CON": "CF",
}


def _data_br(s: str | None) -> str | None:
    if not s:
        return None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", s)
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


def _txt(el, caminho: str) -> str:
    v = el.findtext(caminho)
    return re.sub(r"\s+", " ", v).strip() if v else ""


def _frases(el, caminho: str) -> str:
    frases = [re.sub(r"\s+", " ", f.text).strip(" .") for f in el.findall(caminho) if f.text and f.text.strip()]
    return " ; ".join(frases)


def ref_de_nome(nome: str) -> Referencia | None:
    """"Medida Provisória nº 1.563-1 de 30/01/1997" -> Referencia(MPV, 1563-1, 1997)."""
    if not nome:
        return None
    if re.match(r"\s*(Constitui|Ato das Disposi[çc][õo]es Constitucionais Transit)", nome, re.I):
        anos = re.findall(r"(1[89]\d\d|20\d\d)", nome)
        return Referencia("CF", "1988", 1988) if not anos or "1988" in anos else None
    if not re.search(r"n[º°o]\s*[\d.]+", nome):
        return None  # norma não numerada (ex.: "Decreto de 12/03/2001")
    try:
        r = interpretar(nome)
    except ValueError:
        return None
    if r.tipo not in ("LEI", "LCP", "DEC", "DEL", "MPV", "EMC", "LDL", "CF"):
        return None
    # "Decreto Legislativo", "Resolução" etc. não são tratados
    if re.match(r"\s*(Decreto Legislativo|Resolu|Portaria|Ato|Instru)", nome, re.I):
        return None
    return r


@dataclass
class DetalheSenado:
    norma: Norma
    relacoes_recebidas: list[Relacao] = field(default_factory=list)  # vides: posteriores -> esta
    relacoes_feitas: list[Relacao] = field(default_factory=list)  # edivs: esta -> anteriores
    dispositivos: list[dict] = field(default_factory=list)  # por dispositivo: [{dispositivo, refs:[...]}]


def _situacao(vides: list[etree._Element]) -> str | None:
    """Situação da norma a partir das declarações do Senado.

    Declarações permanentes prevalecem. Revogação "provisória" (feita por MP) não torna a norma
    revogada: se a MP caducar, a norma volta a valer; só é informada se nada mais houver.
    """
    permanentes, provisorias = [], []
    for v in vides:
        dec = _txt(v, "comentario").lower()
        nome = _txt(v, "nomeNormaPosterior")
        rotulo = None
        if "revogação" in dec and "no todo" in dec and "retirada" not in dec:
            rotulo = f"Revogada ({nome})"
        elif "conversão em lei" in dec:
            rotulo = f"Convertida em lei ({nome})"
        elif ("perda de eficácia" in dec and "parcial" not in dec) or "caducidade" in dec or "rejeição" in dec:
            rotulo = f"Sem eficácia ({nome})" if nome else "Sem eficácia"
        elif "vigência encerrada" in dec:
            rotulo = f"Vigência encerrada ({nome})" if nome else "Vigência encerrada"
        if not rotulo:
            continue
        (provisorias if "provis" in dec else permanentes).append((_data_br(_txt(v, "datAssinatura")) or "", rotulo))
    if permanentes:
        return max(permanentes)[1]  # a mais recente
    if provisorias:
        data, rotulo = max(provisorias)
        return rotulo.replace("Revogada (", "Revogação provisória por MP, conferir se a MP foi convertida (")
    return None


_RE_DECL_FORTE = re.compile(r"revoga|efic[áa]cia|convers|vig[êe]ncia|caduc|rejei|revig|repristin", re.I)


def _sem_vides_vazias_duplicadas(vides: list[etree._Element]) -> list[etree._Element]:
    """O Senado às vezes repete a relação com a mesma norma posterior: uma vide com itens (ex.: só
    "Ressalva") e outra genérica, sem itens ("Declaração de Alteração Permanente"). A genérica faria
    a ressalva parecer alteração; descarta-a quando a mesma norma tem outra vide com itens."""
    com_itens = {_txt(v, "codnormaposterior") or _txt(v, "nomeNormaPosterior")
                 for v in vides if v.findall("itens/item")}
    return [v for v in vides
            if v.findall("itens/item")
            or (_txt(v, "codnormaposterior") or _txt(v, "nomeNormaPosterior")) not in com_itens
            or _RE_DECL_FORTE.search(_txt(v, "comentario"))]


def _itens_vide(v) -> list[tuple[str, str]]:
    its = [(_txt(i, "comentario"), _txt(i, "dispositivo")) for i in v.findall("itens/item")]
    return its or [("", "")]


def _relacoes_de_vides(vides, destino: str, destino_id: str | None, data_destino: str | None,
                       prefixo: str = "") -> list[Relacao]:
    out = []
    for v in vides:
        origem = ref_de_nome(_txt(v, "nomeNormaPosterior"))
        if not origem:
            continue
        data_o = _data_br(_txt(v, "datAssinatura"))
        if data_o and data_destino and data_o < data_destino and "revig" not in _txt(v, "comentario").lower():
            data_o = None  # erro de cadastro do Senado (ex.: "Decreto 7.212 de 08/03/1879")
        if data_o:
            origem = origem.com_ano(int(data_o[:4]))
        for acao, disp in _itens_vide(v):
            if prefixo:
                disp = f"{prefixo}, {disp}" if disp else prefixo
            out.append(Relacao(
                origem=origem.chave, destino=destino, declaracao=_txt(v, "comentario"), acao=acao,
                dispositivo=disp, data=data_o, origem_senado_id=_txt(v, "codnormaposterior") or None,
                destino_senado_id=destino_id,
            ))
    return out


def ler_vides_adct(xml: bytes, destino: str) -> list[Relacao]:
    """Vides do documento do ADCT no Senado, como relações recebidas pela CF ("ADCT, Art. 76")."""
    d = etree.fromstring(xml).find(".//documento")
    if d is None:
        return []
    return _relacoes_de_vides(_sem_vides_vazias_duplicadas(d.findall("vides/vide")), destino, ID_CF,
                              "1988-10-05", prefixo="ADCT")


def ler_detalhe(xml: bytes, numero: str | None = None) -> DetalheSenado | None:
    """`numero`: quando a resposta traz várias edições (MPs reeditadas), escolhe a pedida ("2158-35")."""
    raiz = etree.fromstring(xml)
    docs = raiz.findall(".//documento")
    if not docs:
        return None
    d = docs[0]
    if numero and len(docs) > 1:
        for cand in docs:
            r = ref_de_nome(_txt(cand, "identificacao/normaNome"))
            if r and r.numero == numero:
                d = cand
                break
    ident = d.find("identificacao")
    tipo_s = _txt(ident, "tipo").split("-")[0]
    tipo = _TIPO_SENADO.get(tipo_s)
    nome = _txt(ident, "normaNome")
    ref = ref_de_nome(nome)
    if tipo is None or ref is None:
        return None
    data = _data_br(_txt(ident, "dataassinatura"))
    ref = Referencia(tipo, ref.numero, int(data[:4]) if data else ref.ano)
    apelido = _txt(ident, "apelido")
    # "LEI-9430-1996-12-27 , Lei do Ajuste Tributário (1996)" -> "Lei do Ajuste Tributário (1996)"
    apelido = ", ".join(p.strip() for p in apelido.split(",")[1:]).strip() or None
    pubs = [_txt(p, "dispositivo") for p in d.findall("publicacoes/publicacao")]
    vides = _sem_vides_vazias_duplicadas(d.findall("vides/vide"))
    urn = None
    if m := re.search(r"urn=([^&\s]+)", _txt(ident, "urlDocumento")):
        urn = m.group(1)
    norma = Norma(
        chave=ref.chave, tipo=ref.tipo, numero=ref.numero, ano=ref.ano, data=data,
        ementa=_txt(d, "ementa") or None, apelido=apelido, situacao=_situacao(vides),
        senado_id=d.get("id"), urn=urn, indexacao=_frases(d, "indexacao/frase") or None,
        catalogo=_frases(d, "catalogo/frase") or None, observacao=_txt(d, "observacao") or None,
        publicacao=" | ".join(p for p in pubs if p) or None, origem="senado",
    )
    det = DetalheSenado(norma)

    det.relacoes_recebidas = _relacoes_de_vides(vides, ref.chave, norma.senado_id, data)
    for v in d.findall("edivs/ediv"):
        destino = ref_de_nome(_txt(v, "nomeNormaAnterior"))
        if not destino:
            continue
        data_d = _data_br(_txt(v, "datAssinatura"))
        if data_d:
            destino = destino.com_ano(int(data_d[:4]))
        adct = _txt(v, "nomeNormaAnterior").lower().startswith("ato das disp")
        for acao, disp in _itens_vide(v):
            if adct:
                disp = f"ADCT, {disp}" if disp else "ADCT"
            det.relacoes_feitas.append(Relacao(
                origem=ref.chave, destino=destino.chave, declaracao=_txt(v, "comentario"), acao=acao,
                dispositivo=disp, data=data, origem_senado_id=norma.senado_id,
                destino_senado_id=_txt(v, "codnormaAnterior") or None,
            ))
    for disp in d.findall("disps/disp"):
        nome_d = re.sub(r"\s*\[.*\]$", "", _txt(disp, "nomeDispositivo"))
        refs = [
            {"norma": _txt(r, "dispositivo"), "acao": _txt(r, "comentario")}
            for r in disp.findall("refs/ref")
        ]
        det.dispositivos.append({"dispositivo": nome_d, "refs": refs})
    return det


def ler_lista(xml: bytes) -> list[Norma]:
    raiz = etree.fromstring(xml)
    out = []
    for d in raiz.iter("documento"):
        tipo = _TIPO_SENADO.get(_txt(d, "tipo").split("-")[0])
        ref = ref_de_nome(_txt(d, "normaNome"))
        if not tipo or not ref:
            continue
        data = _data_br(_txt(d, "dataassinatura"))
        ref = Referencia(tipo, ref.numero, int(data[:4]) if data else ref.ano)
        apelido = ", ".join(p.strip() for p in _txt(d, "apelido").split(",")[1:]).strip() or None
        out.append(Norma(
            chave=ref.chave, tipo=tipo, numero=ref.numero, ano=ref.ano, data=data,
            ementa=_txt(d, "ementa") or None, apelido=apelido, senado_id=d.get("id"), origem="senado",
        ))
    return out


class Senado:
    def __init__(self, http: ClienteHTTP):
        self.http = http

    def _get(self, url: str, max_idade: float | None) -> bytes | None:
        try:
            return self.http.get(url, max_idade=max_idade, aceitar="application/xml").corpo
        except ErroHTTP as e:
            if e.status == 404:
                return None
            raise

    def detalhe(self, ref: Referencia, max_idade: float | None = 24 * 3600) -> DetalheSenado | None:
        if ref.tipo == "CF":
            det = self.detalhe_por_id(ID_CF, max_idade)
            if det:
                # o ADCT é um documento à parte no Senado; suas alterações entram como "ADCT, Art. N"
                try:
                    xml = self._get(f"{BASE}/{ID_ADCT}", max_idade)
                    if xml and b"<documento" in xml:
                        det.relacoes_recebidas += ler_vides_adct(xml, det.norma.chave)
                except ErroHTTP as e:
                    log.warning("ADCT indisponível no Senado: %s", e)
                    det.norma.observacao = ((det.norma.observacao or "") +
                                            " [Alterações do ADCT indisponíveis nesta consulta.]").strip()
            return det
        if not ref.ano:
            raise ValueError("O Senado exige o ano da norma.")
        # MPs reeditadas: a API só aceita o número-base e devolve todas as edições do ano
        base = ref.numero.split("-")[0] if ref.tipo == "MPV" and re.fullmatch(r"\d+-\d+", ref.numero) else ref.numero
        url = f"{BASE}/{ref.tipo}/{quote(base)}/{ref.ano}"
        xml = self._get(url, max_idade)
        if not xml or b"<documento" not in xml:
            self.http.esquecer(url)  # norma ainda não indexada: não guardar a resposta vazia
            return None
        return ler_detalhe(xml, ref.numero)

    def detalhe_por_id(self, codigo: str, max_idade: float | None = 24 * 3600) -> DetalheSenado | None:
        xml = self._get(f"{BASE}/{quote(codigo)}", max_idade)
        if not xml or b"<documento" not in xml:
            return None
        return ler_detalhe(xml)

    def lista(self, tipo: str, ano: int, max_idade: float | None = 24 * 3600) -> list[Norma]:
        sigla = {"CF": "CON"}.get(tipo, tipo)
        xml = self._get(f"{BASE}/lista?tipo={sigla}&ano={ano}", max_idade)
        return ler_lista(xml) if xml else []
