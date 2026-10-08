"""Camada de serviço: junta catálogo local, Planalto e Senado.

Usada pelo servidor MCP e pela linha de comando.
"""

from __future__ import annotations

import collections
import datetime as dt
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable

from .config import Config
from .db import Banco, Norma, Relacao, agora
from .fontes.planalto_indices import EntradaIndice, IndicesPlanalto
from .fontes.planalto_texto import Documento, ler_documento
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
    if a.startswith(("ressalva", "com novo tratamento")) or "novo tratamento" in d:
        return "ressalva"
    if "conversao" in d and not a:
        return "conversao"
    if "reedicao" in d and not a:
        return "reedicao"  # "Reedição com/sem Alteração" de MP  # "Conversão em Lei com Alteração": a MP virou lei, não foi alterada
    if "revogacao" in d and "no todo" in d:
        return "altera"
    if a.startswith(("alteracao", "acrescimo", "revogacao", "supressao", "renumeracao", "revigora",
                     "restauracao", "repristinacao", "retificacao com sentido", "nova redacao")):
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


def cita_norma(texto: str, ref: Referencia) -> bool:
    """O texto cita a norma? Reconhece "Lei nº 9.430", "Lei n o 9.430", "Lei 9430", "Leis nºs 9.249 e 9.430",
    sem confundir "Decreto-Lei nº 2.848" com "Lei nº 2.848"."""
    if ref.tipo == "CF":
        return False
    t = normalizar(texto)
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


