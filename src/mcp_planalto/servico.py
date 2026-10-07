"""Camada de serviço: junta catálogo local, Planalto e Senado.

Usada pelo servidor MCP e pela linha de comando.
"""

from __future__ import annotations

import collections
import datetime as dt
import logging
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
    dispositivo_contem,
    formatar_numero,
    interpretar,
    normalizar,
    rotulo_dispositivo,
)
from .temas import Tema, localizar_tema

log = logging.getLogger(__name__)

TIPOS_CATALOGO = ["LEI", "LCP", "DEC", "DEL", "MPV", "EMC", "LDL"]
TIPOS_DETALHE_PADRAO = ["LEI", "LCP", "DEL", "MPV", "EMC", "LDL"]


class NormaNaoEncontrada(Exception):
    def __init__(self, mensagem: str, candidatos: list[Norma] | None = None):
        super().__init__(mensagem)
        self.candidatos = candidatos or []


def _acao_altera_texto(r: Relacao) -> bool:
    """A relação muda o texto da norma de destino (e deveria aparecer no texto compilado)?"""
    a, d = normalizar(r.acao), normalizar(r.declaracao)
    if "vetad" in a or "vetad" in d or "veto" in a:
        return False
    if any(x in d for x in ("correlata", "citada", "regulament", "informativa", "constitucionalidade")):
        return False
    if "revogacao" in d and "no todo" in d:
        return True
    return a.startswith(("alteracao", "acrescimo", "revogacao", "supressao", "renumeracao", "revigora",
                         "restauracao", "repristinacao", "retificacao com sentido", "nova redacao"))


def _data_http(valor: str | None) -> str | None:
    if not valor:
        return None
    try:
        return parsedate_to_datetime(valor).date().isoformat()
    except (TypeError, ValueError):
        return None


def _padrao_arquivo(ref: Referencia) -> re.Pattern | None:
    base = ref.numero.split("-")[0]
    prefixo = {"LEI": "l", "LCP": "lcp", "DEC": "d", "DEL": "del", "MPV": "(?:mpv)?", "EMC": "emc", "LDL": "ldl"}.get(ref.tipo)
    if prefixo is None:
        return None
    return re.compile(rf"^{prefixo}0*{base}(?:[-_][0-9a-z]+)*[a-z]*\.html?$")


def _padrao_citacao(ref: Referencia) -> re.Pattern:
    nome = TIPOS[ref.tipo].nome
    nome = nome.replace("Medida Provisória", r"Medida\s+Provis[óo]ria").replace(" ", r"\s+")
    num = re.escape(formatar_numero(ref.numero.split("-")[0]))
    return re.compile(rf"{nome}\s+n[º°o]?\.?\s*{num}\b", re.I)


@dataclass
class PaginaTexto:
    texto: str
    pagina: int
    total_paginas: int


def paginar(texto: str, max_caracteres: int, pagina: int) -> PaginaTexto:
    if len(texto) <= max_caracteres:
        return PaginaTexto(texto, 1, 1)
    paginas, atual, tam = [], [], 0
    for linha in texto.split("\n"):
        if tam + len(linha) + 1 > max_caracteres and atual:
            paginas.append("\n".join(atual))
            atual, tam = [], 0
        atual.append(linha)
        tam += len(linha) + 1
    if atual:
        paginas.append("\n".join(atual))
    pagina = max(1, min(pagina, len(paginas)))
    return PaginaTexto(paginas[pagina - 1], pagina, len(paginas))


