"""Camada de serviço: junta catálogo local, Planalto e Senado.

Usada pelo servidor MCP e pela linha de comando.
"""

from __future__ import annotations

import collections
import datetime as dt
import functools
import hashlib
import logging
import math
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable

from .config import Config
from .db import Banco, Norma, Relacao, agora, radical_flexao
from .fontes.planalto_indices import EntradaIndice, IndicesPlanalto
from .fontes.planalto_texto import Documento, citacoes_de_normas, ler_documento
from .fontes.senado import DetalheSenado, Senado
from .http import ClienteHTTP, ErroHTTP
from .referencias import (
    TIPOS,
    Referencia,
    chave_dispositivo,
    dispositivo_casa,
    formatar_numero,
    interpretar,
    interpretar_citacao,
    normalizar,
    rotulo_dispositivo,
)
from .efeitos import data_de_publicacao, data_efeitos
from .temas import Tema, escopo_de, localizar_temas

log = logging.getLogger(__name__)

TIPOS_CATALOGO = ["LEI", "LCP", "DEC", "DEL", "MPV", "EMC", "LDL"]
TIPOS_DETALHE_PADRAO = ["LEI", "LCP", "DEL", "MPV", "EMC", "LDL"]
ALIASES_TIPO = {
    "LEI": "LEI", "L": "LEI", "LO": "LEI", "LC": "LCP", "LCP": "LCP", "DEC": "DEC", "DECRETO": "DEC", "D": "DEC",
    "DL": "DEL", "DEL": "DEL", "DECRETO-LEI": "DEL", "MP": "MPV", "MPV": "MPV", "EC": "EMC", "EMC": "EMC",
    "LDL": "LDL", "CF": "CF",
}
try:
    from zoneinfo import ZoneInfo

    FUSO = ZoneInfo("America/Sao_Paulo")
except Exception:  # pragma: no cover - sem base de fusos
    FUSO = dt.timezone(dt.timedelta(hours=-3))


class Atividade:
    """Contador de sincronizações em curso (várias podem se sobrepor); is_set() como threading.Event."""

    def __init__(self):
        self._n = 0
        self._lock = threading.Lock()

    def __enter__(self):
        with self._lock:
            self._n += 1
        return self

    def __exit__(self, *exc):
        with self._lock:
            self._n -= 1

    def is_set(self) -> bool:
        return self._n > 0


class NormaNaoEncontrada(Exception):
    def __init__(self, mensagem: str, candidatos: list[Norma] | None = None):
        super().__init__(mensagem)
        self.candidatos = candidatos or []


def hoje() -> dt.date:
    return dt.datetime.now(FUSO).date()


def normalizar_tipos(tipos: list[str] | None) -> list[str] | None:
    if not tipos:
        return None
    out = []
    for t in tipos:
        k = ALIASES_TIPO.get(t.strip().upper())
        if not k:
            raise ValueError(f"Tipo de norma desconhecido: {t!r}. Use LEI, LC, DECRETO, DL, MP, EC ou LDL.")
        out.append(k)
    return out


def ler_data(valor: str | None, campo: str = "data") -> str | None:
    """Aceita AAAA-MM-DD, DD/MM/AAAA e DD-MM-AAAA; devolve AAAA-MM-DD ou levanta ValueError."""
    if not valor:
        return None
    v = valor.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m", "%Y"):
        try:
            d = dt.datetime.strptime(v, fmt).date()
            return d.isoformat()
        except ValueError:
            continue
    raise ValueError(f"{campo} inválida: {valor!r}. Use AAAA-MM-DD (ex.: 2026-01-31) ou DD/MM/AAAA.")


# ---------------------------------------------------------------- classificação de relações

def classificar_relacao(r: Relacao) -> str:
    """altera | regulamenta | correlata | ressalva | vetada | outra"""
    a, d = normalizar(r.acao), normalizar(r.declaracao)
    if "vetad" in a or "veto" in a or ("vetad" in d and not a):
        return "vetada"
    if "regulament" in a or "regulament" in d:
        return "regulamenta"
    if "correlata" in d or "citada" in d or "correlato" in a:
        return "correlata"
    if a.startswith(("ressalva", "com novo tratamento")) or "novo tratamento" in d or \
            (not a and "ressalva" in d and "revogacao" not in d):
        return "ressalva"
    if "disposicoes em contrario" in d:
        return "outra"  # "revoga as disposições em contrário": não revoga a norma inteira
    if "conversao" in d and not a:
        return "conversao"
    if "reedicao" in d and not a:
        return "reedicao"  # "Reedição com/sem Alteração" de MP  # "Conversão em Lei com Alteração": a MP virou lei, não foi alterada
    if "revogacao" in d and "no todo" in d:
        return "altera"
    if a.startswith(("alteracao", "acrescimo", "revogacao", "supressao", "renumeracao", "revigora",
                     "restauracao", "repristinacao", "retificacao com sentido", "nova redacao",
                     # o Planalto anota esses como "Redação dada/Revogado pela": mudam o texto
                     "encerramento de vigencia", "vigencia determinada")) and "implicita" not in a:
        return "altera"
    if not a and ("alteracao" in d or "revogacao" in d):
        return "altera"  # declaração de alteração sem itens detalhados
    return "outra"


def _acao_altera_texto(r: Relacao) -> bool:
    return classificar_relacao(r) == "altera"


def _data_http(valor: str | None) -> str | None:
    if not valor:
        return None
    try:
        return parsedate_to_datetime(valor).astimezone(FUSO).date().isoformat()
    except (TypeError, ValueError):
        return None


def _padrao_arquivo(ref: Referencia) -> re.Pattern | None:
    base = ref.numero.split("-")[0]
    prefixo = {"LEI": "l", "LCP": "lcp", "DEC": "d", "DEL": "del", "MPV": "mpv", "EMC": "emc", "LDL": "ldl"}.get(ref.tipo)
    if prefixo is None:
        return None
    return re.compile(rf"^{prefixo}0*{base}(?:[-_][0-9a-z]+)*[a-z]*\.html?$")


_RE_TIPO_ANTES = {
    "LEI": r"\blei(?:s)?(?!\s+complementar|\s+delegada)\b",
    "LCP": r"\bleis?\s+complementar(?:es)?\b",
    "LDL": r"\bleis?\s+delegadas?\b",
    "DEC": r"\bdecretos?(?![\s-]*lei)\b",
    "DEL": r"\bdecretos?[\s-]*leis?\b",
    "MPV": r"\bmedidas?\s+provisorias?\b",
    "EMC": r"\bemendas?\s+constitucion(?:al|ais)\b",
}


# verificar_atualizacao procura centenas de normas no mesmo texto de vários MB: normaliza uma vez só
_normalizado = functools.lru_cache(maxsize=16)(normalizar)


def cita_norma(texto: str, ref: Referencia) -> bool:
    """O texto cita a norma? Reconhece "Lei nº 9.430", "Lei n o 9.430", "Lei 9430", "Leis nºs 9.249 e 9.430",
    sem confundir "Decreto-Lei nº 2.848" com "Lei nº 2.848"."""
    if ref.tipo == "CF":
        return False
    t = _normalizado(texto)
    base = ref.numero.split("-")[0]
    numeros = {formatar_numero(base), base}
    for num in numeros:
        for m in re.finditer(rf"(?<![\d.]){re.escape(num)}(?![\d]|\.\d)", t):
            antes = t[max(0, m.start() - 160):m.start()]
            # palavra de tipo mais próxima antes do número
            melhor = None
            for tipo, rx in _RE_TIPO_ANTES.items():
                for mt in re.finditer(rx, antes):
                    if melhor is None or mt.end() > melhor[0] or (mt.end() == melhor[0] and len(mt.group(0)) > melhor[2]):
                        melhor = (mt.end(), tipo, len(mt.group(0)))
            if melhor and melhor[1] == ref.tipo:
                entre = antes[melhor[0]:]
                # entre o tipo e o número só pode haver "nº", outros números da lista, "de 1995", vírgulas e "e"
                if re.fullmatch(r"[\s\w.,º°/ -]*", entre) and len(entre) < 140 and not re.search(
                        r"\b(lei|decreto|medida|emenda|art|inciso|paragrafo)\b", entre):
                    return True
    return False


@dataclass
class PaginaTexto:
    texto: str
    pagina: int
    total_paginas: int


def paginar(texto: str, max_caracteres: int, pagina: int) -> PaginaTexto:
    max_caracteres = max(500, int(max_caracteres))
    if len(texto) <= max_caracteres:
        return PaginaTexto(texto, 1, 1)
    paginas, atual, tam = [], [], 0
    for linha in texto.split("\n"):
        while len(linha) > max_caracteres:  # linha gigante (tabela): corta
            if atual:
                paginas.append("\n".join(atual))
                atual, tam = [], 0
            paginas.append(linha[:max_caracteres])
            linha = linha[max_caracteres:]
        if tam + len(linha) + 1 > max_caracteres and atual:
            paginas.append("\n".join(atual))
            atual, tam = [], 0
        atual.append(linha)
        tam += len(linha) + 1
    if atual:
        paginas.append("\n".join(atual))
    pagina = max(1, min(int(pagina), len(paginas)))
    return PaginaTexto(paginas[pagina - 1], pagina, len(paginas))


def _decl_forte(r: Relacao) -> bool:
    """Declaração que atinge a norma inteira mesmo sem itens (revogação no todo, suspensão, perda de
    eficácia, revigoração, conversão de MP)."""
    d = normalizar(r.declaracao)
    if "disposicoes em contrario" in d:
        return False
    if any(x in d for x in ("eficacia", "caduc", "rejei", "conversao")):
        return True
    return "no todo" in d and any(x in d for x in ("revogacao", "suspensao", "revigoracao", "repristin"))


def _limpar_relacoes(rels: list[Relacao], chave_outra) -> list[Relacao]:
    """Descarta a relação genérica sem itens quando a mesma norma tem outra relação detalhada: o Senado
    repete a vide ("Declaração de Alteração Permanente" vazia ao lado de uma só com "Ressalva")."""
    detalhadas = {chave_outra(r) for r in rels if r.acao or r.dispositivo}
    return [r for r in rels if r.acao or r.dispositivo or chave_outra(r) not in detalhadas or _decl_forte(r)]


def _e_adct(dispositivo: str | None) -> bool:
    n = normalizar(dispositivo or "")
    return "adct" in n or "disposicoes constitucionais transitorias" in n


_RE_INCLUIDO = re.compile(r"\((?:Inclu[íi]d|Acrescid|Acrescentad)[oa]s?\s+pel[oa]s?\s+([^()]{3,120})\)", re.I)


_RE_VIDE = re.compile(r"\(\s*Vide\b[^()]*(?:\([^()]*\)[^()]*)*\)\s*(?:Vig[êe]ncia|Produ[çc][ãa]o de efeitos?)?", re.I)


def _cita_incorporada(texto: str, ref: Referencia) -> bool:
    """O texto cita `ref` fora de "(Vide Lei X) Vigência": a remissão só avisa de mudança futura."""
    return cita_norma(_RE_VIDE.sub(" ", texto), ref)


def _incluido_por_outra(blocos, ref: Referencia) -> bool:
    """O dispositivo traz "(Incluído pela Lei X)" e X não é `ref`: é homônimo de outro acréscimo."""
    notas = [m.group(1) for b in blocos[:1] for m in _RE_INCLUIDO.finditer(b.completo)]
    return bool(notas) and not any(cita_norma(n, ref) for n in notas)


def _relacao_no_escopo(r: Relacao, esc: Callable[[str], bool] | None, ementa_origem: str | None = None) -> bool:
    """A relação atinge o escopo do tema? Sem escopo, sempre. Com escopo, só se o dispositivo for legível e
    estiver dentro; sem dispositivo, só revogação/perda de eficácia da norma inteira ou regulamentação cuja
    ementa cite um artigo do escopo ("Regulamenta o art. 24-C da Lei ...")."""
    if esc is None:
        return True
    k = chave_dispositivo(r.dispositivo) if r.dispositivo else ()
    if k:
        return esc(k[0][1])
    if _decl_forte(r):
        return True
    if classificar_relacao(r) == "regulamenta" and ementa_origem:
        arts = re.findall(r"\bart(?:igo)?s?\.?\s*(\d+(?:\s*-\s*[a-z])?)", normalizar(ementa_origem))
        return any(esc(re.sub(r"[\s-]", "", a)) for a in arts)
    return False


