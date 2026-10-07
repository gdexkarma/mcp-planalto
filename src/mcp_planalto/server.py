"""Servidor MCP: ferramentas de pesquisa e leitura da legislação federal."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Literal

try:  # SDK mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Servidor
except ImportError:  # SDK mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Servidor

from .exportar import exportar
from .servico import Legislacao, NormaNaoEncontrada
from .temas import TEMAS

log = logging.getLogger(__name__)

INSTRUCOES = """\
Pesquisa e leitura da legislação federal brasileira, sempre na versão mais recente.
Fontes: texto compilado do Planalto (planalto.gov.br/ccivil_03) e Dados Abertos do Senado
(indexação temática e histórico de alterações de cada dispositivo).

Fluxo recomendado:
- Para ler uma norma: ler_norma("Lei 9.430/1996", dispositivo="art. 74"). Aceita citações
  usuais: "LC 214/2025", "Decreto 9.580/2018", "RIR/2018", "CTN", "MP 2.158-35/2001", "DL 1.598/77".
  Normas grandes: use estrutura_norma primeiro ou os parâmetros dispositivo/termo.
- Para saber quem alterou um dispositivo: historico_alteracoes(..., dispositivo="art. 2º").
- Antes de afirmar que um texto está vigente e atualizado: verificar_atualizacao(...).
- Para levantar o acervo de um tema (ex.: IRPJ): mapear_tema("IRPJ"); gera planilha.
- Para acompanhar publicações: novidades_legislativas(dias=7, tema="IRPJ").
Cite sempre a norma e o dispositivo. O texto do Planalto não substitui o publicado no DOU.
"""

_servico: Legislacao | None = None
_lock = threading.Lock()


def servico() -> Legislacao:
    global _servico
    with _lock:
        if _servico is None:
            _servico = Legislacao()
        return _servico


def _erro(e: Exception) -> dict:
    out = {"erro": str(e)}
    if isinstance(e, NormaNaoEncontrada) and e.candidatos:
        out["candidatos"] = [c.resumo() for c in e.candidatos]
    return out


async def _rodar(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except (NormaNaoEncontrada, ValueError) as e:
        return _erro(e)


mcp = _Servidor("planalto", instructions=INSTRUCOES)


@mcp.tool()
async def ler_norma(
    referencia: str,
    dispositivo: str | None = None,
    termo: str | None = None,
    modo: Literal["vigente", "historico"] = "vigente",
    incluir_notas: bool = True,
    omitir_revogados: bool = False,
    pagina: int = 1,
    max_caracteres: int = 40000,
) -> str:
    """Lê o texto atualizado (compilado) de uma norma federal direto do Planalto.

    Args:
        referencia: citação da norma: "Lei 9.430/1996", "Lei nº 12.973, de 2014", "LC 214/2025",
            "Decreto 9.580/2018", "RIR/2018", "CTN", "MP 2.158-35/2001", "DL 1.598/1977", "EC 132/2023", "CF".
        dispositivo: trecho a ler: "art. 74", "arts. 15 a 20", "art. 2º, § 4º, III", "art. 10-A".
            Vários separados por ";". Sem isto, devolve a norma inteira (paginada).
        termo: devolve só os artigos que contêm estas palavras (ex.: "juros sobre o capital próprio").
        modo: "vigente" (só a redação atual) ou "historico" (inclui redações anteriores, marcadas ~~assim~~).
        incluir_notas: mantém notas como "(Redação dada pela Lei nº 12.973, de 2014)".
        omitir_revogados: oculta dispositivos revogados (por padrão aparecem como "§ 3º (Revogado pela ...)").
        pagina: página do texto, quando ele excede max_caracteres.
        max_caracteres: tamanho máximo de cada página.
    """
    r = await _rodar(servico().texto, referencia, dispositivo=dispositivo, termo=termo, modo=modo,
                     notas=incluir_notas, omitir_revogados=omitir_revogados, pagina=pagina,
                     max_caracteres=max_caracteres)
    if "erro" in r:
        msg = f"Erro: {r['erro']}"
        if r.get("candidatos"):
            msg += "\nCandidatos: " + "; ".join(f"{c['norma']} ({c.get('data')}): {c.get('ementa', '')[:80]}"
                                                for c in r["candidatos"])
        return msg
    cab = [f"# {r['norma']}"]
    if r.get("epigrafe"):
        cab.append(r["epigrafe"])
    if r.get("ementa"):
        cab.append(f"Ementa: {r['ementa']}")
    if r.get("situacao"):
        cab.append(f"Situação: {r['situacao']}")
    cab.append(f"Fonte: {r['url']}" + (f" (página atualizada no Planalto em {r['planalto_atualizado_em']})"
                                       if r.get("planalto_atualizado_em") else ""))
    if r.get("notas_gerais"):
        cab.append("Notas: " + " · ".join(r["notas_gerais"]))
    if r.get("artigos_com_o_termo") is not None:
        cab.append(f"Artigos com o termo: {r['artigos_com_o_termo']}")
    if r.get("legenda"):
        cab.append(r["legenda"])
    if r.get("aviso"):
        cab.append(f"Aviso: {r['aviso']}")
    if r.get("total_paginas", 1) > 1:
        cab.append(f"Página {r['pagina']} de {r['total_paginas']} (use pagina=N para continuar).")
    return "\n".join(cab) + ("\n\n" + r["texto"] if r.get("texto") else "")


@mcp.tool()
async def estrutura_norma(referencia: str) -> dict:
    """Sumário de uma norma (livros, títulos, capítulos e seções, com os artigos de cada um).

    Útil antes de ler normas extensas como o RIR/2018, a LC 214/2025 ou o Código Civil.
    """
    return await _rodar(servico().texto, referencia, estrutura=True)


@mcp.tool()
async def consultar_norma(referencia: str) -> dict:
    """Ficha da norma: ementa, data, apelido, situação (ex.: revogada), link do texto, URN LexML,
    publicação no DOU, indexação temática do Senado, quantas normas a alteraram e quem a regulamenta."""
    return await _rodar(servico().ficha, referencia)


@mcp.tool()
async def buscar_normas(
    consulta: str,
    tipos: list[str] | None = None,
    ano_inicio: int | None = None,
    ano_fim: int | None = None,
    limite: int = 20,
    pagina: int = 1,
) -> dict:
    """Busca normas no catálogo local (ementa, apelido e indexação temática do Senado), sem acento/caixa.

    Sintaxe: palavras são combinadas com E; "frase exata" entre aspas; OU para alternativas;
    prefixo com * (tribut*). Ex.: '"lucro presumido"', 'CSLL OU "contribuição social sobre o lucro"'.

    Args:
        tipos: filtra por tipo: LEI, LCP (lei complementar), DEC, DEL (decreto-lei), MPV, EMC, LDL.
        ano_inicio, ano_fim: intervalo de anos de assinatura.
    """
    return await _rodar(servico().buscar, consulta, tipos, ano_inicio, ano_fim, limite, pagina)


@mcp.tool()
async def historico_alteracoes(
    referencia: str,
    dispositivo: str | None = None,
    desde: str | None = None,
    direcao: Literal["recebidas", "feitas"] = "recebidas",
    incluir_correlatas: bool = False,
) -> dict:
    """Histórico de alterações de uma norma ou de um dispositivo (dados do Senado Federal).

    Args:
        dispositivo: restringe a um dispositivo ("art. 74", "art. 2º, § 5º"); inclui os subdispositivos.
        desde: só alterações a partir desta data (AAAA-MM-DD).
        direcao: "recebidas" = normas que alteraram/revogaram/regulamentaram esta;
            "feitas" = normas anteriores que esta alterou ou revogou.
        incluir_correlatas: inclui "legislação correlata/citada", que não altera o texto.
    """
    return await _rodar(servico().historico, referencia, dispositivo, desde, direcao, incluir_correlatas)


@mcp.tool()
async def verificar_atualizacao(referencia: str) -> dict:
    """Confere se o texto compilado do Planalto já incorpora todas as alterações conhecidas.

    Cruza as normas alteradoras registradas pelo Senado, e as normas recentes cuja ementa cita esta,
    com os links e notas do texto do Planalto. Use antes de afirmar que uma redação está atualizada.
    """
    return await _rodar(servico().verificar_atualizacao, referencia)


@mcp.tool()
async def novidades_legislativas(
    dias: int = 30,
    desde: str | None = None,
    tipos: list[str] | None = None,
    tema: str | None = None,
    termos: list[str] | None = None,
    limite: int = 100,
) -> dict:
    """Normas federais publicadas recentemente (relê os quadros do Planalto na hora).

    Args:
        dias: janela em dias (ignorado se `desde` for informado).
        desde: data inicial AAAA-MM-DD.
        tipos: LEI, LCP, DEC, MPV, EMC.
        tema: filtra por tema (ex.: "IRPJ", "PIS/COFINS", "IBS/CBS"; veja listar_temas). Inclui normas de
            ementa genérica que alteram as normas-núcleo do tema.
        termos: termos adicionais de filtro na ementa.
    """
    return await _rodar(servico().novidades, desde, dias, tipos, tema, termos, limite)


@mcp.tool()
async def mapear_tema(
    tema: str,
    termos_extras: list[str] | None = None,
    normas_extras: list[str] | None = None,
    tipos: list[str] | None = None,
    ano_inicio: int | None = None,
    ano_fim: int | None = None,
    profundidade: int = 1,
    exportar_como: Literal["xlsx", "csv", "nenhum"] = "xlsx",
    max_resultados: int = 60,
) -> dict:
    """Mapeia o acervo de legislação federal de um tema (ex.: "IRPJ") e exporta para planilha.

    Combina: (1) normas-núcleo do tema; (2) todas as normas que alteraram, revogaram ou
    regulamentaram o núcleo, pelo grafo de alterações do Senado (pega leis de ementa genérica
    como "Altera a Lei nº 9.430..."); (3) normas cuja ementa, apelido ou indexação batem com os
    termos do tema. Cada norma vem com relevância e o motivo de ter entrado.

    Args:
        tema: tema pré-configurado (veja listar_temas: IRPJ, CSLL, PIS/COFINS, IRPF, IRRF, IPI, IOF,
            SIMPLES, IBS/CBS, PRECOS DE TRANSFERENCIA, TRIBUTACAO INTERNACIONAL, PREVIDENCIARIAS,
            PROCESSO FISCAL, CTN, ICMS/ISS) ou texto livre.
        termos_extras: termos adicionais de busca (mesma sintaxe de buscar_normas).
        normas_extras: normas adicionais para o núcleo (ex.: ["Lei 14.754/2023"]).
        profundidade: 2 também segue quem alterou as principais alteradoras (mais amplo, mais lento).
        exportar_como: gera planilha com a lista completa (o retorno traz só as primeiras normas).
        max_resultados: quantas normas incluir na resposta.
    """
    s = servico()
    r = await _rodar(s.mapear_tema, tema, termos_extras, normas_extras, tipos, ano_inicio, ano_fim,
                     True, profundidade)
    if "erro" in r:
        return r
    if exportar_como != "nenhum" and r["normas"]:
        notas = [
            f"Tema: {r['tema']}",
            "Termos: " + " | ".join(r["termos_usados"]),
            "Núcleo: " + "; ".join(r["nucleo"]),
            "Camadas: núcleo (normas estruturantes), alteradora/regulamentadora (grafo de alterações do "
            "Senado) e relacionada (ementa, apelido ou indexação contém termos do tema).",
            "Relevância: soma de pontos das evidências; serve para ordenar, não é juízo jurídico.",
            f"Cobertura do catálogo: {r['cobertura']}",
        ]
        caminho = await asyncio.to_thread(
            exportar, r["normas"], s.config.export_dir, f"acervo-{r['tema']}", exportar_como,
            None, f"Acervo de legislação federal: {r['tema']}", notas)
        r["arquivo_exportado"] = str(caminho)
    r["normas"] = r["normas"][:max_resultados]
    if r["total"] > max_resultados:
        r["observacao"] = f"Mostrando {max_resultados} de {r['total']}; a lista completa está na planilha."
    return r


@mcp.tool()
async def listar_temas() -> dict:
    """Lista os temas tributários pré-configurados para mapear_tema e novidades_legislativas."""
    return {
        t.sigla: {"nome": t.nome, "normas_nucleo": list(t.nucleo), "termos": list(t.termos)}
        for t in TEMAS.values()
    }


@mcp.tool()
async def status_indice() -> dict:
    """Estado do catálogo local: quantas normas, quando foi sincronizado e se há sincronização em curso."""
    return await asyncio.to_thread(servico().status)


@mcp.tool()
async def sincronizar_catalogo(
    detalhes_senado: bool = False,
    tipos: list[str] | None = None,
    ano_inicio: int | None = None,
    limite_detalhes: int = 2000,
) -> dict:
    """Atualiza o catálogo local em segundo plano (retorna na hora; acompanhe com status_indice).

    Args:
        detalhes_senado: também baixa indexação temática e alterações do Senado das normas ainda sem
            esses dados (melhora busca e mapeamento; demora: ~1 s por norma).
        tipos: LEI, LCP, DEC, DEL, MPV, EMC, LDL (padrão: todos).
        ano_inicio: só normas a partir deste ano.
        limite_detalhes: máximo de normas a detalhar nesta rodada (a sincronização é retomável).
    """
    s = servico()
    if s.sincronizando.is_set():
        return {"status": "já existe uma sincronização em andamento", **s.status()}

    def rodar():
        try:
            s.sincronizar_catalogo(tipos, ano_inicio)
            if detalhes_senado:
                s.sincronizando.set()
                try:
                    s.sincronizar_detalhes(tipos, ano_inicio, limite_detalhes)
                finally:
                    s.sincronizando.clear()
        except Exception:
            log.exception("Falha na sincronização")

    threading.Thread(target=rodar, daemon=True, name="sincronizacao").start()
    return {"status": "sincronização iniciada em segundo plano", **s.status()}


def _auto_sincronizar() -> None:
    """Ao iniciar: monta o catálogo se estiver vazio; senão, relê os quadros do ano corrente."""
    s = servico()
    if not s.config.auto_sync:
        return
    try:
        if s.db.estatisticas()["total_normas"] < 1000:
            log.info("Catálogo vazio: sincronizando quadros do Planalto (alguns minutos)...")
            s.sincronizar_catalogo()
        else:
            ultimo = float(s.db.meta("recentes_em", "0"))
            if time.time() - ultimo > 6 * 3600:
                s.atualizar_recentes()
    except Exception:
        log.exception("Falha na sincronização automática")


def main(transporte: str = "stdio", host: str = "127.0.0.1", porta: int = 8000) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    threading.Thread(target=_auto_sincronizar, daemon=True, name="auto-sync").start()
    if transporte == "stdio":
        mcp.run("stdio")
    else:
        try:
            mcp.run("streamable-http", host=host, port=porta)
        except TypeError:  # SDK 1.x: host/porta vêm das settings
            mcp.settings.host = host
            mcp.settings.port = porta
            mcp.run("streamable-http")