class Legislacao:
    def __init__(self, config: Config | None = None):
        self.config = (config or Config()).preparar()
        self.http = ClienteHTTP(self.config.cache_dir / "http", self.config.user_agent)
        self.db = Banco(self.config.db_path)
        self.indices = IndicesPlanalto(self.http)
        self.senado = Senado(self.http)
        self._docs: collections.OrderedDict[str, tuple[float, Documento]] = collections.OrderedDict()
        self._docs_lock = threading.Lock()
        self._detalhes: dict[str, DetalheSenado] = {}
        self.sincronizando = threading.Event()

    # ================================================================ resolução
    def resolver(self, texto: str) -> Norma:
        """Encontra a norma citada (no catálogo local, no Planalto e no Senado)."""
        ref = interpretar(texto)
        if not ref.ano:
            cands = self.db.candidatos(ref)
            exatos = [c for c in cands if c.numero == ref.numero]
            if len(exatos) == 1:
                ref = exatos[0].ref
            elif len(exatos) > 1:
                raise NormaNaoEncontrada(
                    f"Há mais de uma norma '{ref.nome}'. Informe o ano.", exatos[:10])
            else:
                e = self.indices.localizar(ref.tipo, ref.numero, None)
                if not e:
                    raise NormaNaoEncontrada(f"Não encontrei {ref.nome}. Informe o ano (ex.: {ref.nome}/1996).")
                self._salvar_entradas([e])
                ref = Referencia(e.tipo, e.numero, e.ano)
        n = self.db.obter(ref.chave)
        if n and n.url_planalto:
            return n
        e = self.indices.localizar(ref.tipo, ref.numero, ref.ano)
        if e:
            self._salvar_entradas([e])
            return self.db.obter(Referencia(e.tipo, e.numero, e.ano).chave)
        if n:
            return n
        det = self._buscar_detalhe(ref)
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
            normas.append(Norma(
                chave=ref.chave, tipo=e.tipo, numero=e.numero, ano=e.ano, data=e.data, ementa=e.ementa or None,
                url_planalto=e.url, origem="planalto", atualizado_em=agora(),
                situacao=e.situacao if e.tipo == "MPV" and e.situacao and e.situacao != "-" else None,
            ))
        if reedicoes:
            self.db.salvar_reedicoes(reedicoes)
        return self.db.salvar(normas)

    # ================================================================ Senado
    def _buscar_detalhe(self, ref: Referencia, max_idade: float | None = 7 * 24 * 3600) -> DetalheSenado | None:
        try:
            det = self.senado.detalhe(ref, max_idade=max_idade)
        except ErroHTTP as e:
            log.warning("Senado indisponível para %s: %s", ref.nome, e)
            return None
        if det:
            self._gravar_detalhe(det)
        return det

    def _gravar_detalhe(self, det: DetalheSenado) -> None:
        det.norma.detalhe_em = agora()
        # A ementa do Planalto prevalece (é a do texto publicado); o Senado completa o resto.
        atual = self.db.obter(det.norma.chave)
        if atual and atual.ementa:
            det.norma.ementa = None
        self.db.salvar([det.norma])
        self.db.salvar_relacoes(
            det.relacoes_recebidas + det.relacoes_feitas,
            substituir_de=[("destino", det.norma.chave), ("origem", det.norma.chave)],
        )
        self._detalhes[det.norma.chave] = det

    def detalhe(self, norma: Norma, max_idade: float = 7 * 24 * 3600) -> DetalheSenado | None:
        if norma.chave in self._detalhes and norma.detalhe_em and time.time() - norma.detalhe_em < max_idade:
            return self._detalhes[norma.chave]
        if not norma.ano:
            return None
        return self._buscar_detalhe(norma.ref, max_idade=max_idade)

    # ================================================================ texto
    def documento(self, norma: Norma, forcar: bool = False) -> Documento:
        if not norma.url_planalto:
            raise NormaNaoEncontrada(
                f"{norma.nome} não tem texto no Planalto (ou ainda não foi localizada nos quadros).")
        url = norma.url_planalto
        max_idade = 0 if forcar else self.config.cache_horas * 3600
        with self._docs_lock:
            if not forcar and url in self._docs and time.time() - self._docs[url][0] < max_idade:
                self._docs.move_to_end(url)
                return self._docs[url][1]
        r = self.http.get(url, max_idade=max_idade)
        doc = ler_documento(r.texto(), r.url, r.last_modified)
        with self._docs_lock:
            self._docs[url] = (time.time(), doc)
            while len(self._docs) > 12:
                self._docs.popitem(last=False)
        return doc

    def texto(self, referencia: str, dispositivo: str | None = None, termo: str | None = None,
              modo: str = "vigente", notas: bool = True, omitir_revogados: bool = False,
              estrutura: bool = False, pagina: int = 1, max_caracteres: int = 40000) -> dict:
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
        if estrutura:
            cab["estrutura"] = doc.estrutura()
            cab["total_artigos"] = len(doc.artigos())
            return cab
        blocos = None
        if dispositivo:
            blocos = doc.selecionar(dispositivo)
            if not blocos:
                cab["aviso"] = f"Dispositivo '{dispositivo}' não encontrado. Use estrutura=true para ver o sumário."
                return cab
        if termo:
            base = blocos if blocos is not None else doc.blocos
            sub = Documento(doc.url, doc.epigrafe, doc.ementa, doc.notas_gerais, base)
            blocos, n_arts = sub.buscar(termo)
            cab["artigos_com_o_termo"] = n_arts
            if not blocos:
                cab["aviso"] = f"O termo '{termo}' não aparece no texto vigente."
                return cab
        if blocos is None and doc.notas_gerais:
            cab["notas_gerais"] = doc.notas_gerais
        corpo = doc.renderizar(blocos, modo=modo, notas=notas, omitir_revogados=omitir_revogados)
        p = paginar(corpo, max_caracteres, pagina)
        cab["pagina"] = p.pagina
        cab["total_paginas"] = p.total_paginas
        cab["texto"] = p.texto
        if modo == "historico":
            cab["legenda"] = "Trechos entre ~~ ~~ são redações anteriores (riscadas no Planalto)."
        return cab

    # ================================================================ ficha
    def ficha(self, referencia: str) -> dict:
        norma = self.resolver(referencia)
        det = self.detalhe(norma)
        norma = self.db.obter(norma.chave) or norma
        out = norma.resumo()
        out["tipo"] = TIPOS[norma.tipo].nome
        for k in ("urn", "indexacao", "catalogo", "observacao", "publicacao"):
            if getattr(norma, k):
                out[k] = getattr(norma, k)
        if norma.urn:
            out["url_lexml"] = f"https://normas.leg.br/?urn={norma.urn}"
        if det:
            rec = [r for r in det.relacoes_recebidas if _acao_altera_texto(r)]
            out["alterada_por"] = len({r.origem for r in rec})
            out["ultima_alteracao"] = max((r.data for r in rec if r.data), default=None)
            out["regulamentada_por"] = sorted({
                self._nome(r.origem) for r in det.relacoes_recebidas if "regulament" in normalizar(r.declaracao + r.acao)
            })
            out["normas_que_esta_altera"] = len({r.destino for r in det.relacoes_feitas})
        else:
            out["aviso"] = "Sem dados do Senado (indexação/alterações) para esta norma."
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
        norma = self.resolver(referencia)
        det = self.detalhe(norma, max_idade=24 * 3600)
        if not det:
            return {"norma": norma.nome, "aviso": "O Senado não tem o histórico desta norma."}
        rels = det.relacoes_recebidas if direcao == "recebidas" else det.relacoes_feitas
        alvo = chave_dispositivo(dispositivo) if dispositivo else ()
        if dispositivo and not alvo:
            return {"norma": norma.nome, "aviso": f"Não entendi o dispositivo '{dispositivo}'. Ex.: 'art. 74, § 12'."}
        grupos: dict[str, dict] = {}
        for r in rels:
            if not incluir_correlatas and any(x in normalizar(r.declaracao) for x in ("correlata", "citada")):
                continue
            if desde and (r.data or "") < desde:
                continue
            if alvo:
                k = chave_dispositivo(r.dispositivo)
                if not k or not (dispositivo_contem(alvo, k) or dispositivo_contem(k, alvo)):
                    continue
            outra = r.origem if direcao == "recebidas" else r.destino
            g = grupos.setdefault(outra, {
                "norma": self._nome(outra), "chave": outra, "data": r.data, "declaracao": r.declaracao,
                "dispositivos": [],
            })
            item = f"{r.acao}: {r.dispositivo}".strip(": ") if r.dispositivo else r.acao
            if item and item not in g["dispositivos"]:
                g["dispositivos"].append(item)
        lista = sorted(grupos.values(), key=lambda g: (g["data"] or "", g["norma"]), reverse=True)
        for g in lista:
            n = self.db.obter(g["chave"])
            if n and n.ementa:
                g["ementa"] = n.ementa
        return {
            "norma": norma.nome,
            "direcao": "normas que alteraram esta" if direcao == "recebidas" else "normas alteradas por esta",
            "dispositivo": rotulo_dispositivo(alvo) if alvo else None,
            "total": len(lista),
            "alteracoes": lista,
            "fonte": "Senado Federal - Dados Abertos (vides)",
        }

    # ================================================================ atualização
    def verificar_atualizacao(self, referencia: str) -> dict:
        """Confere se o texto do Planalto já incorpora todas as alterações conhecidas."""
        norma = self.resolver(referencia)
        doc = self.documento(norma, forcar=True)
        det = self.detalhe(norma, max_idade=6 * 3600)
        texto = " ".join(b.completo for b in doc.blocos) + " " + " ".join(doc.notas_gerais)
        res: dict = {
            "norma": norma.nome,
            "url": doc.url,
            "planalto_atualizado_em": _data_http(doc.last_modified),
        }
        pendentes, mps_antigas, conferidas = [], [], 0
        ultima = None
        if det:
            por_norma: dict[str, list[Relacao]] = collections.defaultdict(list)
            for r in det.relacoes_recebidas:
                if _acao_altera_texto(r):
                    por_norma[r.origem].append(r)
            for chave, rels in por_norma.items():
                ref = Referencia.de_chave(chave)
                data = max((r.data for r in rels if r.data), default=None)
                ultima = max(ultima or "", data or "") or None
                pad_arq = _padrao_arquivo(ref)
                citada = bool(pad_arq and any(pad_arq.match(a) for a in doc.arquivos_citados)) \
                    or bool(_padrao_citacao(ref).search(texto))
                conferidas += 1
                if not citada:
                    item = {
                        "norma": ref.nome, "data": data,
                        "dispositivos": sorted({f"{r.acao}: {r.dispositivo}".strip(": ") for r in rels})[:15],
                    }
                    provisoria = ref.tipo == "MPV" and all("provis" in normalizar(r.declaracao) for r in rels)
                    if provisoria and data and data < (dt.date.today() - dt.timedelta(days=180)).isoformat():
                        # MP antiga: ou caducou, ou foi convertida (e então a lei de conversão é que aparece)
                        mps_antigas.append(item)
                    else:
                        pendentes.append(item)
        else:
            res["aviso_senado"] = "Sem dados do Senado; só foi possível checar normas recentes do catálogo."
        # Normas recentes do catálogo cuja ementa menciona esta norma (o Senado pode ainda não ter indexado)
        recentes = []
        desde = (dt.date.today() - dt.timedelta(days=120)).isoformat()
        num = formatar_numero(norma.numero)
        pad = _padrao_citacao(norma.ref)
        for n in self.db.recentes(desde):
            if n.chave == norma.chave or not n.ementa or not pad.search(n.ementa):
                continue
            ref = n.ref
            pad_arq = _padrao_arquivo(ref)
            citada = bool(pad_arq and any(pad_arq.match(a) for a in doc.arquivos_citados)) \
                or bool(_padrao_citacao(ref).search(texto))
            if not citada and not any(p["norma"] == n.nome for p in pendentes):
                recentes.append({"norma": n.nome, "data": n.data, "ementa": n.ementa})
        res["alteracoes_conhecidas"] = conferidas
        res["ultima_alteracao_conhecida"] = ultima
        res["nao_refletidas_no_texto"] = sorted(pendentes, key=lambda p: p["data"] or "", reverse=True)
        res["normas_recentes_que_citam_na_ementa"] = recentes
        if mps_antigas:
            res["mps_antigas_nao_citadas"] = mps_antigas
            res["nota_mps"] = ("Alterações provisórias de MPs com mais de 180 dias não indicam desatualização: "
                               "a MP caducou (e o texto voltou) ou foi convertida em lei (e a lei aparece no texto).")
        if pendentes or recentes:
            res["conclusao"] = (
                "ATENÇÃO: há normas que alteram esta e não aparecem no texto do Planalto. "
                "O texto compilado pode estar desatualizado nesses pontos; confira as normas listadas."
            )
        else:
            res["conclusao"] = (
                f"O texto do Planalto menciona todas as {conferidas - len(mps_antigas)} normas alteradoras "
                f"relevantes de {norma.nome} (sinal de que o compilado está atualizado)."
            )
        res["observacao"] = (
            "Checagem heurística: procura cada norma alteradora (Senado) nos links e notas do texto compilado. "
            f"Normas de {num} citadas só de passagem contam como refletidas."
        )
        return res

    # ================================================================ busca
    def buscar(self, consulta: str, tipos: list[str] | None = None, ano_inicio: int | None = None,
               ano_fim: int | None = None, limite: int = 20, pagina: int = 1) -> dict:
        tipos = [t.upper() for t in tipos] if tipos else None
        res, total = self.db.buscar(consulta, tipos, ano_inicio, ano_fim, limite, (pagina - 1) * limite)
        out = {
            "consulta": consulta,
            "total": total,
            "pagina": pagina,
            "resultados": [n.resumo() for n, _ in res],
        }
        est = self.db.estatisticas()
        if est["total_normas"] < 1000:
            out["aviso"] = (
                "O catálogo local ainda está pequeno; a busca pode estar incompleta. "
                "Rode a ferramenta sincronizar_catalogo (ou `mcp-planalto sincronizar`)."
            )
        elif est["normas_com_detalhe_senado"] < est["total_normas"] / 3:
            out["observacao"] = (
                "A busca cobre ementas e apelidos de todo o catálogo, mas a indexação temática do Senado "
                f"só foi baixada para {est['normas_com_detalhe_senado']} normas. "
                "Para cobertura máxima: `mcp-planalto sincronizar --detalhes`."
            )
        return out

    # ================================================================ novidades
    def atualizar_recentes(self, max_idade: float = 3600) -> int:
        """Relê os quadros do ano corrente (e do anterior, em janeiro) de todos os tipos."""
        hoje = dt.date.today()
        ano_ini = hoje.year - 1 if hoje.month == 1 else hoje.year
        n = 0
        for tipo in TIPOS_CATALOGO:
            if tipo == "DEL" or tipo == "LDL":
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
        self.atualizar_recentes()
        desde = desde or (dt.date.today() - dt.timedelta(days=dias)).isoformat()
        tipos = [t.upper() for t in tipos] if tipos else None
        normas = self.db.recentes(desde, tipos)
        filtro = None
        if tema or termos:
            filtro = self._filtro_tematico(tema, termos or [])
        itens = []
        for n in normas:
            item = n.resumo()
            if filtro:
                motivo = filtro(n)
                if not motivo:
                    continue
                item["motivo"] = motivo
            itens.append(item)
        return {
            "desde": desde,
            "total": len(itens),
            "normas": itens[:limite],
            "fonte": "Quadros de legislação do Planalto (relidos agora)",
        }

    def _filtro_tematico(self, tema: str | None, termos: list[str]) -> Callable[[Norma], str | None]:
        t = localizar_tema(tema) if tema else None
        consultas = list(t.termos if t else ([tema] if tema else [])) + termos
        nucleo = set()
        if t:
            for c in t.nucleo:
                try:
                    nucleo.add(interpretar(c).chave)
                except ValueError:
                    pass
        exprs = [(c, _consulta_regex(c)) for c in consultas]

        def f(n: Norma) -> str | None:
            texto = normalizar(" ".join(x for x in (n.ementa, n.apelido, n.indexacao) if x))
            for c, rx in exprs:
                if rx(texto):
                    return f"ementa/indexação contém {c}"
            if nucleo:
                if n.chave in nucleo:
                    return "norma-núcleo do tema"
                det = self.detalhe(n) if n.ano else None
                if det:
                    alvo = {r.destino for r in det.relacoes_feitas} & nucleo
                    if alvo:
                        return "altera " + ", ".join(sorted(self._nome(a) for a in alvo))
                # ementa "Altera a Lei nº 9.430, de 1996..."
                for k in nucleo:
                    if _padrao_citacao(Referencia.de_chave(k)).search(n.ementa or ""):
                        return f"ementa cita {self._nome(k)}"
            return None

        return f

    # ================================================================ mapeamento temático
    def mapear_tema(self, tema: str, termos_extras: list[str] | None = None,
                    normas_extras: list[str] | None = None, tipos: list[str] | None = None,
                    ano_inicio: int | None = None, ano_fim: int | None = None,
                    seguir_alteracoes: bool = True, profundidade: int = 1, limite_busca: int = 400,
                    agrupar_reedicoes: bool = True,
                    progresso: Callable[[str], None] | None = None) -> dict:
        """Monta o acervo de normas de um tema combinando três fontes de evidência:

        1. normas-núcleo do tema (curadas ou informadas);
        2. normas que alteraram, revogaram ou regulamentaram o núcleo (grafo do Senado);
        3. normas cuja ementa, apelido ou indexação do Senado batem com os termos do tema.
        """
        aviso = progresso or (lambda _m: None)
        t: Tema | None = localizar_tema(tema)
        termos = list(t.termos) if t else [tema]
        termos += termos_extras or []
        nucleo_txt = list(t.nucleo) if t else []
        nucleo_txt += normas_extras or []
        tipos = [x.upper() for x in tipos] if tipos else None

        achados: dict[str, dict] = {}

        def marcar(chave: str, pontos: float, motivo: str) -> None:
            a = achados.setdefault(chave, {"pontos": 0.0, "motivos": []})
            a["pontos"] += pontos
            if motivo not in a["motivos"]:
                a["motivos"].append(motivo)

        # 1. núcleo
        nucleo: list[Norma] = []
        nao_resolvidas = []
        for c in nucleo_txt:
            try:
                n = self.resolver(c)
                nucleo.append(n)
                marcar(n.chave, 100, "norma-núcleo do tema")
            except (NormaNaoEncontrada, ValueError, ErroHTTP) as e:
                nao_resolvidas.append(f"{c}: {e}")
        aviso(f"Núcleo: {len(nucleo)} normas")

        # 2. grafo de alterações
        if seguir_alteracoes and nucleo:
            fronteira = nucleo
            vistos = {n.chave for n in nucleo}
            for nivel in range(1, max(1, profundidade) + 1):
                proxima: dict[str, int] = collections.Counter()
                with ThreadPoolExecutor(max_workers=4) as ex:
                    futuros = {ex.submit(self.detalhe, n): n for n in fronteira}
                    for fut in as_completed(futuros):
                        alvo = futuros[fut]
                        try:
                            det = fut.result()
                        except Exception as e:  # rede
                            log.warning("Falha no detalhe de %s: %s", alvo.nome, e)
                            continue
                        if not det:
                            continue
                        por_origem: dict[str, list[Relacao]] = collections.defaultdict(list)
                        for r in det.relacoes_recebidas:
                            por_origem[r.origem].append(r)
                        for origem, rels in por_origem.items():
                            decl = normalizar(" ".join({r.declaracao for r in rels}))
                            acoes = {normalizar(r.acao) for r in rels}
                            ndisp = len({r.dispositivo for r in rels})
                            if "regulament" in decl or any("regulament" in a for a in acoes):
                                pts, rot = 45, f"regulamenta {alvo.nome}"
                            elif "correlata" in decl or "citada" in decl:
                                pts, rot = 15, f"legislação correlata a {alvo.nome}"
                            elif all("vetad" in a or "veto" in a for a in acoes if a):
                                pts, rot = 10, f"dispositivos vetados sobre {alvo.nome}"
                            else:
                                pts = 40 + 4 * min(ndisp, 10)
                                rot = f"altera/revoga {ndisp} dispositivo(s) de {alvo.nome}"
                            if nivel > 1:
                                pts *= 0.5
                            marcar(origem, pts, rot)
                            if origem not in vistos:
                                proxima[origem] += ndisp
                aviso(f"Grafo nível {nivel}: {len(achados)} normas")
                if nivel >= profundidade:
                    break
                # no nível seguinte, só as alteradoras mais relevantes (evita explosão)
                fronteira = []
                for chave, ndisp in proxima.most_common(40):
                    if ndisp < 3:
                        break
                    n = self.db.obter(chave) or self._norma_de_chave(chave)
                    if n:
                        fronteira.append(n)
                        vistos.add(chave)
                if not fronteira:
                    break

        # 3. catálogo (ementa, apelido, indexação)
        for termo in termos:
            try:
                res, _total = self.db.buscar(termo, tipos, ano_inicio, ano_fim, limite_busca)
            except Exception as e:  # expressão FTS inválida
                log.warning("Termo ignorado (%s): %s", termo, e)
                continue
            for n, score in res:
                marcar(n.chave, 20 + min(score, 15) * 2, f"ementa/indexação: {termo}")
        aviso(f"Catálogo: {len(achados)} normas")

        # MPs reeditadas (antes de 2001): agrupa a família na última edição
        if agrupar_reedicoes:
            achados = self._agrupar_reedicoes(achados)
        self._completar_metadados(list(achados))

        # Montagem
        linhas = []
        for chave, a in achados.items():
            n = self.db.obter(chave) or self._norma_de_chave(chave)
            if not n:
                continue
            if tipos and n.tipo not in tipos:
                continue
            if ano_inicio and (n.ano or 0) < ano_inicio:
                continue
            if ano_fim and (n.ano or 9999) > ano_fim:
                continue
            linhas.append({
                "norma": n.nome, "chave": n.chave, "tipo": TIPOS[n.tipo].nome, "data": n.data,
                "ementa": n.ementa, "apelido": n.apelido, "situacao": n.situacao,
                "relevancia": round(a["pontos"], 1), "motivos": a["motivos"], "url": n.url_planalto,
            })
        linhas.sort(key=lambda x: (-x["relevancia"], x["data"] or ""))
        for i, l in enumerate(linhas, 1):
            l["posicao"] = i
            l["camada"] = (
                "núcleo" if any("núcleo" in m for m in l["motivos"])
                else "alteradora/regulamentadora" if any(m.startswith(("altera", "regulamenta")) for m in l["motivos"])
                else "relacionada"
            )
        est = self.db.estatisticas()
        return {
            "tema": t.nome if t else tema,
            "termos_usados": termos,
            "nucleo": [n.nome for n in nucleo],
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

    def _agrupar_reedicoes(self, achados: dict[str, dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for chave, a in achados.items():
            destino = chave
            ref = Referencia.de_chave(chave) if chave.startswith("MPV:") else None
            # Reedições só existiram até a EC 32/2001; depois dela a numeração recomeçou do 1.
            if ref and ref.ano and (ref.ano < 2001 or (ref.ano == 2001 and int(ref.numero.split("-")[0]) >= 1000)):
                destino = self.db.familia_mp(ref.numero) or chave
                if destino == chave and "-" in ref.numero:
                    # família desconhecida: agrupa pelo número-base (ex.: 2.132-41 ... 2.132-46)
                    base = ref.numero.split("-")[0]
                    irmas = [k for k in achados if k.startswith(f"MPV:{base}-")]
                    destino = max(irmas, key=lambda k: int(k.split(":")[1].split("-")[1]))
            g = out.setdefault(destino, {"pontos": 0.0, "motivos": [], "edicoes": set()})
            g["pontos"] = max(g["pontos"], a["pontos"])
            for m in a["motivos"]:
                if m not in g["motivos"]:
                    g["motivos"].append(m)
            if destino != chave:
                g["edicoes"].add(Referencia.de_chave(chave).nome)
        for g in out.values():
            if g["edicoes"]:
                g["motivos"].append(f"agrupa {len(g['edicoes'])} reedição(ões) anteriores")
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
                    self._gravar_detalhe(det)

    def _norma_de_chave(self, chave: str) -> Norma | None:
        try:
            ref = Referencia.de_chave(chave)
        except Exception:
            return None
        n = Norma(chave=ref.chave, tipo=ref.tipo, numero=ref.numero, ano=ref.ano)
        return n

    # ================================================================ sincronização
    def sincronizar_catalogo(self, tipos: list[str] | None = None, ano_inicio: int | None = None,
                             senado: bool = False, progresso: Callable[[str], None] | None = None) -> dict:
        """Lê os quadros do Planalto (e, opcionalmente, as listas do Senado) para o catálogo local."""
        aviso = progresso or (lambda _m: None)
        tipos = [t.upper() for t in tipos] if tipos else TIPOS_CATALOGO
        self.sincronizando.set()
        try:
            resumo = {}
            for tipo in tipos:
                es = self.indices.entradas(tipo, ano_inicio=ano_inicio, max_idade=12 * 3600)
                resumo[tipo] = self._salvar_entradas(es)
                aviso(f"Planalto {tipo}: {resumo[tipo]} normas")
            if senado:
                ano0 = ano_inicio or 1988
                for tipo in tipos:
                    total = 0
                    for ano in range(ano0, dt.date.today().year + 1):
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
            self.db.set_meta("catalogo_em", agora())
            return {"normas_por_tipo": resumo, **self.db.estatisticas()}
        finally:
            self.sincronizando.clear()

    def sincronizar_detalhes(self, tipos: list[str] | None = None, ano_inicio: int | None = None,
                             limite: int | None = None, paralelo: int = 4,
                             progresso: Callable[[str], None] | None = None) -> dict:
        """Baixa do Senado indexação e vides das normas do catálogo ainda sem detalhe (retomável)."""
        aviso = progresso or (lambda _m: None)
        tipos = [t.upper() for t in tipos] if tipos else TIPOS_DETALHE_PADRAO
        pendentes = [n for n in self.db.iterar(tipos, ano_inicio, sem_detalhe=True) if n.ano]
        if limite:
            pendentes = pendentes[:limite]
        ok = falhas = vazios = 0
        inicio = time.time()

        def um(n: Norma):
            return n, self.senado.detalhe(n.ref, max_idade=30 * 24 * 3600)

        with ThreadPoolExecutor(max_workers=paralelo) as ex:
            futuros = [ex.submit(um, n) for n in pendentes]
            for i, fut in enumerate(as_completed(futuros), 1):
                try:
                    n, det = fut.result()
                except Exception as e:
                    falhas += 1
                    log.info("Falha no detalhe: %s", e)
                    continue
                if det:
                    self._gravar_detalhe(det)
                    ok += 1
                else:
                    vazios += 1
                    n.detalhe_em = agora()
                    self.db.salvar([Norma(chave=n.chave, tipo=n.tipo, numero=n.numero, ano=n.ano,
                                          detalhe_em=n.detalhe_em)])
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
            return dt.datetime.fromtimestamp(float(v)).isoformat(timespec="minutes") if v else None

        est.update({
            "catalogo_sincronizado_em": quando("catalogo_em"),
            "recentes_atualizados_em": quando("recentes_em"),
            "detalhes_sincronizados_em": quando("detalhes_em"),
            "sincronizando_agora": self.sincronizando.is_set(),
            "diretorio_de_dados": str(self.config.home),
        })
        return est


def _consulta_regex(consulta: str) -> Callable[[str], bool]:
    """Avalia uma consulta no formato do buscador (aspas, OU) sobre texto normalizado."""
    grupos: list[list[str]] = [[]]
    for m in re.finditer(r'"([^"]+)"|(\S+)', consulta):
        if m.group(2) and m.group(2).upper() in ("OU", "OR", "|"):
            grupos.append([])
            continue
        termo = normalizar(m.group(1) or m.group(2)).rstrip("*")
        if termo:
            grupos[-1].append(termo)
    pads = [[re.compile(r"\b" + re.escape(t)) for t in g] for g in grupos if g]
    return lambda texto: any(all(p.search(texto) for p in g) for g in pads)