# siglas que quase nunca aparecem por extenso em ementas e indexação
_SINONIMOS_SIGLA = {
    "irrf": 'IRRF OU "retido na fonte" OU "imposto de renda na fonte" OU "incidente na fonte" OU '
            '"retencao na fonte" "imposto"',
    "irpj": 'IRPJ OU "imposto de renda" "pessoa juridica" OU "imposto sobre a renda" "pessoa juridica" OU '
            '"imposto de renda" "pessoas juridicas" OU "imposto sobre a renda das pessoas juridicas"',
    "irpf": 'IRPF OU "imposto de renda" "pessoa fisica" OU "imposto sobre a renda" "pessoa fisica" OU '
            '"imposto de renda" "pessoas fisicas"',
    "csll": 'CSLL OU "contribuicao social sobre o lucro"',
    "iof": 'IOF OU "imposto sobre operacoes financeiras" OU "imposto sobre operacoes de credito"',
    "ipi": 'IPI OU "imposto sobre produtos industrializados"',
    "cprb": 'CPRB OU "contribuicao previdenciaria sobre a receita bruta"',
}


def _contexto_superior(doc: Documento, blocos: list) -> str | None:
    """Ao ler só um inciso/alínea/parágrafo, a cabeça a que ele se liga (caput do artigo, § ou inciso pai)."""
    chaves = [b.chave for b in blocos if b.chave and not b.obsoleto]
    if not chaves or min(len(k) for k in chaves) < 2:
        return None
    k = min(chaves, key=len)
    partes = []
    for n in range(1, len(k)):
        pai = k[:n]
        b = next((x for x in doc.blocos if x.chave == pai and not x.obsoleto and x.espaco == blocos[0].espaco), None)
        if b and b.vigente:
            partes.append(b.vigente[:400] + ("…" if len(b.vigente) > 400 else ""))
    return " ▸ ".join(partes) or None


def _situacao_exibida(norma: Norma) -> tuple[str | None, str | None]:
    """(situação, aviso). MP anterior à EC 32 "em tramitação" vale por força do art. 2º da emenda; MP recente
    "em tramitação" com o prazo vencido provavelmente caducou ou foi convertida."""
    sit = norma.situacao
    if norma.tipo == "MPV" and "tramit" in normalizar(sit or ""):
        if (norma.data or "9999") < "2001-09-12" or re.match(r"\d+-\d+$", norma.numero):
            return ("Em vigor por força do art. 2º da EC 32/2001 (MP anterior à emenda: vale até ser convertida, "
                    "rejeitada ou revogada)"), None
        if norma.data and norma.data < (hoje() - dt.timedelta(days=130)).isoformat():
            return sit, ("O cadastro diz 'em tramitação', mas o prazo constitucional da MP (até 120 dias, fora o "
                         "recesso) já passou: provavelmente perdeu a eficácia ou foi convertida. Confira no Congresso.")
    return sit, None


def _em_segundo_plano(fn, *args) -> Future:
    """Executa em thread daemon (não segura o encerramento do servidor se o portal travar)."""
    fut: Future = Future()

    def rodar():
        try:
            fut.set_result(fn(*args))
        except BaseException as e:  # noqa: BLE001 - repassado a quem espera o resultado
            fut.set_exception(e)

    threading.Thread(target=rodar, daemon=True, name="senado-fundo").start()
    return fut


_BLOQUEIO_PAGINA = re.compile(rb"request rejected|the requested url was rejected|access denied|acesso negado|"
                              rb"service unavailable", re.I)


def _pagina_invalida(corpo: bytes) -> str | None:
    if len(corpo.strip()) < 300:
        return "página vazia"
    if len(corpo) < 20000 and _BLOQUEIO_PAGINA.search(corpo[:20000]):
        return "página de bloqueio do portal"
    return None


def _data_br(iso: str | None) -> str:
    if not iso:
        return "data não identificada"
    a, m, d = iso.split("-")
    return f"{d}/{m}/{a}"