def _familia(chave: str) -> str:
    """MPs reeditadas (pré-2001) contam como uma só: "MPV:1537-41:1997" -> "MPV:1537"."""
    if chave.startswith("MPV:") and re.match(r"MPV:\d+-\d+:", chave):
        return "MPV:" + chave.split(":")[1].split("-")[0]
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
        self._fundo = ThreadPoolExecutor(max_workers=2, thread_name_prefix="senado")
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
    def documento(self, norma: Norma, forcar: bool = False) -> Documento:
        if not norma.url_planalto:
            raise NormaNaoEncontrada(
                f"{norma.nome} não tem texto disponível no Planalto (o quadro do Planalto não traz o link, "
                "ou o link aponta para outra norma).")
        url = norma.url_planalto
        max_idade = 0 if forcar else self.config.cache_horas * 3600
        with self._lock:
            if not forcar and url in self._docs and time.time() - self._docs[url][0] < max_idade:
                self._docs.move_to_end(url)
                return self._docs[url][1]
        r = self.http.get(url, max_idade=max_idade)
        doc = ler_documento(r.texto(), r.url, r.last_modified)
        with self._lock:
            self._docs[url] = (time.time(), doc)
            while len(self._docs) > 12:
                self._docs.popitem(last=False)
        return doc

    def texto(self, referencia: str, dispositivo: str | None = None, termo: str | None = None,
              modo: str = "vigente", notas: bool = True, omitir_revogados: bool = False,
              estrutura: bool = False, pagina: int = 1, max_caracteres: int = 40000) -> dict:
        if modo not in ("vigente", "historico"):
            raise ValueError("modo deve ser 'vigente' ou 'historico'.")
        _ref, disp_citado = interpretar_citacao(referencia)
        dispositivo = dispositivo or disp_citado
        norma = self.resolver(referencia)
        doc = self.documento(norma)
        cab = {
            "norma": norma.nome,
            "epigrafe": doc.epigrafe or None,
            "ementa": norma.ementa or doc.ementa,
            "url": doc.url,
            "planalto_atualizado_em": _data_http(doc.last_modified),
        }
        if norma.situacao:
            cab["situacao"] = norma.situacao
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
        if termo:
            base = blocos if blocos is not None else doc.blocos
            sub = Documento(doc.url, doc.epigrafe, doc.ementa, doc.notas_gerais, base)
            blocos, n_arts = sub.buscar(termo)
            cab["artigos_com_o_termo"] = n_arts
            if n_arts > 15:
                cab["observacao"] = f"Mostrando os 15 primeiros de {n_arts} artigos com o termo."
            if not blocos:
                cab["aviso"] = f"O termo '{termo}' não aparece no texto vigente."
                return cab
        if blocos is None and doc.notas_gerais:
            cab["notas_gerais"] = doc.notas_gerais
        elif dispositivo and doc.notas_gerais:
            # "(Vide Lei X) Vigência", "produção de efeitos": valem também para o dispositivo lido
            ano_min = hoje().year - 2
            vig = [n for n in doc.notas_gerais
                   if re.search(r"vig[êe]ncia|efeitos|vigorar", n, re.I)
                   or any(int(a) >= ano_min for a in re.findall(r"\b(?:19|20)\d\d\b", n))]
            if vig:
                cab["notas_de_vigencia_da_norma"] = vig[:12]
        if dispositivo and blocos:
            pend = self._alteracoes_nao_refletidas(norma, dispositivo, blocos)
            if pend:
                cab["alerta"] = ("O Senado registra alterações recentes deste dispositivo que não aparecem nas notas "
                                 "do texto do Planalto (vigência futura ou compilado desatualizado): "
                                 + "; ".join(pend) + ". Confira no DOU ou com verificar_atualizacao.")
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

    def _alteracoes_nao_refletidas(self, norma: Norma, dispositivo: str, blocos: list) -> list[str]:
        """Alterações dos últimos 2 anos registradas pelo Senado para o dispositivo e não citadas nos blocos."""
        alvo = chave_dispositivo(dispositivo)
        if not alvo:
            return []
        # complemento da leitura: não pode atrasá-la. Espera até 10 s; a consulta segue em segundo plano
        # e fica em cache para a próxima leitura.
        futuro = self._fundo.submit(self.obter_detalhe, norma)
        try:
            det, _ = futuro.result(timeout=10)
        except Exception as e:  # sem Senado (ou lento), segue só com o texto
            log.info("Senado indisponível para o alerta de %s: %s", norma.nome, e)
            return []
        if not det:
            return []
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
            out["alterada_por"] = len({_familia(r.origem) for r in rec})
            out["ultima_alteracao"] = max((r.data for r in rec if r.data), default=None)
            regs = {}
            for r in det.relacoes_recebidas:
                if classificar_relacao(r) == "regulamenta":
                    regs.setdefault(_familia(r.origem), r.origem)
            out["regulamentada_por"] = sorted(self._nome(c) for c in regs.values())
            out["normas_que_esta_altera"] = len({r.destino for r in det.relacoes_feitas
                                                 if classificar_relacao(r) == "altera"})
        if norma.tipo == "MPV" and "tramit" in normalizar(norma.situacao or ""):
            if (norma.data or "9999") < "2001-09-12":
                out["situacao"] = ("Em vigor por força do art. 2º da EC 32/2001 (MP anterior à emenda: vale até "
                                   "ser convertida, rejeitada ou revogada)")
            elif norma.data and norma.data < (hoje() - dt.timedelta(days=130)).isoformat():
                out["aviso_situacao"] = ("O cadastro diz 'em tramitação', mas o prazo constitucional da MP (até 120 "
                                         "dias, fora o recesso) já passou: provavelmente perdeu a eficácia ou foi "
                                         "convertida. Confira no Congresso.")
        if aviso:
            out["aviso"] = aviso
        return out

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
        _ref, disp_citado = interpretar_citacao(referencia)
        dispositivo = dispositivo or disp_citado
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
        for r in rels:
            classe = classificar_relacao(r)
            if not incluir_correlatas and classe in ("correlata", "vetada"):
                continue
            if desde and (r.data or "") < desde:
                continue
            disp_r = r.dispositivo.strip()
            norma_inteira = not disp_r or (_e_adct(disp_r) and not chave_dispositivo(disp_r))
            if alvo:
                if norma_inteira:
                    # sem itens, só interessa ao dispositivo o que atinge a norma toda (revogação no todo,
                    # perda de eficácia...); "Declaração de Alteração" genérica não diz o que mudou
                    if not _decl_forte(r):
                        continue
                else:
                    if norma.tipo == "CF" and _e_adct(disp_r) != alvo_adct:
                        continue
                    if not dispositivo_casa(alvo, chave_dispositivo(disp_r)):
                        continue
            outra = r.origem if direcao == "recebidas" else r.destino
            chave_g = _familia(outra)  # reedições de MP (pré-2001) num grupo só
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
            if classes & {"altera"} or (direcao == "feitas" and not classes - {"altera", "outra"}):
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
        res, total = self.db.buscar(consulta, tipos, ano_inicio, ano_fim, limite, (pagina - 1) * limite)
        resultados = [n.resumo() for n, _ in res]
        # a consulta é uma citação ("Lei 9.430/96", "RIR")? põe a norma citada no topo
        if pagina == 1:
            try:
                ref = interpretar(consulta)
                if ref.ano or ref.tipo == "CF":
                    n = self.db.obter(ref.chave)
                    if n and n.chave not in {r["chave"] for r in resultados}:
                        resultados.insert(0, n.resumo())
                        total += 1
            except ValueError:
                pass
        out = {
            "consulta": consulta,
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
        n = 0
        with self.sincronizando:
            for tipo in TIPOS_CATALOGO:
                if tipo in ("DEL", "LDL"):
                    continue
                try:
                    es = self.indices.entradas(tipo, ano_inicio=ano_ini, max_idade=max_idade)
                except ErroHTTP as e:
                    log.warning("Falha ao atualizar quadro %s: %s", tipo, e)
                    continue
                n += self._salvar_entradas(es)
        self.db.set_meta("recentes_em", agora())
        return n

    def novidades(self, desde: str | None = None, dias: int = 30, tipos: list[str] | None = None,
                  tema: str | None = None, termos: list[str] | None = None, limite: int = 100) -> dict:
        desde = ler_data(desde, "desde")
        dias = max(1, min(int(dias), 3660))
        if not desde:
            desde = (hoje() - dt.timedelta(days=dias)).isoformat()
        tipos = normalizar_tipos(tipos)
        limite = max(1, min(int(limite), 500))
        aviso = []
        try:
            self.atualizar_recentes()
        except Exception as e:  # o catálogo local ainda serve
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
            "fonte": "Quadros de legislação do Planalto (revalidados agora)",
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

        def motivo_local(n: Norma) -> str | None:
            texto = normalizar(" ".join(x for x in (n.ementa, n.apelido, n.indexacao) if x))
            for c, rx in exprs:
                if rx(texto):
                    return f"ementa/indexação contém {c}"
            if n.chave in nucleo:
                return "norma-núcleo do tema"
            for k in nucleo:
                if _ementa_altera(n.ementa or "", Referencia.de_chave(k)):
                    return f"ementa diz alterar {self._nome(k)}"
            return None

        def motivo_relacoes(rels: list[Relacao]) -> str | None:
            alvos = set()
            for r in rels:
                if r.destino in nucleo and classificar_relacao(r) == "altera":
                    esc = nucleo[r.destino]
                    k = chave_dispositivo(r.dispositivo) if r.dispositivo else ()
                    if esc is None or not k or esc(k[0][1]):
                        alvos.add(r.destino)
            return ("altera " + ", ".join(sorted(self._nome(a) for a in alvos))) if alvos else None

        def filtrar(normas: list[Norma]) -> tuple[list[tuple[Norma, str]], int]:
            achados: dict[str, str] = {}
            consultar = []
            for n in normas:
                m = motivo_local(n)
                if m:
                    achados[n.chave] = m
                    continue
                if not nucleo:
                    continue
                rels = self.db.relacoes(origem=n.chave)
                if rels or (n.detalhe_em and time.time() - n.detalhe_em < 7 * 24 * 3600):
                    m = motivo_relacoes(rels)
                    if m:
                        achados[n.chave] = m
                elif n.tipo in ("LEI", "LCP", "MPV", "DEL", "EMC") or (
                        n.tipo == "DEC" and re.search(r"tribut|imposto|contribuic|aliquota|incidencia",
                                                      normalizar(n.ementa or ""))):
                    consultar.append(n)
            sobra = max(0, len(consultar) - max_consultas)
            with ThreadPoolExecutor(max_workers=4) as ex:
                futuros = {ex.submit(self._buscar_detalhe, n.ref, 7 * 24 * 3600, False): n
                           for n in consultar[:max_consultas]}
                for fut in as_completed(futuros):
                    n = futuros[fut]
                    try:
                        det, _ = fut.result()
                    except Exception:
                        continue
                    if det:
                        m = motivo_relacoes(det.relacoes_feitas)
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
                            if esc is not None and r.dispositivo:
                                k = chave_dispositivo(r.dispositivo)
                                if k and not esc(k[0][1]):
                                    continue  # alteração fora do escopo do tema
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

        # 3. catálogo (ementa, apelido, indexação)
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
                marcar(n.chave, 20 + min(score, 15) * 2, f"ementa/indexação: {termo}", "relacionada")
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
        for chave, a in achados.items():
            n = self.db.obter(chave) or self._norma_de_chave(chave)
            if not n:
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
    palavras = [p for p in re.findall(r"[\w/]+", normalizar(tema)) if p not in _STOPWORDS]
    radicais = [(p[: max(5, len(p) - 3)] + "*") if len(p) >= 7 and p.isalpha() else p for p in palavras]
    return " ".join(radicais)


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
    grupos: list[list[str]] = [[]]
    for m in re.finditer(r'"([^"]+)"|(\S+)', consulta):
        if m.group(2) and m.group(2).upper() in ("OU", "OR", "|"):
            grupos.append([])
            continue
        bruto = normalizar(m.group(1) or m.group(2))
        prefixo = bruto.endswith("*")
        termo = bruto.rstrip("*")
        if termo:
            grupos[-1].append((termo, prefixo))
    pads = [[re.compile(r"\b" + re.escape(t) + ("" if p else r"\b")) for t, p in g] for g in grupos if g]
    return lambda texto: any(all(p.search(texto) for p in g) for g in pads)