def _espacos_txt(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _nome_da_epigrafe(epigrafe: str | None) -> str | None:
    try:
        return interpretar(epigrafe or "").nome
    except ValueError:
        return None


def _trecho_da_ancora(doc: Documento, i: int) -> list:
    """Bloco da âncora e seus filhos (alíneas de um inciso, incisos de um caput)."""
    base = doc.blocos[i]
    out = [base]
    for b in doc.blocos[i + 1:i + 60]:
        if b.obsoleto:
            continue
        if base.chave and len(b.chave) > len(base.chave) and b.chave[:len(base.chave)] == base.chave:
            out.append(b)
        elif not b.chave and b.tipo == "texto" and b.artigo == base.artigo and len(out) < 3:
            out.append(b)
        else:
            break
    return out


def _artigos_citados(texto: str, alvo: Documento) -> list[str]:
    """Artigos da própria norma de destino citados na cláusula ("em relação aos arts. 450, 461 e 542")."""
    out = []
    for m in re.finditer(r"\barts?\.\s*((?:\d+[º°o]?(?:-[A-Za-z])?)(?:(?:\s*,\s*|\s+e\s+|\s+a\s+)"
                         r"(?:\d+[º°o]?(?:-[A-Za-z])?))*)(?!\s*(?:,\s*)?d[aoe]s?\s+(?:Lei|Decreto|Medida|Emenda|Constitui))",
                         texto):
        esc = escopo_de("arts. " + m.group(1))
        if esc:
            out += [a for a in alvo.artigos("") if esc(a) and a not in out]
    return out[:60]


def _revogacoes_diferidas(alvo: Documento, arts: list[str], ref: Referencia, artigos_lidos: set | None,
                          pub: dt.date | None, data_padrao: dt.date | None) -> list[dict]:
    """Nos artigos `arts` de `alvo` que revogam ("Ficam revogados…"), os trechos que atingem `ref`
    (e, se dado, um dos `artigos_lidos`)."""
    out = []
    for a in arts:
        bl = [b for b in alvo.blocos if b.artigo == a and b.espaco == "" and not b.obsoleto and b.tipo != "citacao"]
        if not bl or not re.search(r"revogad", normalizar(bl[0].vigente)):
            continue
        data = data_efeitos(bl[0].vigente, pub) or data_padrao
        for i, b in enumerate(bl):
            if not b.chave or not cita_norma(b.vigente, ref):
                continue
            grupo = [b] + [c for c in bl[i + 1:i + 40]
                           if len(c.chave) > len(b.chave) and c.chave[:len(b.chave)] == b.chave]
            texto = _espacos_txt(" ".join(c.vigente for c in grupo))
            if artigos_lidos is not None:
                esc = _escopo_revogado(texto, ref)
                if esc is not None and not any(esc(x) for x in artigos_lidos):
                    continue
            out.append({"tipo": "revogacao_futura", "data": data.isoformat() if data else None,
                        "dispositivo_revogador": f"art. {a}, " + rotulo_dispositivo(b.chave).split(", ", 1)[-1],
                        "trecho": texto[:700]})
    return out


def _escopo_revogado(texto: str, ref: Referencia) -> Callable[[str], bool] | None:
    """Artigos de `ref` atingidos no trecho de revogação; None = a norma inteira (ou não deu para saber)."""
    filtros = []
    for m in re.finditer(r"\barts?\.\s*((?:\d+[º°o]?(?:-[A-Za-z])?)(?:(?:\s*,\s*|\s+e\s+|\s+a\s+)"
                         r"(?:\d+[º°o]?(?:-[A-Za-z])?))*)", texto):
        if esc := escopo_de("arts. " + m.group(1)):
            filtros.append(esc)
    if not filtros:
        return None
    return lambda art: any(f(art) for f in filtros)


def _redacao_anterior(doc: Documento, b) -> str | None:
    """Redação riscada imediatamente anterior do mesmo dispositivo (a que ainda vale até a data de efeitos)."""
    try:
        i = doc.blocos.index(b)
    except ValueError:
        return None
    for x in reversed(doc.blocos[max(0, i - 6):i]):
        if x.chave == b.chave and "~~" in x.completo:
            return _espacos_txt(x.completo.replace("~~", ""))
    if "~~" in b.completo:  # trecho riscado dentro do próprio bloco
        return _espacos_txt(" ".join(re.findall(r"~~(.*?)~~", b.completo)))
    return None


def _familia(chave: str) -> str:
    """MPs reeditadas (pré-EC 32/2001) contam como uma só: "MPV:1537-41:1997" e "MPV:1537:1996" -> "MPV:1537".
    Depois da EC 32 a numeração recomeçou do 1 e não há reedições."""
    m = re.match(r"MPV:(\d+)(?:-\d+)?:(\d{4})$", chave)
    if m and (int(m.group(2)) < 2001 or (int(m.group(2)) == 2001 and int(m.group(1)) >= 1000) or "-" in chave):
        return "MPV:" + m.group(1)
    return chave


class Legislacao:
    def __init__(self, config: Config | None = None):
        self.config = (config or Config()).preparar()
        self.http = ClienteHTTP(self.config.cache_dir / "http", self.config.user_agent)
        self.db = Banco(self.config.db_path)
        self.indices = IndicesPlanalto(self.http)
        self.senado = Senado(self.http)
        self._docs: collections.OrderedDict[str, tuple[float, Documento]] = collections.OrderedDict()
        self._detalhes: collections.OrderedDict[str, tuple[float, DetalheSenado]] = collections.OrderedDict()
        self._travas_url: dict[str, threading.Lock] = {}
        self.ultimas_falhas_recentes: list[str] = []
        self._lock = threading.Lock()
        self._sync_lock = threading.Lock()
        self.sincronizando = Atividade()

    # ================================================================ resolução
    def resolver(self, texto: str) -> Norma:
        """Encontra a norma citada (no catálogo local, no Planalto e no Senado)."""
        ref = interpretar(texto)
        tentativas = [ref]
        if ref.ano_ambiguo and ref.ano:  # "Lei 1.234/24": 2024 ou 1924?
            outro = ref.ano - 100 if ref.ano > 1999 else ref.ano + 100
            if outro <= hoje().year:
                tentativas.append(ref.com_ano(outro))
        erro = None
        for r in tentativas:
            try:
                return self._resolver_ref(r)
            except NormaNaoEncontrada as e:
                erro = erro or e
        if len(tentativas) > 1 and not erro.candidatos:
            raise NormaNaoEncontrada(f"Não encontrei {tentativas[0].nome} nem {tentativas[1].nome}.")
        raise erro

    def _familia_com_texto(self, ref: Referencia) -> Norma | None:
        """MP reeditada antes da EC 32/2001: a última edição da família (que tem texto no Planalto)."""
        if ref.tipo != "MPV" or (ref.ano and ref.ano > 2001):
            return None
        base = ref.numero.split("-")[0]
        if ref.ano == 2001 and base.isdigit() and int(base) < 1000:
            return None  # numeração nova, pós-EC 32
        fam = self.db.familia_mp(ref.numero)
        n = self.db.obter(fam) if fam else None
        return n if n and n.chave != ref.chave else None

    def _resolver_ref(self, ref: Referencia) -> Norma:
        if ref.ano and ref.ano > hoje().year:
            raise NormaNaoEncontrada(f"{ref.nome}: o ano {ref.ano} ainda não chegou.")
        if ref.tipo == "EMC" and ref.ano and ref.ano < 1992:
            raise NormaNaoEncontrada(f"{ref.nome}: emendas anteriores a 1992 são à Constituição de 1967/69, "
                                     "que não é coberta.")
        if not ref.ano and ref.tipo == "MPV" and not self.db.candidatos(ref):
            fam = self._familia_com_texto(ref)
            if fam:
                return fam
        if not ref.ano and ref.tipo != "CF":
            cands = self.db.candidatos(ref)
            exatos = [c for c in cands if c.numero == ref.numero]
            if len(exatos) == 1:
                ref = exatos[0].ref
            elif len(exatos) > 1:
                com_ano = [c for c in exatos if c.ano]
                if len(com_ano) == 1:
                    ref = com_ano[0].ref
                else:
                    raise NormaNaoEncontrada(f"Há mais de uma norma '{ref.nome}'. Informe o ano.", exatos[:10])
            elif cands and ref.tipo == "MPV":
                # "MP 2.158" -> última reedição (2.158-35)
                cands.sort(key=lambda c: int(c.numero.split("-")[1]) if "-" in c.numero else 0)
                ref = cands[-1].ref
            else:
                e = self.indices.localizar(ref.tipo, ref.numero, None)
                if not e:
                    raise NormaNaoEncontrada(f"Não encontrei {ref.nome}. Informe o ano (ex.: {ref.nome}/1996).")
                self._salvar_entradas([e])
                if not e.ano:
                    n = self.db.obter(Referencia(e.tipo, e.numero, None).chave)
                    if n:
                        return n
                ref = Referencia(e.tipo, e.numero, e.ano)
        n = self.db.obter(ref.chave)
        if n and n.url_planalto:
            return n
        # MP intermediária de família reeditada (antes da EC 32/2001): a última edição tem o texto
        fam = self._familia_com_texto(ref)
        if fam:
            return fam
        e = self.indices.localizar(ref.tipo, ref.numero, ref.ano)
        if e:
            self._salvar_entradas([e])
            return self.db.obter(Referencia(e.tipo, e.numero, e.ano).chave)
        if n:
            return n
        det, _aviso = self._buscar_detalhe(ref)
        if det:
            return self.db.obter(det.norma.chave)
        raise NormaNaoEncontrada(f"Não encontrei {ref.nome} no Planalto nem no Senado.")

    def _salvar_entradas(self, entradas: list[EntradaIndice]) -> int:
        normas = []
        reedicoes: dict[str, str] = {}
        for e in entradas:
            ref = Referencia(e.tipo, e.numero, e.ano)
            for r in e.reedicoes:
                reedicoes[r] = ref.chave
            data = e.data
            atual = self.db.obter(ref.chave)
            if atual and atual.detalhe_em and atual.data:
                data = None  # a data do Senado (conferida com a epígrafe) prevalece sobre erros de digitação do quadro
            normas.append(Norma(
                chave=ref.chave, tipo=e.tipo, numero=e.numero, ano=e.ano, data=data, ementa=e.ementa or None,
                url_planalto=e.url, origem="planalto", atualizado_em=agora(),
                situacao=e.situacao if e.tipo == "MPV" and e.situacao and e.situacao != "-" else None,
            ))
        if reedicoes:
            self.db.salvar_reedicoes(reedicoes)
        return self.db.salvar(normas)

    # ================================================================ Senado
    def _buscar_detalhe(self, ref: Referencia, max_idade: float | None = 7 * 24 * 3600,
                        guardar: bool = True) -> tuple[DetalheSenado | None, str | None]:
        """(detalhe, aviso). Se o Senado estiver fora, usa as relações já gravadas no banco."""
        try:
            det = self.senado.detalhe(ref, max_idade=max_idade)
        except ErroHTTP as e:
            log.warning("Senado indisponível para %s: %s", ref.nome, e)
            local = self._detalhe_do_banco(ref.chave)
            if local:
                return local, "Senado indisponível agora; usando os dados de alterações já guardados localmente."
            return None, "Senado indisponível agora; tente novamente em alguns minutos."
        if det:
            self._gravar_detalhe(det, guardar)
            return det, None
        local = self._detalhe_do_banco(ref.chave)
        if local and (local.relacoes_recebidas or local.relacoes_feitas):
            return local, ("O Senado não devolveu o registro desta norma agora; usando os dados de alterações já "
                           "guardados localmente.")
        return None, "O Senado não tem registro desta norma (ainda não indexada ou não numerada)."

    def _detalhe_do_banco(self, chave: str) -> DetalheSenado | None:
        n = self.db.obter(chave)
        rec, fei = self.db.relacoes(destino=chave), self.db.relacoes(origem=chave)
        if not n or not (rec or fei or n.detalhe_em):
            return None
        return DetalheSenado(n, _limpar_relacoes(rec, lambda r: r.origem),
                             _limpar_relacoes(fei, lambda r: r.destino), [])

    def _gravar_detalhe(self, det: DetalheSenado, guardar: bool = True) -> None:
        det.norma.detalhe_em = agora()
        # A ementa do Planalto prevalece (é a do texto publicado); o Senado completa o resto.
        atual = self.db.obter(det.norma.chave)
        if atual and atual.ementa:
            det.norma.ementa = None
        substituir = ["situacao"] if det.norma.tipo != "MPV" or det.norma.situacao else []
        self.db.salvar([det.norma], substituir=substituir)
        self.db.salvar_relacoes(
            det.relacoes_recebidas + det.relacoes_feitas,
            substituir_de=[("destino", det.norma.chave), ("origem", det.norma.chave)],
        )
        if guardar:
            with self._lock:
                self._detalhes[det.norma.chave] = (time.time(), det)
                self._detalhes.move_to_end(det.norma.chave)
                while len(self._detalhes) > 64:
                    self._detalhes.popitem(last=False)

    def obter_detalhe(self, norma: Norma, max_idade: float = 7 * 24 * 3600) -> tuple[DetalheSenado | None, str | None]:
        with self._lock:
            em_cache = self._detalhes.get(norma.chave)
        if em_cache and time.time() - em_cache[0] < max_idade:
            return em_cache[1], None
        if not norma.ano and norma.tipo != "CF":
            return None, "Sem o ano da norma não é possível consultar o Senado."
        return self._buscar_detalhe(norma.ref, max_idade=max_idade)

    def detalhe(self, norma: Norma, max_idade: float = 7 * 24 * 3600) -> DetalheSenado | None:
        return self.obter_detalhe(norma, max_idade)[0]

    # ================================================================ texto
    def _carregar(self, url: str, max_idade: float, forcar: bool = False) -> Documento:
        """Baixa (ou revalida) e interpreta uma página do Planalto, com cache em memória e uma trava por URL
        (leituras simultâneas da mesma norma não processam a página várias vezes). Página vazia, de
        bloqueio ou sem nenhum artigo não entra no cache: é baixada de novo uma vez e, persistindo, vira erro."""
        url = url.split("#")[0]
        with self._lock:
            trava = self._travas_url.setdefault(url, threading.Lock())
        with trava:
            with self._lock:
                em_cache = self._docs.get(url)
                if not forcar and em_cache and time.time() - em_cache[0] < max_idade:
                    self._docs.move_to_end(url)
                    return em_cache[1]
            for tentativa in range(2):
                r = self.http.get(url, max_idade=0 if (forcar or tentativa) else max_idade)
                assinatura = hashlib.sha256(r.corpo).hexdigest()
                if em_cache and getattr(em_cache[1], "_assinatura", None) == assinatura:
                    doc = em_cache[1]  # revalidada e igual: não reprocessa (LC 214 leva segundos)
                    break
                motivo = _pagina_invalida(r.corpo)
                doc = None
                if not motivo:
                    try:
                        doc = ler_documento(r.texto(), r.url, r.last_modified)
                    except Exception as e:  # lxml: "Document is empty" etc.
                        motivo = f"página ilegível ({type(e).__name__})"
                    else:
                        if not doc.epigrafe and not any(b.tipo == "dispositivo" for b in doc.blocos):
                            motivo = "página sem epígrafe nem artigos"
                if not motivo:
                    break
                self.http.esquecer(url)
                log.warning("Planalto devolveu página inválida para %s: %s", url, motivo)
                if tentativa:
                    raise ErroHTTP(url, None, f"O Planalto devolveu uma página inválida ({motivo}); tente de novo "
                                              "em alguns minutos")
            doc._assinatura = assinatura
            doc.aviso_rede = r.falha
            with self._lock:
                self._docs[url] = (time.time(), doc)
                self._docs.move_to_end(url)
                while len(self._docs) > 12:
                    self._docs.popitem(last=False)
            return doc

    def _doc_por_url(self, url: str) -> Documento:
        """Documento de outra página do Planalto (destino de link), com o mesmo cache de documentos."""
        return self._carregar(url, self.config.cache_horas * 3600)

    def documento(self, norma: Norma, forcar: bool = False) -> Documento:
        if not norma.url_planalto:
            raise NormaNaoEncontrada(
                f"{norma.nome} não tem texto disponível no Planalto (o quadro do Planalto não traz o link, "
                "ou o link aponta para outra norma).")
        return self._carregar(norma.url_planalto, self.config.cache_horas * 3600, forcar)

    def texto(self, referencia: str, dispositivo: str | None = None, termo: str | None = None,
              modo: str = "vigente", notas: bool = True, omitir_revogados: bool = False,
              estrutura: bool = False, pagina: int = 1, max_caracteres: int = 40000) -> dict:
        if modo not in ("vigente", "historico"):
            raise ValueError("modo deve ser 'vigente' ou 'historico'.")
        dispositivo = self._dispositivo_pedido(referencia, dispositivo)
        norma = self.resolver(referencia)
        doc = self.documento(norma)
        cab = {
            "norma": norma.nome,
            **({"aviso_rede": doc.aviso_rede} if doc.aviso_rede else {}),
            "epigrafe": doc.epigrafe or None,
            "ementa": norma.ementa or doc.ementa,
            "url": doc.url,
            "planalto_atualizado_em": _data_http(doc.last_modified),
        }
        sit, aviso_sit = _situacao_exibida(norma)
        if sit:
            cab["situacao"] = sit
        if aviso_sit:
            cab["aviso_situacao"] = aviso_sit
        if ed := self._aviso_edicao(referencia, norma):
            cab["aviso_edicao"] = ed
        espacos = [e for e in doc.espacos() if e]
        if "reg" in espacos:
            cab["observacao_numeracao"] = ("Esta norma aprova um regulamento anexo: 'art. N' se refere ao regulamento; "
                                           "use 'decreto, art. N' para os artigos do próprio decreto.")
        if "adct" in espacos:
            cab["observacao_numeracao"] = "Para o ADCT, use 'art. N do ADCT'."
        if doc.anexos():
            cab["anexos"] = doc.anexos()
        if estrutura:
            cab["estrutura"] = doc.estrutura()
            cab["total_artigos"] = len(doc.artigos())
            return cab
        if doc.tudo_riscado() and modo == "vigente":
            cab["aviso"] = ("Todo o texto desta norma está riscado no Planalto (revogada ou sem eficácia). "
                            "Use modo='historico' para ler o texto original.")
            if "situacao" not in cab:
                try:
                    det, _ = self.obter_detalhe(norma)
                    if det and det.norma.situacao:
                        cab["situacao"] = det.norma.situacao
                except Exception as e:  # a situação é complemento; o aviso já basta
                    log.info("Situação de %s indisponível: %s", norma.nome, e)
            return cab
        blocos = None
        if dispositivo:
            blocos = doc.selecionar(dispositivo)
            if not blocos:
                cab["aviso"] = f"Dispositivo '{dispositivo}' não encontrado. Use estrutura_norma para ver o sumário."
                return cab
            if ctx := _contexto_superior(doc, blocos):
                cab["contexto"] = ctx
        if termo:
            base = blocos if blocos is not None else doc.blocos
            sub = Documento(doc.url, doc.epigrafe, doc.ementa, doc.notas_gerais, base)
            blocos, n_arts = sub.buscar(termo)
            cab["artigos_com_o_termo"] = n_arts
            achados = []
            for b in blocos:
                rot = (f"anexo {b.espaco.split(':', 1)[1]}" if b.espaco.startswith("anexo:")
                       else rotulo_dispositivo((("art", b.artigo),)) + (" do ADCT" if b.espaco == "adct" else "")
                       if b.artigo else None)
                if rot and rot not in achados:
                    achados.append(rot)
            if achados:
                cab["artigos_encontrados"] = achados
            if n_arts > 15:
                cab["observacao"] = f"Mostrando os 15 primeiros de {n_arts} artigos com o termo."
            if not blocos:
                cab["aviso"] = f"O termo '{termo}' não aparece no texto vigente."
                return cab
        if blocos is None and doc.notas_gerais:
            cab["notas_gerais"] = doc.notas_gerais
        elif dispositivo and doc.notas_gerais:
            # "(Vide Lei X) Vigência", "produção de efeitos": valem também para o dispositivo lido
            ano_min = hoje().year - 3
            vig = []
            for n in doc.notas_gerais:
                # só as remissões a normas recentes; rótulos de link soltos ("Texto compilado Vigência") são ruído
                n = re.sub(r"\s*\($", "", n).strip()
                if not re.search(r"\(Vide\b|Lei|Decreto|Medida|Emenda", n):
                    continue
                if any(int(a) >= ano_min for a in re.findall(r"\b(?:19|20)\d\d\b", n)):
                    vig.append(n if n.count("(") <= n.count(")") else n + ")")
            if vig:
                cab["notas_de_vigencia_da_norma"] = vig[:12]
        if dispositivo and blocos:
            alertas = []
            try:
                futuros = self._efeitos_futuros(norma, doc, blocos)
            except Exception as e:  # complemento: a leitura não pode falhar por causa dele
                log.warning("Falha ao conferir notas de vigência de %s: %s", norma.nome, e)
                futuros = []
            if futuros:
                cab["efeitos_futuros"] = futuros
                for f in futuros:
                    if f["tipo"] == "revogacao_futura":
                        alertas.append(f"{f['norma']} ({f['dispositivo_revogador']}) revoga este dispositivo "
                                       f"a partir de {_data_br(f['data'])}: \"{f['trecho'][:300]}\"")
                    else:
                        alertas.append(f"a redação do {f['dispositivo'] or 'dispositivo'} dada por norma cuja "
                                       f"cláusula de vigência ({f['norma']}) só produz efeitos a partir de "
                                       f"{_data_br(f['data'])}; até lá vale a redação anterior"
                                       + (" (em 'redacao_ainda_aplicavel')" if f.get("redacao_ainda_aplicavel")
                                          else " (use modo='historico')"))
            pend = self._alteracoes_nao_refletidas(norma, dispositivo, blocos)
            if pend is None:
                cab["aviso_senado"] = ("Não foi possível conferir agora, no Senado, se há alteração recente deste "
                                       "dispositivo ainda não refletida no texto (portal lento ou fora do ar).")
            elif pend:
                alertas.append("o Senado registra alterações recentes deste dispositivo que não aparecem nas notas "
                               "do texto do Planalto (vigência futura ou compilado desatualizado): " + "; ".join(pend)
                               + ". Confira no DOU ou com verificar_atualizacao")
            if alertas:
                cab["alerta"] = " | ".join(a[0].upper() + a[1:] for a in alertas) + "."
        corpo = doc.renderizar(blocos, modo=modo, notas=notas, omitir_revogados=omitir_revogados)
        if not corpo:
            cab["aviso"] = ("O trecho pedido só existe em redação anterior (está riscado no Planalto). "
                            "Use modo='historico'.")
            return cab
        p = paginar(corpo, max_caracteres, pagina)
        cab["pagina"] = p.pagina
        cab["total_paginas"] = p.total_paginas
        cab["texto"] = p.texto
        if modo == "historico":
            cab["legenda"] = "Trechos entre ~~ ~~ são redações anteriores (riscadas no Planalto)."
        return cab

    def _efeitos_futuros(self, norma: Norma, doc: Documento, blocos: list | None,
                         prazo: float = 15.0) -> list[dict]:
        """Notas "(Produção de efeitos)", "(Vigência)" e "(Vide …)" cujo link aponta para uma cláusula com data
        futura, e revogações com data futura ("Ficam revogados a partir de 1º de janeiro de 2027: ... da Lei
        X: arts. 1º a 16").

        blocos: dispositivo lido (considera também o caput do artigo e as notas do cabeçalho);
        None = a norma inteira (para verificar_atualizacao)."""
        inicio, hoje_ = time.time(), hoje()
        if blocos is None:
            fontes = [(b, lk) for b in doc.blocos if not b.obsoleto for lk in b.links]
            artigos_lidos = None
        else:
            proprios = list(blocos)
            arts = {(b.espaco, b.artigo) for b in blocos if b.artigo}
            for b in doc.blocos:  # notas do caput valem para os incisos e parágrafos lidos
                if (b.espaco, b.artigo) in arts and len(b.chave) == 1 and b not in proprios:
                    proprios.append(b)
            fontes = [(b, lk) for b in proprios for lk in b.links]
            artigos_lidos = {a for _e, a in arts}
        fontes += [(None, lk) for lk in doc.links_gerais]
        resolvidos: dict[tuple[str, str], tuple | None] = {}
        revs_feitas: set[tuple[str, str]] = set()
        out, docs_externos = [], set()
        for b, (texto, href, contexto) in fontes:
            if time.time() - inicio > prazo:
                break
            t = normalizar(texto)
            if "encerrad" in t or "revogad" in t:
                continue
            efeito = bool(re.search(r"produc.o de efeito|vigencia", t))
            if not (efeito or t.startswith("(vide")):
                continue
            url, _, frag = href.partition("#")
            if not frag:
                continue
            k = (url.lower(), frag.lower())
            if k not in resolvidos:
                resolvidos[k] = None
                mesmo = url.lower() == doc.url.split("#")[0].lower()
                if not mesmo and url.lower() not in docs_externos and len(docs_externos) >= 6:
                    continue
                try:
                    alvo = doc if mesmo else self._doc_por_url(url)
                except Exception as e:  # link quebrado ou portal fora: a leitura segue sem a nota
                    log.info("Destino de nota de vigência indisponível (%s): %s", href, e)
                    continue
                if not mesmo:
                    docs_externos.add(url.lower())
                i = alvo.bloco_da_ancora(frag)
                if i is None:
                    continue
                trecho_bl = _trecho_da_ancora(alvo, i)
                trecho = _espacos_txt(" ".join(x.vigente for x in trecho_bl))[:1200]
                pub = data_de_publicacao(alvo.epigrafe)
                resolvidos[k] = (alvo, trecho_bl, trecho, pub,
                                 _nome_da_epigrafe(alvo.epigrafe) or url.rsplit("/", 1)[-1])
            if resolvidos[k] is None:
                continue
            alvo, trecho_bl, trecho, pub, nome_alvo = resolvidos[k]
            if k not in revs_feitas:
                revs_feitas.add(k)
                # 1) o destino é (ou cita) artigo de revogações com data
                arts_alvo = [x.artigo for x in trecho_bl[:1] if x.artigo and len(x.chave) == 1] or \
                    _artigos_citados(trecho, alvo)
                for rev in _revogacoes_diferidas(alvo, arts_alvo, norma.ref, artigos_lidos, pub,
                                                 data_efeitos(trecho, pub)):
                    if rev["data"] and rev["data"] > hoje_.isoformat():
                        rev["norma"] = nome_alvo
                        if rev not in out:
                            out.append(rev)
            # 2) a própria redação lida só produz efeitos no futuro
            if efeito and b is not None:
                d = data_efeitos(trecho, pub)
                if d and d > hoje_:
                    item = {"tipo": "efeitos_futuros", "norma": nome_alvo, "data": d.isoformat(),
                            "clausula": trecho[:600], "dispositivo": rotulo_dispositivo(b.chave) if b.chave else None}
                    anterior = _redacao_anterior(doc, b)
                    if anterior:
                        item["redacao_ainda_aplicavel"] = anterior[:1500]
                    if not any(o.get("clausula") == item["clausula"] and o.get("dispositivo") == item["dispositivo"]
                               for o in out):
                        out.append(item)
        return out

    def _alteracoes_nao_refletidas(self, norma: Norma, dispositivo: str, blocos: list) -> list[str]:
        """Alterações dos últimos 2 anos registradas pelo Senado para o dispositivo e não citadas nos blocos."""
        alvo = chave_dispositivo(dispositivo)
        if not alvo:
            return []
        # complemento da leitura: não pode atrasá-la. Espera até 10 s; a consulta segue em segundo plano
        # e fica em cache para a próxima leitura.
        futuro = _em_segundo_plano(self.obter_detalhe, norma)
        try:
            det, aviso = futuro.result(timeout=10)
        except Exception as e:  # sem Senado (ou lento), segue só com o texto
            log.info("Senado indisponível para o alerta de %s: %s", norma.nome, e)
            return None
        if not det:
            return None if aviso and "indispon" in aviso else []
        adct = norma.tipo == "CF" and _e_adct(dispositivo)
        limite = (hoje() - dt.timedelta(days=730)).isoformat()
        texto = " ".join(b.completo for b in blocos)
        vistas, out = set(), []
        for r in det.relacoes_recebidas:
            if (r.data or "") < limite or not r.dispositivo or not _acao_altera_texto(r):
                continue
            if r.origem.startswith("DEC:") and norma.tipo != "DEC":
                continue
            if norma.tipo == "CF" and (not r.origem.startswith("EMC:") or _e_adct(r.dispositivo) != adct):
                continue
            if not dispositivo_casa(alvo, chave_dispositivo(r.dispositivo)):
                continue
            ref = Referencia.de_chave(r.origem)
            if r.origem in vistas or cita_norma(texto, ref):
                continue
            if ref.tipo == "MPV" and self._mp_encerrada(r.origem):
                continue
            vistas.add(r.origem)
            out.append(f"{ref.nome} ({r.acao or 'alteração'}: {r.dispositivo})")
        return out[:8]

    # ================================================================ ficha
    def ficha(self, referencia: str) -> dict:
        norma = self.resolver(referencia)
        det, aviso = self.obter_detalhe(norma)
        norma = self.db.obter(norma.chave) or norma
        out = norma.resumo()
        out["tipo"] = TIPOS[norma.tipo].nome
        for k in ("urn", "indexacao", "catalogo", "observacao"):
            if getattr(norma, k):
                out[k] = getattr(norma, k)
        if norma.publicacao:
            pubs = norma.publicacao.split(" | ")
            out["publicacao"] = pubs[0] + (f" (+{len(pubs) - 1} republicações/retificações)" if len(pubs) > 1 else "")
        if norma.urn:
            out["url_lexml"] = f"https://normas.leg.br/?urn={norma.urn}"
        if det:
            rec = [r for r in det.relacoes_recebidas if _acao_altera_texto(r)]
            if norma.tipo == "CF":
                rec = [r for r in rec if r.origem.startswith("EMC:")]
            elif norma.tipo != "DEC":
                rec = [r for r in rec if not r.origem.startswith("DEC:")]  # decreto não altera lei
            fam: dict = {}
            out["alterada_por"] = len({self._familia_mp(r.origem, fam) for r in rec})
            out["ultima_alteracao"] = max((r.data for r in rec if r.data), default=None)
            regs = {}
            for r in det.relacoes_recebidas:
                if classificar_relacao(r) == "regulamenta":
                    regs.setdefault(self._familia_mp(r.origem, fam), r.origem)
            out["regulamentada_por"] = sorted(self._nome(c) for c in regs.values())
            out["normas_que_esta_altera"] = len({r.destino for r in det.relacoes_feitas
                                                 if classificar_relacao(r) == "altera"})
        sit, aviso_sit = _situacao_exibida(norma)
        if sit:
            out["situacao"] = sit
        if aviso_sit:
            out["aviso_situacao"] = aviso_sit
        if ed := self._aviso_edicao(referencia, norma):
            out["aviso_edicao"] = ed
        if aviso:
            out["aviso"] = aviso
        return out

    def _aviso_edicao(self, referencia: str, norma: Norma) -> str | None:
        """MP reeditada: o Planalto só publica o texto compilado da última edição da família."""
        try:
            pedida = interpretar(referencia)
        except ValueError:
            return None
        if pedida.tipo == "MPV" and norma.tipo == "MPV" and pedida.numero != norma.numero:
            return (f"Você pediu a MP {formatar_numero(pedida.numero)}; ela foi reeditada (ou renumerada) e o texto, "
                    f"a ficha e o histórico são os da última edição da família, a {norma.nome}. A redação de uma edição "
                    "intermediária pode ser diferente.")
        return None

    @staticmethod
    def _dispositivo_pedido(referencia: str, dispositivo: str | None) -> str | None:
        """Dispositivo informado à parte ou na própria referência ("art. 74 da Lei 9.430"). Com a referência
        "ADCT", o dispositivo é do ADCT ("art. 76" -> "art. 76 do ADCT")."""
        _ref, disp_citado = interpretar_citacao(referencia)
        if disp_citado == "ADCT" or (disp_citado and _e_adct(disp_citado) and not dispositivo):
            if dispositivo and not _e_adct(dispositivo):
                return f"{dispositivo} do ADCT"
            return dispositivo or (None if disp_citado == "ADCT" else disp_citado)
        return dispositivo or disp_citado

    def _alteradas_no_texto(self, norma: Norma, ja: set[str]) -> list[str]:
        """Normas que o próprio texto altera ("A Lei nº X passa a vigorar...", "Ficam revogados ... da Lei Y")
        e que o Senado não registrou."""
        try:
            doc = self.documento(norma)
        except Exception as e:  # o texto é complemento; sem ele fica só o Senado
            log.info("Texto de %s indisponível: %s", norma.nome, e)
            return []
        ja_fam = {_familia(c) for c in ja}
        achadas: dict[str, str] = {}
        ativo = False  # o artigo corrente altera/revoga outras normas
        for b in doc.blocos:
            if b.tipo != "dispositivo" or b.espaco:
                continue
            texto_b = b.crua()  # inclui o riscado: MP que perdeu a eficácia também alterou normas
            if len(b.chave) == 1:
                ativo = bool(re.search(r"passa(?:m)?\s+a\s+vigorar|ficam?\s+revogad|acrescid[oa]s?\s+d|"
                                       r"revoga(?:m)?(?:-se)?\s", texto_b, re.I))
            if not ativo:
                continue
            # sem as notas do Planalto ("Redação dada pela Lei Y" cita quem alterou ESTA norma)
            t = re.sub(r"\((?:Reda[çc][ãa]o dada|Inclu[íi]d|Revogad|Vide|Vig[êe]ncia|Produ[çc]|Acrescid|Renumerad|"
                       r"Regulamento|Promulga)[^)]*\)", " ", texto_b)
            for c in citacoes_de_normas(t):
                if c != norma.chave and _familia(c) not in ja_fam and c not in achadas:
                    achadas[c] = self._nome(c)
        return list(achadas.values())[:60]

    def _familia_mp(self, chave: str, cache: dict | None = None) -> str:
        """Como _familia, mas também junta linhagens renumeradas (MP 542 -> ... -> MP 1.027) pela tabela de
        reedições dos quadros do Planalto."""
        f = _familia(chave)
        if f == chave or not f.startswith("MPV:"):
            return f
        if cache is not None and chave in cache:
            return cache[chave]
        final = self.db.familia_mp(Referencia.de_chave(chave).numero)
        r = _familia(final) if final else f
        if cache is not None:
            cache[chave] = r
        return r

    def _nome(self, chave: str) -> str:
        try:
            return Referencia.de_chave(chave).nome
        except Exception:
            return chave

    # ================================================================ histórico
    def historico(self, referencia: str, dispositivo: str | None = None, desde: str | None = None,
                  direcao: str = "recebidas", incluir_correlatas: bool = False) -> dict:
        """direcao: "recebidas" (quem alterou esta norma) ou "feitas" (o que esta norma alterou)."""
        if direcao not in ("recebidas", "feitas"):
            raise ValueError("direcao deve ser 'recebidas' ou 'feitas'.")
        desde = ler_data(desde, "desde")
        dispositivo = self._dispositivo_pedido(referencia, dispositivo)
        norma = self.resolver(referencia)
        det, aviso = self.obter_detalhe(norma, max_idade=24 * 3600)
        if not det:
            return {"norma": norma.nome, "aviso": aviso or "Sem dados do Senado."}
        rels = det.relacoes_recebidas if direcao == "recebidas" else det.relacoes_feitas
        alvo = chave_dispositivo(dispositivo) if dispositivo else ()
        if dispositivo and not alvo:
            return {"norma": norma.nome, "aviso": f"Não entendi o dispositivo '{dispositivo}'. Ex.: 'art. 74, § 12'."}
        alvo_adct = norma.tipo == "CF" and _e_adct(dispositivo)
        grupos: dict[str, dict] = {}
        fam: dict = {}
        sem_dispositivo: set[str] = set()
        for r in rels:
            classe = classificar_relacao(r)
            if not incluir_correlatas and classe in ("correlata", "vetada"):
                continue
            if classe == "altera" and direcao == "recebidas" and r.origem.startswith("DEC:") and norma.tipo != "DEC":
                classe = "decreto (remissão/regulamento; decreto não altera lei)"
            if desde and (r.data or "") < desde:
                continue
            disp_r = r.dispositivo.strip()
            norma_inteira = not disp_r or (_e_adct(disp_r) and not chave_dispositivo(disp_r))
            if alvo:
                if norma_inteira:
                    # sem itens, só interessa ao dispositivo o que atinge a norma toda (revogação no todo,
                    # perda de eficácia...); "Declaração de Alteração" genérica não diz o que mudou
                    if not _decl_forte(r):
                        if classe == "altera":
                            sem_dispositivo.add(r.origem if direcao == "recebidas" else r.destino)
                        continue
                else:
                    if norma.tipo == "CF" and _e_adct(disp_r) != alvo_adct:
                        continue
                    if not dispositivo_casa(alvo, chave_dispositivo(disp_r)):
                        continue
            outra = r.origem if direcao == "recebidas" else r.destino
            chave_g = self._familia_mp(outra, fam)  # reedições de MP (pré-2001) num grupo só
            g = grupos.setdefault(chave_g, {
                "norma": self._nome(outra), "chave": outra, "data": r.data, "declaracao": r.declaracao,
                "dispositivos": [], "_classes": set(), "_edicoes": set(),
            })
            g["_classes"].add(classe)
            g["_edicoes"].add(outra)
            if (r.data or "") > (g["data"] or ""):
                g.update(norma=self._nome(outra), chave=outra, data=r.data)
            if norma_inteira:
                item = f"{r.acao or r.declaracao} (norma inteira)"
            else:
                item = f"{r.acao}: {disp_r}".strip(": ")
            if item and item not in g["dispositivos"]:
                g["dispositivos"].append(item)
        alteracoes, outras = [], []
        for g in grupos.values():
            classes, edicoes = g.pop("_classes"), g.pop("_edicoes")
            if len(edicoes) > 1:
                g["reedicoes"] = len(edicoes)
                g["norma"] += f" (última de {len(edicoes)} edições da MP)"
            g["dispositivos"] = g["dispositivos"][:30]
            if "altera" in classes:
                alteracoes.append(g)
            else:
                g["tipo"] = ", ".join(sorted(classes))
                outras.append(g)
        ordem = lambda g: (g["data"] or "", g["norma"])  # noqa: E731
        alteracoes.sort(key=ordem, reverse=True)
        outras.sort(key=ordem, reverse=True)
        for g in alteracoes + outras:
            n = self.db.obter(g["chave"])
            if n and n.ementa:
                g["ementa"] = n.ementa
        rotulo = rotulo_dispositivo(alvo) + (" do ADCT" if alvo_adct else "") if alvo else None
        out = {
            "norma": norma.nome,
            "direcao": "normas que alteraram esta" if direcao == "recebidas" else "normas alteradas por esta",
            "dispositivo": rotulo,
            "total": len(alteracoes),
            "alteracoes": alteracoes,
            "fonte": "Senado Federal - Dados Abertos (vides)",
        }
        sem_dispositivo -= {g["chave"] for g in alteracoes}
        if sem_dispositivo:
            out["alteracoes_sem_dispositivo"] = sorted(self._nome(c) for c in sem_dispositivo)
            out["nota_sem_dispositivo"] = ("O Senado registra que estas normas alteraram a norma, mas sem dizer quais "
                                           "dispositivos: podem ter atingido o dispositivo pedido. Confira nas notas "
                                           "do texto (ler_norma).")
        if direcao == "feitas":
            extras = self._alteradas_no_texto(norma, {g["chave"] for g in alteracoes + outras})
            if extras:
                out["citadas_em_clausulas_de_alteracao_sem_registro_no_senado"] = extras
            out["nota_feitas"] = ("Lista do Senado (vides), que pode estar incompleta sobretudo em normas recentes; "
                                  "'citadas_em_clausulas_de_alteracao_sem_registro_no_senado' vem do próprio texto "
                                  "('passa a vigorar', 'ficam revogados').")
        if outras:
            out["outras_relacoes"] = outras
            out["nota_outras"] = ("Ressalvas, regulamentações, novo tratamento da matéria e conversões/reedições "
                                  "de MP: não mudam a redação do dispositivo, mas podem afetar sua aplicação.")
        if alvo:
            out["observacao"] = ("Inclui alterações do próprio dispositivo, dos que ele contém e dos que o contêm "
                                 "(o Senado às vezes registra só o artigo), além de revogação ou perda de eficácia "
                                 "da norma inteira. Confira as notas do texto (ler_norma) para o detalhe.")
        if aviso:
            out["aviso"] = aviso
        return out

    # ================================================================ atualização
    def verificar_atualizacao(self, referencia: str) -> dict:
        """Confere se o texto do Planalto já incorpora as alterações conhecidas, dispositivo a dispositivo."""
        norma = self.resolver(referencia)
        doc = self.documento(norma, forcar=True)
        det, aviso = self.obter_detalhe(norma, max_idade=6 * 3600)
        texto_doc = " ".join(b.completo for b in doc.blocos) + " " + " ".join(doc.notas_gerais)
        res: dict = {"norma": norma.nome, "url": doc.url, "planalto_atualizado_em": _data_http(doc.last_modified)}
        if not det:
            res["conclusao"] = ("INCONCLUSIVO: sem dados de alterações do Senado para comparar. "
                                + (aviso or ""))
            return res
        if aviso:
            res["aviso"] = aviso
        por_norma: dict[str, list[Relacao]] = collections.defaultdict(list)
        for r in det.relacoes_recebidas:
            if not _acao_altera_texto(r) or (norma.tipo == "CF" and not r.origem.startswith("EMC:")):
                continue
            if r.origem.startswith("DEC:") and norma.tipo != "DEC":
                continue  # decreto não altera lei: o Senado registra assim remissões e tabelas
            por_norma[r.origem].append(r)
        pendentes, a_conferir, mps_antigas, antigas = [], [], [], []
        ultima = None
        limite_mp = (hoje() - dt.timedelta(days=180)).isoformat()
        limite_recente = (hoje() - dt.timedelta(days=730)).isoformat()
        for chave, rels in por_norma.items():
            ref = Referencia.de_chave(chave)
            data = max((r.data for r in rels if r.data), default=None)
            ultima = max(ultima or "", data or "") or None
            item = {"norma": ref.nome, "data": data,
                    "dispositivos": sorted({f"{r.acao}: {r.dispositivo}".strip(": ") for r in rels})[:15]}
            pad_arq = _padrao_arquivo(ref)
            citada_doc = bool(pad_arq and any(pad_arq.match(a) for a in doc.arquivos_citados)) or cita_norma(texto_doc, ref)
            estado, faltam = self._estado_no_texto(doc, ref, rels, citada_doc, adct=norma.tipo == "CF")
            if estado == "refletida":
                continue
            provisoria = ref.tipo == "MPV" and all("provis" in normalizar(r.declaracao) for r in rels)
            fim_mp = self._mp_encerrada(chave) if ref.tipo == "MPV" else None
            if provisoria and ((data and data < limite_mp) or fim_mp):
                if fim_mp:
                    item["situacao_mp"] = fim_mp
                mps_antigas.append(item)  # caducou (texto voltou) ou foi convertida (a lei é que aparece)
            elif (data or "") < limite_recente:
                # alteração antiga sem nota no texto: em geral remissão registrada pelo Senado como alteração,
                # artigo vetado, ou nota omitida pelo Planalto - não indica compilado desatualizado
                antigas.append(item)
            elif estado == "parcial":
                item["motivo"] = ("O texto cita esta norma em parte dos artigos alterados, mas não em: "
                                  + "; ".join(faltam[:10]) + ". Pode ser incorporação parcial, vigência "
                                  "escalonada ou registro impreciso do Senado.")
                a_conferir.append(item)
            elif estado == "so_no_cabecalho":
                item["motivo"] = ("A norma só aparece em 'Vide'/notas gerais, não nos artigos alterados: "
                                  "alteração com vigência futura ou ainda não incorporada.")
                if faltam:
                    item["artigos_sem_nota"] = faltam[:10]
                a_conferir.append(item)
            else:
                pendentes.append(item)
        try:
            futuros = self._efeitos_futuros(norma, doc, None, prazo=25.0)
        except Exception as e:
            log.warning("Falha ao conferir efeitos futuros de %s: %s", norma.nome, e)
            futuros = []
        if futuros:
            res["efeitos_e_revogacoes_futuras"] = futuros[:30]
            res["nota_efeitos_futuros"] = (
                "O texto do Planalto já mostra essas redações (ou ainda mostra os dispositivos revogados), mas elas "
                "só valem a partir da data indicada: até lá aplica-se a redação anterior / o dispositivo segue em vigor.")
        conhecidas = {_familia(c) for c in por_norma}
        recentes, recentes_cabecalho = self._recentes_que_alteram(norma, doc, texto_doc,
                                                                 {r.origem for r in det.relacoes_recebidas})
        a_conferir += recentes_cabecalho
        res["alteracoes_conhecidas"] = len(conhecidas)
        res["ultima_alteracao_conhecida"] = ultima
        res["nao_refletidas_no_texto"] = sorted(pendentes, key=lambda p: p["data"] or "", reverse=True)
        res["a_conferir"] = sorted(a_conferir, key=lambda p: p["data"] or "", reverse=True)
        res["normas_recentes_que_citam_na_ementa"] = recentes
        if antigas:
            res["antigas_sem_nota"] = sorted(antigas, key=lambda p: p["data"] or "", reverse=True)
            res["nota_antigas"] = ("Alterações com mais de 2 anos que não aparecem nas notas do texto. Costumam ser "
                                   "remissões que o Senado registra como alteração, artigos vetados ou notas omitidas "
                                   "pelo Planalto; confira só se o dispositivo for relevante para o caso.")
        if mps_antigas:
            mps_antigas.sort(key=lambda p: p["data"] or "", reverse=True)
            res["mps_antigas_nao_citadas"] = len(mps_antigas)
            res["mps_antigas_exemplos"] = mps_antigas[:10]
            res["nota_mps"] = ("Alterações provisórias de MPs encerradas (ou com mais de 180 dias) não indicam "
                               "desatualização: a MP caducou (e o texto voltou) ou foi convertida em lei (e a lei "
                               "aparece no texto).")
        if pendentes or recentes:
            res["conclusao"] = ("ATENÇÃO: há normas que alteram esta e não aparecem no texto do Planalto. O compilado "
                                "pode estar desatualizado nesses pontos; confira as normas listadas no DOU.")
        elif futuros and not a_conferir:
            res["conclusao"] = ("O texto reflete as alterações conhecidas, mas há redações ou revogações com efeitos "
                                "FUTUROS (veja 'efeitos_e_revogacoes_futuras'): confira a data antes de aplicar.")
        elif a_conferir:
            res["conclusao"] = ("A CONFERIR: há alterações que o texto só menciona em 'Vide' ou em parte dos "
                                "dispositivos alterados (em geral vigência futura). Veja 'a_conferir' e confirme "
                                "se a nova redação já vale para o seu caso.")
        else:
            res["conclusao"] = (f"O texto do Planalto reflete as alterações recentes conhecidas de {norma.nome} "
                                f"({len(conhecidas)} normas alteradoras no total).")
        res["observacao"] = ("Checagem automática: cada norma alteradora (Senado) é procurada nas notas e links do "
                             "próprio dispositivo alterado; acréscimos e revogações também são conferidos pela "
                             "existência do dispositivo. O Senado às vezes registra como alteração simples remissões; "
                             "confirme no DOU antes de concluir.")
        return res

    def _mp_encerrada(self, chave: str) -> str | None:
        """Situação da MP quando ela já não produz efeitos próprios (convertida, caducada, rejeitada)."""
        n = self.db.obter(chave)
        sit = normalizar(n.situacao or "") if n else ""
        if any(x in sit for x in ("convert", "encerrad", "sem eficacia", "perda de eficacia", "rejeitad",
                                  "caduc", "revogad")):
            return n.situacao
        return None

    def _estado_no_texto(self, doc: Documento, ref: Referencia, rels: list[Relacao], citada_doc: bool,
                         adct: bool = False) -> tuple[str, list[str]]:
        """(refletida | parcial | so_no_cabecalho | ausente, artigos sem nota da alteradora).

        Avalia artigo por artigo: o artigo reflete a alteração quando cita a alteradora numa nota, quando
        o dispositivo acrescentado existe (e não foi incluído por outra norma) ou quando o revogado
        aparece revogado."""
        por_artigo: dict[str, list[tuple[tuple, str]]] = {}
        for r in rels:
            k = chave_dispositivo(r.dispositivo) if r.dispositivo else ()
            if not k:
                continue
            sufixo = " do ADCT" if adct and _e_adct(r.dispositivo) else ""
            por_artigo.setdefault(rotulo_dispositivo(k[:1]) + sufixo, []).append((k, normalizar(r.acao), sufixo))
        if not por_artigo:
            return ("refletida" if citada_doc else "ausente"), []
        ok, faltam = 0, []
        for rot_art, itens in por_artigo.items():
            bl = doc.selecionar(rot_art)
            if not bl:
                continue  # artigo não localizado (numeração do Senado diferente): não decide
            if _cita_incorporada(" ".join(b.completo for b in bl), ref):
                ok += 1
                continue
            refletido = False
            for k, acao, sufixo in itens:
                alvo = doc.selecionar(rotulo_dispositivo(k) + sufixo)
                if not alvo:
                    continue
                if acao.startswith("acrescimo") and not _incluido_por_outra(alvo, ref):
                    refletido = True  # o dispositivo acrescentado existe
                elif acao.startswith("revogacao") and all(b.revogado or b.obsoleto for b in alvo
                                                          if b.tipo == "dispositivo"):
                    refletido = True
            if refletido:
                ok += 1
            else:
                faltam.append(rot_art)
        if not ok and not faltam:
            return ("refletida" if citada_doc else "ausente"), []
        if not faltam:
            return "refletida", []
        if ok:
            return "parcial", faltam
        return ("so_no_cabecalho" if citada_doc else "ausente"), faltam

    def _recentes_que_alteram(self, norma: Norma, doc: Documento, texto_doc: str,
                              ja_conhecidas: set[str]) -> tuple[list[dict], list[dict]]:
        """Normas dos últimos ~13 meses (cobre vacatio de até 1 ano) cuja ementa diz alterar/revogar esta e
        que o Senado ainda não registrou. Devolve (não citadas no texto, citadas só no cabeçalho)."""
        desde = (hoje() - dt.timedelta(days=400)).isoformat()
        conhecidas = {_familia(c) for c in ja_conhecidas}
        texto_artigos = None
        ausentes, cabecalho = [], []
        for n in self.db.recentes(desde):
            if n.chave == norma.chave or not n.ementa or _familia(n.chave) in conhecidas:
                continue
            if n.tipo == "DEC" and norma.tipo in ("LEI", "LCP", "DEL", "MPV", "CF", "EMC"):
                continue  # decreto não altera lei
            if not _ementa_altera(n.ementa, norma.ref):
                continue
            pad_arq = _padrao_arquivo(n.ref)
            citada = (pad_arq and any(pad_arq.match(a) for a in doc.arquivos_citados)) or cita_norma(texto_doc, n.ref)
            item = {"norma": n.nome, "data": n.data, "ementa": n.ementa}
            if not citada:
                if n.tipo == "MPV" and self._mp_encerrada(n.chave):
                    continue
                ausentes.append(item)
                continue
            if texto_artigos is None:
                texto_artigos = " ".join(b.completo for b in doc.blocos if b.tipo == "dispositivo")
            if not _cita_incorporada(texto_artigos, n.ref):
                item["motivo"] = ("A ementa diz alterar esta norma, mas o texto só a cita em 'Vide' (cabeçalho ou "
                                  "remissão no artigo), não como redação dada/incluída: vigência futura ou alteração "
                                  "ainda não incorporada.")
                cabecalho.append(item)
        return ausentes, cabecalho

    # ================================================================ busca
    def buscar(self, consulta: str, tipos: list[str] | None = None, ano_inicio: int | None = None,
               ano_fim: int | None = None, limite: int = 20, pagina: int = 1) -> dict:
        tipos = normalizar_tipos(tipos)
        _validar_anos(ano_inicio, ano_fim)
        limite = max(1, min(int(limite), 100))
        pagina = max(1, int(pagina))
        expandida = _SINONIMOS_SIGLA.get(normalizar(consulta).strip())
        res, total = self.db.buscar(expandida or consulta, tipos, ano_inicio, ano_fim, limite, (pagina - 1) * limite)
        resultados = [n.resumo() for n, _ in res]
        # a consulta é uma citação ("Lei 9.430/96", "RIR")? põe a norma citada no topo
        if pagina == 1:
            try:
                ref = interpretar(consulta)
                if ref.ano or ref.tipo == "CF":
                    citadas = [self.db.obter(ref.chave)]
                else:  # "LC 214" sem ano: todas as normas com esse número
                    citadas = self.db.candidatos(ref)[:5]
                for n in reversed([c for c in citadas if c]):
                    if n.chave not in {r["chave"] for r in resultados}:
                        resultados.insert(0, n.resumo())
                        total += 1
            except ValueError:
                pass
        out = {
            "consulta": consulta,
            **({"consulta_expandida": expandida} if expandida else {}),
            "total": total,
            "pagina": pagina,
            "total_paginas": max(1, math.ceil(total / limite)),
            "resultados": resultados,
        }
        if consulta.strip() and not resultados and total == 0:
            out["aviso"] = "Nada encontrado. Tente sinônimos, menos palavras ou OU entre alternativas."
        est = self.db.estatisticas()
        if est["total_normas"] < 1000:
            out["aviso"] = ("O catálogo local ainda está pequeno; a busca pode estar incompleta. "
                            "Rode sincronizar_catalogo (ou `mcp-planalto sincronizar`).")
        elif est["normas_com_detalhe_senado"] < est["total_normas"] / 3:
            out["observacao"] = (
                "A busca cobre ementas e apelidos de todo o catálogo, mas a indexação temática do Senado "
                f"só foi baixada para {est['normas_com_detalhe_senado']} normas. "
                "Para cobertura máxima: `mcp-planalto sincronizar --detalhes`.")
        return out

    # ================================================================ novidades
    def atualizar_recentes(self, max_idade: float = 0) -> int:
        """Relê os quadros do ano corrente (e do anterior, até fevereiro) de todos os tipos.
        max_idade=0 revalida com ETag/Last-Modified: barato quando nada mudou."""
        h = hoje()
        ano_ini = h.year - 1 if h.month <= 2 else h.year
        n, falhas, ok = 0, [], 0
        with self.sincronizando:
            for tipo in TIPOS_CATALOGO:
                if tipo in ("DEL", "LDL"):
                    continue
                f_tipo: list = []
                t_tipo = time.time()
                try:
                    es = self.indices.entradas(tipo, ano_inicio=ano_ini, max_idade=max_idade, falhas=f_tipo)
                except ErroHTTP as e:
                    log.warning("Falha ao atualizar quadro %s: %s", tipo, e)
                    falhas.append(tipo)
                    continue
                if f_tipo or any(t >= t_tipo for u, t in list(self.http.copias_entregues.items())
                                 if "planalto.gov.br" in u):
                    falhas.append(tipo)
                else:
                    ok += 1
                n += self._salvar_entradas(es)
        if ok:
            self.db.set_meta("recentes_em", agora())
        self.ultimas_falhas_recentes = falhas
        return n

    def novidades(self, desde: str | None = None, dias: int = 30, tipos: list[str] | None = None,
                  tema: str | None = None, termos: list[str] | None = None, limite: int = 100) -> dict:
        desde = ler_data(desde, "desde")
        if int(dias) < 1:
            raise ValueError("dias deve ser 1 ou mais.")
        dias = min(int(dias), 3660)
        if desde and desde > hoje().isoformat():
            raise ValueError(f"A data 'desde' ({desde}) está no futuro.")
        if not desde:
            desde = (hoje() - dt.timedelta(days=dias)).isoformat()
        tipos = normalizar_tipos(tipos)
        limite = max(1, min(int(limite), 500))
        aviso = []
        revalidado = True
        try:
            self.atualizar_recentes(max_idade=900)  # quadros relidos há menos de 15 min valem
            if self.ultimas_falhas_recentes:
                revalidado = False
                aviso.append("Não foi possível reler agora os quadros do Planalto de: "
                             + ", ".join(self.ultimas_falhas_recentes) + ". Normas publicadas nos últimos dias "
                             "podem faltar; usando o catálogo local.")
        except Exception as e:  # o catálogo local ainda serve
            revalidado = False
            aviso.append(f"Não foi possível reler os quadros do Planalto agora ({e}); usando o catálogo local.")
        normas = self.db.recentes(desde, tipos)
        if tema or termos:
            temas = localizar_temas(tema) if tema else []
            if tema and not temas:
                aviso.append(f"Tema '{tema}' não é pré-configurado (veja listar_temas): filtrando pela expressão "
                             "na ementa.")
            if len(normas) > 3000:
                raise ValueError("Janela grande demais para filtrar por tema (mais de 3.000 normas). Reduza o período.")
            filtro = self._filtro_tematico(temas, ([tema] if tema and not temas else []) + (termos or []))
            itens, sem_conferir = filtro(normas)
            if sem_conferir:
                aviso.append(f"{sem_conferir} normas recentes não puderam ser conferidas no Senado (limite de consultas "
                             "por chamada); só a ementa delas foi analisada.")
        else:
            itens = [(n, None) for n in normas]
        saida = []
        for n, motivo in itens:
            item = n.resumo()
            if motivo:
                item["motivo"] = motivo
            saida.append(item)
        out = {
            "desde": desde,
            "total": len(saida),
            "normas": saida[:limite],
            "fonte": ("Quadros de legislação do Planalto (revalidados agora)" if revalidado else
                      "Catálogo local (os quadros do Planalto não puderam ser relidos agora)"),
        }
        if len(saida) > limite:
            out["observacao"] = f"Mostrando {limite} de {len(saida)}; aumente `limite` para ver todas."
        if aviso:
            out["aviso"] = " ".join(aviso)
        return out

    def _filtro_tematico(self, temas: list[Tema], termos: list[str], max_consultas: int = 60):
        consultas = [c for t in temas for c in t.termos] + termos
        exprs = [(c, _consulta_regex(c)) for c in consultas if c.strip()]
        nucleo: dict[str, Callable | None] = {}
        for t in temas:
            for c in t.nucleo:
                try:
                    ref, disp = interpretar_citacao(c)
                    nucleo[ref.chave] = escopo_de(disp)
                except ValueError:
                    pass

        def motivo_termos(n: Norma, indexacao: str | None = None) -> str | None:
            texto = normalizar(" ".join(x for x in (n.ementa, n.apelido, n.indexacao, indexacao) if x))
            for c, rx in exprs:
                if rx(texto):
                    return f"ementa/indexação contém {c}"
            return None

        def motivo_ementa(n: Norma) -> str | None:
            """Só quando o Senado ainda não registrou as alterações da norma."""
            for k, esc in nucleo.items():
                if _ementa_altera(n.ementa or "", Referencia.de_chave(k)):
                    if esc is None:
                        return f"ementa diz alterar {self._nome(k)}"
                    return (f"ementa diz alterar {self._nome(k)} (o Senado ainda não detalhou quais dispositivos: "
                            "confira se atinge a parte da norma ligada ao tema)")
            return None

        def motivo_relacoes(rels: list[Relacao]) -> str | None:
            alvos = set()
            for r in rels:
                if r.destino in nucleo and classificar_relacao(r) == "altera" and \
                        _relacao_no_escopo(r, nucleo[r.destino]):
                    alvos.add(r.destino)
            return ("altera " + ", ".join(sorted(self._nome(a) for a in alvos))) if alvos else None

        def filtrar(normas: list[Norma]) -> tuple[list[tuple[Norma, str]], int]:
            achados: dict[str, str] = {}
            consultar = []
            for n in normas:
                m = motivo_termos(n)
                if m:
                    achados[n.chave] = m
                    continue
                if not nucleo:
                    continue
                if n.chave in nucleo:
                    achados[n.chave] = "norma-núcleo do tema"
                    continue
                rels = self.db.relacoes(origem=n.chave)
                if rels:
                    m = motivo_relacoes(rels)  # o Senado já detalhou: decide por ele (escopo, vetos)
                elif n.detalhe_em and time.time() - n.detalhe_em < 7 * 24 * 3600:
                    m = motivo_ementa(n)
                elif n.tipo in ("LEI", "LCP", "MPV", "DEL", "EMC") or (
                        n.tipo == "DEC" and re.search(r"tribut|imposto|contribuic|aliquota|incidencia",
                                                      normalizar(n.ementa or ""))):
                    consultar.append(n)
                    continue
                else:
                    m = None
                if m:
                    achados[n.chave] = m
            sobra = max(0, len(consultar) - max_consultas)
            for n in consultar[max_consultas:]:
                if m := motivo_ementa(n):
                    achados[n.chave] = m
            with ThreadPoolExecutor(max_workers=4) as ex:
                futuros = {ex.submit(self._buscar_detalhe, n.ref, 7 * 24 * 3600, False): n
                           for n in consultar[:max_consultas]}
                for fut in as_completed(futuros):
                    n = futuros[fut]
                    try:
                        det, _ = fut.result()
                    except Exception:
                        det = None
                    if det and det.relacoes_feitas:
                        m = motivo_relacoes(det.relacoes_feitas) or motivo_termos(n, det.norma.indexacao)
                    else:
                        m = (motivo_termos(n, det.norma.indexacao) if det else None) or motivo_ementa(n)
                    if m:
                        achados[n.chave] = m
            return [(n, achados[n.chave]) for n in normas if n.chave in achados], sobra

        return filtrar

    # ================================================================ mapeamento temático
    def mapear_tema(self, tema: str, termos_extras: list[str] | None = None,
                    normas_extras: list[str] | None = None, tipos: list[str] | None = None,
                    ano_inicio: int | None = None, ano_fim: int | None = None,
                    seguir_alteracoes: bool = True, profundidade: int = 1, limite_busca: int = 1000,
                    agrupar_reedicoes: bool = True,
                    progresso: Callable[[str], None] | None = None) -> dict:
        """Monta o acervo de normas de um tema combinando três fontes de evidência:

        1. normas-núcleo do tema (curadas ou informadas), com escopo por dispositivo quando há;
        2. normas que alteraram, revogaram ou regulamentaram o núcleo (grafo do Senado);
        3. normas cuja ementa, apelido ou indexação do Senado batem com os termos do tema.
        """
        aviso_p = progresso or (lambda _m: None)
        if not (tema or "").strip() and not normas_extras and not termos_extras:
            raise ValueError("Informe o tema (ex.: 'IRPJ') ou normas/termos.")
        temas = localizar_temas(tema) if tema else []
        termos = [c for t in temas for c in t.termos] if temas else ([_termo_livre(tema)] if tema else [])
        termos += termos_extras or []
        termos = [t for t in dict.fromkeys(termos) if t and t.strip()]
        nucleo_txt = [c for t in temas for c in t.nucleo] + list(normas_extras or [])
        tipos = normalizar_tipos(tipos)
        _validar_anos(ano_inicio, ano_fim)
        profundidade = max(1, min(int(profundidade), 2))
        avisos: list[str] = []

        achados: dict[str, dict] = {}

        def marcar(chave: str, pontos: float, motivo: str, camada: str) -> None:
            a = achados.setdefault(chave, {"pontos": 0.0, "motivos": [], "camadas": set()})
            a["pontos"] += pontos
            a["camadas"].add(camada)
            if motivo not in a["motivos"]:
                a["motivos"].append(motivo)

        # 1. núcleo (sem duplicatas)
        nucleo: dict[str, tuple[Norma, Callable | None, str | None]] = {}
        nao_resolvidas = []
        for c in nucleo_txt:
            try:
                _ref, disp = interpretar_citacao(c)
                n = self.resolver(c)
            except (NormaNaoEncontrada, ValueError, ErroHTTP) as e:
                nao_resolvidas.append(f"{c}: {e}")
                continue
            if n.chave not in nucleo:
                nucleo[n.chave] = (n, escopo_de(disp), disp)
                marcar(n.chave, 100, "norma-núcleo do tema" + (f" ({disp})" if disp else ""), "núcleo")
        aviso_p(f"Núcleo: {len(nucleo)} normas")

        # 2. grafo de alterações
        extras_fracas: dict[str, list[str]] = {}
        if seguir_alteracoes and nucleo:
            fronteira = [(n, esc) for n, esc, _d in nucleo.values()]
            vistos = set(nucleo)
            for nivel in range(1, profundidade + 1):
                proxima: collections.Counter = collections.Counter()
                camada = "alteradora/regulamentadora" if nivel == 1 else "alteradora de 2º nível"
                with ThreadPoolExecutor(max_workers=4) as ex:
                    futuros = {ex.submit(self.detalhe, n): (n, esc) for n, esc in fronteira}
                    for fut in as_completed(futuros):
                        alvo, esc = futuros[fut]
                        try:
                            det = fut.result()
                        except Exception as e:  # rede
                            log.warning("Falha no detalhe de %s: %s", alvo.nome, e)
                            continue
                        if not det:
                            continue
                        por_origem: dict[str, list[Relacao]] = collections.defaultdict(list)
                        for r in det.relacoes_recebidas:
                            if esc is not None:
                                o = self.db.obter(r.origem) if not r.dispositivo else None
                                if not _relacao_no_escopo(r, esc, o.ementa if o else None):
                                    continue  # alteração fora do escopo do tema (ou sem dispositivo legível)
                            por_origem[r.origem].append(r)
                        for origem, rels in por_origem.items():
                            classes = collections.Counter(classificar_relacao(r) for r in rels)
                            ndisp = len({r.dispositivo for r in rels if classificar_relacao(r) == "altera"})
                            if classes["altera"]:
                                pts = 40 + 4 * min(ndisp, 10)
                                rot = f"altera/revoga {max(ndisp, 1)} dispositivo(s) de {alvo.nome}"
                                cam = camada
                            elif classes["regulamenta"]:
                                pts, rot, cam = 45, f"regulamenta {alvo.nome}", camada
                            else:
                                # correlata, ressalva e vetos não bastam para entrar no acervo (ruído: quase toda
                                # lei tributária é "correlata" ao CTN); só contam se a norma vier por outra via
                                for classe, rotulo in (("correlata", "legislação correlata a"),
                                                       ("ressalva", "ressalva ou novo tratamento de"),
                                                       ("vetada", "só dispositivos vetados sobre")):
                                    if classes[classe]:
                                        extras_fracas.setdefault(origem, []).append(f"{rotulo} {alvo.nome}")
                                        break
                                continue
                            if nivel > 1:
                                pts *= 0.4
                            marcar(origem, pts, rot, cam)
                            if classes["altera"] and origem not in vistos:
                                proxima[origem] += ndisp
                aviso_p(f"Grafo nível {nivel}: {len(achados)} normas")
                if nivel >= profundidade:
                    break
                fronteira = []
                for chave, ndisp in proxima.most_common(30):
                    if ndisp < 3:
                        break
                    n = self.db.obter(chave) or self._norma_de_chave(chave)
                    if n:
                        fronteira.append((n, None))
                        vistos.add(chave)
                if not fronteira:
                    break

        # 3. catálogo (ementa, apelido, indexação). Vale o melhor termo, não a soma: sinônimos do nome do
        # imposto ("imposto de renda" / "imposto sobre a renda") não podem inflar a mesma norma.
        por_termo: dict[str, tuple[float, list[str]]] = {}
        for termo in termos:
            try:
                res, total = self.db.buscar(termo, tipos, ano_inicio, ano_fim, limite_busca)
            except Exception as e:  # expressão FTS inválida
                log.warning("Termo ignorado (%s): %s", termo, e)
                continue
            if total > limite_busca:
                avisos.append(f"O termo {termo} tem {total} normas; só as {limite_busca} mais relevantes entraram.")
            for n, score in res:
                if _ementa_orcamentaria(n.ementa):
                    continue
                pts, mots = por_termo.get(n.chave, (0.0, []))
                por_termo[n.chave] = (max(pts, 20 + min(score, 15) * 2), mots + [termo])
        for chave, (pts, mots) in por_termo.items():
            marcar(chave, pts, "ementa/indexação: " + ", ".join(mots[:4]), "relacionada")
        aviso_p(f"Catálogo: {len(achados)} normas")

        for chave, motivos in extras_fracas.items():
            if chave in achados:
                for m in motivos:
                    marcar(chave, 5, m, "relacionada")

        if agrupar_reedicoes:
            achados = self._agrupar_reedicoes(achados)
        self._completar_metadados(list(achados))
        achados = self._agrupar_conversoes(achados)

        # Montagem
        ordem_camada = {"núcleo": 0, "alteradora/regulamentadora": 1, "relacionada": 2, "alteradora de 2º nível": 3}
        linhas = []
        regex_termos = [_consulta_regex(t) for t in termos]
        descartadas_2o = 0
        for chave, a in achados.items():
            n = self.db.obter(chave) or self._norma_de_chave(chave)
            if not n:
                continue
            if a["camadas"] == {"alteradora de 2º nível"}:
                # 2º nível só com evidência do tema: as alteradoras de 1º nível são leis de vários assuntos
                texto = normalizar(" ".join(x for x in (n.ementa, n.apelido, n.indexacao) if x))
                if not any(rx(texto) for rx in regex_termos):
                    descartadas_2o += 1
                    continue
            camada = min(a["camadas"], key=lambda c: ordem_camada[c])
            if camada != "núcleo":
                if tipos and n.tipo not in tipos:
                    continue
                if ano_inicio and (n.ano or 0) < ano_inicio:
                    continue
                if ano_fim and (n.ano or 9999) > ano_fim:
                    continue
            if camada != "núcleo" and _ementa_orcamentaria(n.ementa):
                a["pontos"] *= 0.3
            linhas.append({
                "norma": n.nome, "chave": n.chave, "tipo": TIPOS[n.tipo].nome, "data": n.data,
                "ementa": n.ementa, "apelido": n.apelido, "situacao": n.situacao,
                "relevancia": round(a["pontos"], 1), "camada": camada, "motivos": a["motivos"],
                "url": n.url_planalto,
            })
        linhas.sort(key=lambda x: (ordem_camada[x["camada"]], -x["relevancia"], x["data"] or ""))
        for i, l in enumerate(linhas, 1):
            l["posicao"] = i
        if descartadas_2o:
            avisos.append(f"{descartadas_2o} normas de 2º nível ficaram de fora por não terem termos do tema na "
                          "ementa/indexação (alteram leis de vários assuntos).")
        est = self.db.estatisticas()
        if not linhas:
            avisos.append("Nenhuma norma encontrada. Para temas livres, informe normas_extras (normas-núcleo) e "
                          "termos_extras; veja listar_temas para os temas pré-configurados.")
        elif not nucleo and not temas:
            avisos.append("Tema livre sem normas-núcleo: o resultado depende só de palavras na ementa. Informe "
                          "normas_extras para seguir o grafo de alterações.")
        if est["normas_com_detalhe_senado"] < est["total_normas"] / 3:
            avisos.append(f"A indexação temática do Senado está baixada para só {est['normas_com_detalhe_senado']} "
                          "normas: a camada 'relacionada' depende quase só da ementa. Para cobertura máxima rode "
                          "`mcp-planalto sincronizar --detalhes`.")
        out = {
            "tema": " + ".join(t.nome for t in temas) if temas else tema,
            "termos_usados": termos,
            "nucleo": [n.nome + (f" ({d})" if d else "") for n, _e, d in nucleo.values()],
            "nucleo_nao_encontrado": nao_resolvidas,
            "total": len(linhas),
            "por_camada": dict(collections.Counter(l["camada"] for l in linhas)),
            "por_tipo": dict(collections.Counter(l["tipo"] for l in linhas)),
            "normas": linhas,
            "cobertura": {
                "normas_no_catalogo": est["total_normas"],
                "normas_com_indexacao_senado": est["normas_com_detalhe_senado"],
            },
        }
        if avisos:
            out["avisos"] = avisos
        return out

    def _agrupar_reedicoes(self, achados: dict[str, dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for chave, a in achados.items():
            destino = chave
            ref = Referencia.de_chave(chave) if chave.startswith("MPV:") else None
            base = ref.numero.split("-")[0] if ref else ""
            # Reedições só existiram até a EC 32/2001; depois dela a numeração recomeçou do 1.
            if ref and ref.ano and base.isdigit() and (ref.ano < 2001 or (ref.ano == 2001 and int(base) >= 1000)):
                destino = self.db.familia_mp(ref.numero) or chave
                if destino == chave and "-" in ref.numero:
                    irmas = [k for k in achados if k.startswith(f"MPV:{base}-")]
                    destino = max(irmas, key=lambda k: _sufixo_reedicao(k))
            g = out.setdefault(destino, {"pontos": 0.0, "motivos": [], "edicoes": set(), "camadas": set()})
            g["pontos"] = max(g["pontos"], a["pontos"])
            g["camadas"] |= a["camadas"]
            for m in a["motivos"]:
                if m not in g["motivos"]:
                    g["motivos"].append(m)
            if destino != chave:
                g["edicoes"].add(Referencia.de_chave(chave).nome)
        for g in out.values():
            if g["edicoes"]:
                g["motivos"].append(f"agrupa {len(g['edicoes'])} reedição(ões) anteriores")
        return out

    def _agrupar_conversoes(self, achados: dict[str, dict]) -> dict[str, dict]:
        """MP convertida em lei que também está na lista: fica só a lei, com a MP citada no motivo."""
        out = dict(achados)
        for chave in list(achados):
            if not chave.startswith("MPV:"):
                continue
            n = self.db.obter(chave)
            sit = (n.situacao if n else "") or ""
            m = re.search(r"Convertid[ao]\s+(?:em\s+lei\s*\()?(?:n[ao]\s+)?Lei\s+n?[º°o]?\.?\s*([\d.]+)"
                          r"(?:,?\s+de\s+(?:\d{1,2}/\d{1,2}/)?(\d{4}))?", sit, re.I)
            if not m:
                continue
            numero = m.group(1).replace(".", "")
            alvo = next((k for k in out if k.startswith(f"LEI:{numero}:")
                         and (not m.group(2) or k.endswith(m.group(2)))), None)
            if not alvo or chave not in out:
                continue
            a = out.pop(chave)
            g = out[alvo]
            g["pontos"] = max(g["pontos"], a["pontos"])
            g["camadas"] |= a["camadas"]
            g["motivos"].append(f"inclui a {Referencia.de_chave(chave).nome}, convertida nesta lei")
        return out

    def _completar_metadados(self, chaves: list[str], maximo: int = 80) -> None:
        """Busca no Senado ementa e data de normas que não estão no catálogo do Planalto."""
        faltam = []
        for k in chaves:
            n = self.db.obter(k)
            if not n or not n.ementa:
                sid = self.db.senado_id(k)
                if sid:
                    faltam.append(sid)
        if not faltam:
            return

        def um(sid):
            try:
                return self.senado.detalhe_por_id(sid, max_idade=30 * 24 * 3600)
            except ErroHTTP:
                return None

        with ThreadPoolExecutor(max_workers=4) as ex:
            for det in ex.map(um, faltam[:maximo]):
                if det:
                    self._gravar_detalhe(det, guardar=False)

    def _norma_de_chave(self, chave: str) -> Norma | None:
        try:
            ref = Referencia.de_chave(chave)
        except Exception:
            return None
        return Norma(chave=ref.chave, tipo=ref.tipo, numero=ref.numero, ano=ref.ano)

    # ================================================================ sincronização
    def sincronizar_catalogo(self, tipos: list[str] | None = None, ano_inicio: int | None = None,
                             senado: bool = False, progresso: Callable[[str], None] | None = None) -> dict:
        """Lê os quadros do Planalto (e, opcionalmente, as listas do Senado) para o catálogo local."""
        aviso = progresso or (lambda _m: None)
        tipos = normalizar_tipos(tipos) or TIPOS_CATALOGO
        if not self._sync_lock.acquire(blocking=False):
            raise RuntimeError("Já existe uma sincronização em andamento neste processo.")
        self.sincronizando.__enter__()
        try:
            resumo = {}
            falhas: list[str] = []
            for tipo in tipos:
                try:
                    es = self.indices.entradas(tipo, ano_inicio=ano_inicio, max_idade=12 * 3600, falhas=falhas)
                except ErroHTTP as e:  # página-raiz do tipo indisponível
                    falhas.append(f"{tipo}: {e}")
                    continue
                resumo[tipo] = self._salvar_entradas(es)
                aviso(f"Planalto {tipo}: {resumo[tipo]} normas")
            if senado:
                ano0 = ano_inicio or 1988
                for tipo in tipos:
                    total = 0
                    for ano in range(ano0, hoje().year + 1):
                        try:
                            normas = self.senado.lista(tipo, ano, max_idade=7 * 24 * 3600)
                        except ErroHTTP as e:
                            log.warning("Lista do Senado %s/%s indisponível: %s", tipo, ano, e)
                            continue
                        for n in normas:  # não sobrescreve a ementa do Planalto
                            if self.db.obter(n.chave):
                                n.ementa = None
                        total += self.db.salvar(normas)
                    aviso(f"Senado {tipo}: {total} normas")
            if set(tipos) >= set(TIPOS_CATALOGO) and not ano_inicio and not falhas:
                # só a carga completa e sem falhas marca o catálogo como pronto (senão é retomada no próximo início)
                self.db.set_meta("catalogo_em", agora())
            out = {"normas_por_tipo": resumo, **self.db.estatisticas()}
            if falhas:
                out["quadros_com_falha"] = falhas
                out["aviso"] = (f"{len(falhas)} quadro(s) do Planalto não puderam ser lidos; a carga será completada na "
                                "próxima sincronização.")
            return out
        finally:
            self.sincronizando.__exit__()
            self._sync_lock.release()

    def sincronizar_detalhes(self, tipos: list[str] | None = None, ano_inicio: int | None = None,
                             limite: int | None = None, paralelo: int = 4,
                             progresso: Callable[[str], None] | None = None) -> dict:
        """Baixa do Senado indexação e vides das normas do catálogo ainda sem detalhe (retomável)."""
        aviso = progresso or (lambda _m: None)
        tipos = normalizar_tipos(tipos) or TIPOS_DETALHE_PADRAO
        if limite is not None and limite < 1:
            raise ValueError("limite deve ser pelo menos 1 (omita para processar todas).")
        pendentes = [n for n in self.db.iterar(tipos, ano_inicio, sem_detalhe=True) if n.ano]
        if limite is not None:
            pendentes = pendentes[:limite]
        ok = falhas = vazios = 0
        inicio = time.time()

        def um(n: Norma):
            return n, self.senado.detalhe(n.ref, max_idade=30 * 24 * 3600)

        with self.sincronizando, ThreadPoolExecutor(max_workers=max(1, min(paralelo, 8))) as ex:
            if True:
                futuros = [ex.submit(um, n) for n in pendentes]
                for i, fut in enumerate(as_completed(futuros), 1):
                    try:
                        n, det = fut.result()
                    except Exception as e:
                        falhas += 1
                        log.info("Falha no detalhe: %s", e)
                        continue
                    if det:
                        self._gravar_detalhe(det, guardar=False)
                        ok += 1
                        if det.norma.chave != n.chave:  # garante que a norma pedida não é buscada de novo
                            self.db.salvar([Norma(chave=n.chave, tipo=n.tipo, numero=n.numero, ano=n.ano,
                                                  detalhe_em=agora())])
                    else:
                        vazios += 1
                        self.db.salvar([Norma(chave=n.chave, tipo=n.tipo, numero=n.numero, ano=n.ano,
                                              detalhe_em=agora())])
                    if i % 100 == 0:
                        taxa = i / max(1, time.time() - inicio)
                        aviso(f"Detalhes: {i}/{len(pendentes)} ({taxa:.1f}/s, falhas {falhas})")
        self.db.set_meta("detalhes_em", agora())
        return {"processadas": len(pendentes), "com_detalhe": ok, "sem_registro_no_senado": vazios,
                "falhas": falhas, **self.db.estatisticas()}

    def status(self) -> dict:
        est = self.db.estatisticas()

        def quando(chave):
            v = self.db.meta(chave)
            return dt.datetime.fromtimestamp(float(v), FUSO).isoformat(timespec="minutes") if v else None

        est.update({
            "catalogo_sincronizado_em": quando("catalogo_em"),
            "recentes_atualizados_em": quando("recentes_em"),
            "detalhes_sincronizados_em": quando("detalhes_em"),
            "sincronizando_agora": self.sincronizando.is_set(),
            "diretorio_de_dados": str(self.config.home),
        })
        return est


# ---------------------------------------------------------------- utilitários

def _validar_anos(ano_inicio: int | None, ano_fim: int | None) -> None:
    if ano_inicio and ano_fim and ano_inicio > ano_fim:
        raise ValueError(f"Intervalo de anos invertido: ano_inicio {ano_inicio} > ano_fim {ano_fim}.")


def _sufixo_reedicao(chave: str) -> int:
    m = re.search(r"-(\d+):", chave)
    return int(m.group(1)) if m else 0


_STOPWORDS = {"de", "da", "do", "das", "dos", "e", "a", "o", "as", "os", "para", "por", "em", "no", "na", "nos",
              "nas", "com", "sobre", "ao", "aos"}


def _termo_livre(tema: str) -> str:
    """Tema livre vira busca por radicais, sem palavras vazias:
    "subvenções para investimento" -> "subvenc* investim*" (casa subvenção/subvenções, investimento/investimentos)."""
    alternativas = []
    for parte in re.split(r"\s+/\s+|\s+ou\s+|\s*;\s*", normalizar(tema)):  # "Pilar 2 / tributação mínima"
        palavras = [p for p in re.findall(r"[\w/]+", parte) if p not in _STOPWORDS and p != "/"]
        # só palavras longas viram radical: "reporto" truncado casaria "repor", "reportagem"
        radicais = [(p[: max(6, len(p) - 3)] + "*") if len(p) >= 9 and p.isalpha() else p for p in palavras]
        if radicais:
            alternativas.append(" ".join(radicais))
    return " OU ".join(alternativas)


def _ementa_orcamentaria(ementa: str | None) -> bool:
    e = normalizar(ementa or "")
    return bool(re.match(r"(abre|reabre)\b.*\bcredito|estima a receita|orca a receita|"
                         r"dispoe sobre as diretrizes para a elaboracao", e))


def _ementa_altera(ementa: str, ref: Referencia) -> bool:
    """A ementa diz que altera/revoga a norma (e não só a cita, como "que regulamenta a Lei X")?"""
    e = normalizar(ementa)
    # a finalidade ("para incluir ... do art. 121 do Código Penal") só cita a norma; vai até a próxima
    # norma alterada (", e a Lei Y") ou o fim
    e = re.sub(r"\b(?:para|a fim de|com o objetivo de)\b.*?(?=,?\s+e\s+(?:a|o|as|os)\s+(?:lei|decreto|medida|"
               r"codigo|consolidacao|emenda)|;|$)", " ", e)
    for m in re.finditer(r"\b(altera|revoga|acrescenta|acresce|da nova redacao|modifica|inclui|prorroga)\w*\b", e):
        trecho = e[m.start(): m.start() + 600]
        trecho = re.split(r";|\be da outras\b", trecho)[0]
        trecho = re.sub(r"\bque (regulamenta|dispoe|institui|trata)\b.*?(?=,|$)", " ", trecho)
        if cita_norma(trecho, ref):
            return True
    return False


def _consulta_regex(consulta: str) -> Callable[[str], bool]:
    """Avalia uma consulta no formato do buscador (aspas, OU, prefixo*) sobre texto normalizado,
    respeitando fronteira de palavra ("PIS" não casa "piso")."""
    grupos: list[list[re.Pattern]] = [[]]
    for m in re.finditer(r'"([^"]+)"|(\S+)', consulta):
        tok = m.group(2)
        if tok and tok.upper() in ("OU", "OR", "|"):
            grupos.append([])
            continue
        bruto = normalizar(m.group(1) or tok)
        prefixo = bruto.endswith("*")
        termo = bruto.rstrip("*")
        if not termo:
            continue
        if tok and re.fullmatch(r"\w+(?:/\w+)+", termo):  # mesma regra do buscador: "IRPJ/CSLL" = um ou outro
            alts = "|".join(re.escape(a) for a in termo.split("/"))
            grupos[-1].append(re.compile(rf"\b(?:{alts})" + ("" if prefixo else r"\b")))
            continue
        if tok and not prefixo and tok.lower() == tok and (radical := radical_flexao(termo)):
            termo, prefixo = radical, True
        grupos[-1].append(re.compile(r"\b" + re.escape(termo) + ("" if prefixo else r"\b")))
    pads = [g for g in grupos if g]
    return lambda texto: any(all(p.search(texto) for p in g) for g in pads)
