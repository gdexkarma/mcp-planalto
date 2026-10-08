"""Servidor MCP: ferramentas de pesquisa e leitura da legislação federal."""

import asyncio
import logging
import threading
import time
from typing import Annotated, Literal

from pydantic import Field

try:  # SDK mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Servidor
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp_types import ToolAnnotations
except ImportError:  # SDK mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Servidor
    from mcp.server.fastmcp.exceptions import ToolError
    from mcp.types import ToolAnnotations

from . import __version__
from .exportar import exportar
from .http import ErroHTTP
from .servico import Legislacao, NormaNaoEncontrada, normalizar_tipos
from .temas import TEMAS

log = logging.getLogger(__name__)

INSTRUCOES = """\
Pesquisa e leitura da legislação federal brasileira, sempre na versão mais recente.
Fontes: texto compilado do Planalto (planalto.gov.br/ccivil_03) e Dados Abertos do Senado
(indexação temática e histórico de alterações de cada dispositivo).

Fluxo recomendado:
- Para ler uma norma: ler_norma("Lei 9.430/1996", dispositivo="art. 74"). Aceita citações
  usuais: "LC 214/2025", "Decreto 9.580/2018", "RIR/2018", "CTN", "MP 2.158-35/2001", "DL 1.598/77",
  e também "art. 74 da Lei 9.430/96". Normas grandes: use estrutura_norma primeiro ou dispositivo/termo.
- Para saber quem alterou um dispositivo: historico_alteracoes(..., dispositivo="art. 2º").
- Antes de afirmar que um texto está vigente e atualizado: verificar_atualizacao(...).
- Para levantar o acervo de um tema (ex.: IRPJ): mapear_tema("IRPJ"); gera planilha.
- Para acompanhar publicações: novidades_legislativas(dias=7, tema="IRPJ").
Cite sempre a norma e o dispositivo. O texto do Planalto não substitui o publicado no DOU.
"""

_instancia: "Legislacao | None" = None
_lock = threading.Lock()
_sync_ativa = threading.Event()

LEITURA = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

Referencia = Annotated[str, Field(
    min_length=1, max_length=300,
    description='Citação da norma: "Lei 9.430/1996", "Lei nº 12.973, de 2014", "LC 214/2025", "Decreto 9.580/2018", '
                '"RIR/2018", "CTN", "CF", "MP 2.158-35/2001", "DL 1.598/1977", "EC 132/2023".')]
Tipos = Annotated[list[str] | None, Field(
    description="Filtra por tipo: LEI, LC, DECRETO, DL (decreto-lei), MP, EC, LDL (lei delegada).")]
Data = Annotated[str | None, Field(description="Data inicial: AAAA-MM-DD ou DD/MM/AAAA.")]


def servico() -> Legislacao:
    global _instancia
    with _lock:
        if _instancia is None:
            _instancia = Legislacao()
        return _instancia


def _mensagem(e: Exception) -> str:
    msg = str(e)
    if isinstance(e, NormaNaoEncontrada) and e.candidatos:
        msg += "\nCandidatos: " + "; ".join(
            f"{c.nome} ({c.data or 's/ data'}): {(c.ementa or '')[:80]}" for c in e.candidatos)
    return msg


async def _rodar(fn, *args, **kwargs):
    """Executa fora do laço de eventos; erros viram ToolError (o cliente recebe isError e a mensagem)."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except (NormaNaoEncontrada, ValueError, RuntimeError) as e:
        raise ToolError(_mensagem(e)) from e
    except ErroHTTP as e:
        raise ToolError(f"Portal indisponível no momento ({e}). Tente novamente em alguns minutos.") from e
    except OSError as e:
        raise ToolError(f"Erro de arquivo/sistema: {e}. Confira MCP_PLANALTO_HOME.") from e
    except Exception as e:  # nunca derruba a sessão MCP
        log.exception("Erro inesperado em %s", getattr(fn, "__name__", fn))
        raise ToolError(f"Erro inesperado: {type(e).__name__}: {e}") from e


async def _servico() -> Legislacao:
    return await _rodar(servico)


try:
    mcp = _Servidor("planalto", instructions=INSTRUCOES, version=__version__)
except TypeError:  # SDK 1.x sem `version`
    mcp = _Servidor("planalto", instructions=INSTRUCOES)


def _ferramenta(**kw):
    """@mcp.tool() tolerante a versões do SDK sem alguns parâmetros."""
    def deco(fn):
        try:
            return mcp.tool(**kw)(fn)
        except TypeError:
            kw.pop("structured_output", None)
            try:
                return mcp.tool(**kw)(fn)
            except TypeError:
                kw.pop("annotations", None)
                return mcp.tool(**kw)(fn)
    return deco


@_ferramenta(annotations=LEITURA, structured_output=False)
async def ler_norma(
    referencia: Referencia,
    dispositivo: Annotated[str | None, Field(max_length=300, description=(
        'Trecho a ler: "art. 74", "arts. 15 a 20", "arts. 74 e 80", "art. 2º, § 4º, III", "§ 1º do art. 44", '
        '"art. 44, caput", "art. 10-A", "art. 76 do ADCT", "anexo I". Em decretos que aprovam regulamento '
        '(RIR, RPS, CLT) "art. N" é do regulamento e "decreto, art. N" do decreto. Vários separados por ";".'
    ))] = None,
    termo: Annotated[str | None, Field(max_length=200,
                                       description="Devolve só os artigos/anexos que contêm estas palavras.")] = None,
    modo: Annotated[Literal["vigente", "historico"], Field(
        description='"vigente": só a redação atual; "historico": inclui redações anteriores (~~riscadas~~).')] = "vigente",
    incluir_notas: Annotated[bool, Field(
        description='Mantém notas como "(Redação dada pela Lei nº 12.973, de 2014)".')] = True,
    omitir_revogados: Annotated[bool, Field(description="Oculta dispositivos revogados.")] = False,
    pagina: Annotated[int, Field(ge=1, le=10000, description="Página, quando o texto excede max_caracteres.")] = 1,
    max_caracteres: Annotated[int, Field(ge=2000, le=150000, description="Tamanho máximo de cada página.")] = 40000,
) -> str:
    """Lê o texto atualizado (compilado) de uma norma federal direto do Planalto, inteiro ou por dispositivo.

    Também aceita o dispositivo na própria referência: "art. 74, § 12 da Lei 9.430/96". Ao ler um dispositivo,
    traz as notas de vigência do cabeçalho da norma e um ALERTA quando o Senado registra alteração recente do
    dispositivo que o texto ainda não mostra.
    """
    s = await _servico()
    r = await _rodar(s.texto, referencia, dispositivo=dispositivo, termo=termo, modo=modo,
                     notas=incluir_notas, omitir_revogados=omitir_revogados, pagina=pagina,
                     max_caracteres=max_caracteres)
    cab = [f"# {r['norma']}"]
    if r.get("epigrafe"):
        cab.append(r["epigrafe"])
    if r.get("ementa"):
        cab.append(f"Ementa: {r['ementa']}")
    if r.get("situacao"):
        cab.append(f"Situação: {r['situacao']}")
    cab.append(f"Fonte: {r['url']}" + (f" (página atualizada no Planalto em {r['planalto_atualizado_em']})"
                                       if r.get("planalto_atualizado_em") else ""))
    if r.get("alerta"):
        cab.append(f"ALERTA: {r['alerta']}")
    for k, rot in (("observacao_numeracao", "Numeração"), ("notas_gerais", "Notas"),
                   ("notas_de_vigencia_da_norma", "Notas de vigência da norma (valem para todo o texto)"),
                   ("anexos", "Anexos")):
        if r.get(k):
            v = r[k]
            cab.append(f"{rot}: " + (" · ".join(v) if isinstance(v, list) else v))
    if r.get("artigos_com_o_termo") is not None:
        cab.append(f"Artigos com o termo: {r['artigos_com_o_termo']}" +
                   (f" ({r['observacao']})" if r.get("observacao") else ""))
    if r.get("legenda"):
        cab.append(r["legenda"])
    if r.get("aviso"):
        cab.append(f"Aviso: {r['aviso']}")
    tp = r.get("total_paginas", 1)
    if tp > 1:
        cab.append(f"Página {r['pagina']} de {tp}" + (" (use pagina=N para continuar)." if r["pagina"] < tp else
                                                      " (última página)."))
    return "\n".join(cab) + ("\n\n" + r["texto"] if r.get("texto") else "")


@_ferramenta(annotations=LEITURA)
async def estrutura_norma(referencia: Referencia) -> dict:
    """Sumário de uma norma (livros, títulos, capítulos e seções, com os artigos de cada um) e seus anexos.

    Útil antes de ler normas extensas como o RIR/2018, a LC 214/2025 ou o Código Civil.
    """
    s = await _servico()
    return await _rodar(s.texto, referencia, estrutura=True)


@_ferramenta(annotations=LEITURA)
async def consultar_norma(referencia: Referencia) -> dict:
    """Ficha da norma: ementa, data, apelido, situação (revogada, convertida, sem eficácia), link do texto,
    URN LexML, publicação no DOU, indexação temática do Senado, quantas normas a alteraram e quem a regulamenta."""
    s = await _servico()
    return await _rodar(s.ficha, referencia)


@_ferramenta(annotations=LEITURA)
async def buscar_normas(
    consulta: Annotated[str, Field(min_length=1, max_length=500, description=(
        'Palavras (combinadas com E), "frase exata" entre aspas, OU para alternativas, prefixo*. '
        'Ex.: \'"lucro presumido"\', \'CSLL OU "contribuição social sobre o lucro"\'. Sem acento/caixa. '
        'Uma citação ("Lei 9.430/96") também funciona.'))],
    tipos: Tipos = None,
    ano_inicio: Annotated[int | None, Field(ge=1800, le=2100)] = None,
    ano_fim: Annotated[int | None, Field(ge=1800, le=2100)] = None,
    limite: Annotated[int, Field(ge=1, le=100, description="Resultados por página.")] = 20,
    pagina: Annotated[int, Field(ge=1, le=1000)] = 1,
) -> dict:
    """Busca normas no catálogo local por ementa, apelido e indexação temática do Senado."""
    s = await _servico()
    return await _rodar(s.buscar, consulta, tipos, ano_inicio, ano_fim, limite, pagina)


@_ferramenta(annotations=LEITURA)
async def historico_alteracoes(
    referencia: Referencia,
    dispositivo: Annotated[str | None, Field(max_length=300, description=(
        'Restringe a um dispositivo ("art. 74", "art. 2º, § 5º", "art. 22, caput"); inclui o que ele contém, '
        "registros no nível do artigo e alterações da norma inteira."))] = None,
    desde: Data = None,
    direcao: Annotated[Literal["recebidas", "feitas"], Field(description=(
        '"recebidas": normas que alteraram/revogaram/regulamentaram esta; "feitas": normas que esta alterou.'))]
    = "recebidas",
    incluir_correlatas: Annotated[bool, Field(
        description="Inclui legislação correlata/citada e dispositivos vetados (não alteram o texto).")] = False,
) -> dict:
    """Histórico de alterações de uma norma ou de um dispositivo (dados do Senado Federal)."""
    s = await _servico()
    return await _rodar(s.historico, referencia, dispositivo, desde, direcao, incluir_correlatas)


@_ferramenta(annotations=LEITURA)
async def verificar_atualizacao(referencia: Referencia) -> dict:
    """Confere se o texto compilado do Planalto já incorpora as alterações conhecidas.

    Para cada norma alteradora registrada pelo Senado, procura a nota "(Redação dada/Incluído/Revogado pela …)"
    em cada artigo alterado (acréscimos: o dispositivo existe e não foi incluído por outra norma). Também procura
    leis dos últimos ~13 meses cuja ementa diz alterar a norma. Separa: não refletidas (ATENÇÃO), a conferir (só
    em "Vide", incorporação parcial ou vigência futura), antigas sem nota (informativo) e MPs encerradas. Use
    antes de afirmar que uma redação está atualizada.
    """
    s = await _servico()
    return await _rodar(s.verificar_atualizacao, referencia)


@_ferramenta(annotations=LEITURA)
async def novidades_legislativas(
    dias: Annotated[int, Field(ge=1, le=3660, description="Janela em dias (ignorado se `desde` vier).")] = 30,
    desde: Data = None,
    tipos: Tipos = None,
    tema: Annotated[str | None, Field(description=(
        'Tema: "IRPJ", "CSLL", "PIS/COFINS", "IBS/CBS"... (veja listar_temas; aceita "IRPJ/CSLL"). Inclui normas de '
        "ementa genérica que alteram as normas-núcleo do tema."))] = None,
    termos: Annotated[list[Annotated[str, Field(max_length=200)]] | None, Field(
        max_length=20, description="Termos adicionais de filtro na ementa.")] = None,
    limite: Annotated[int, Field(ge=1, le=500)] = 100,
) -> dict:
    """Normas federais publicadas recentemente (revalida os quadros do Planalto na hora)."""
    s = await _servico()
    return await _rodar(s.novidades, desde, dias, tipos, tema, termos, limite)


@_ferramenta()
async def mapear_tema(
    tema: Annotated[str, Field(min_length=1, max_length=200, description=(
        "Tema pré-configurado (IRPJ, CSLL, PIS/COFINS, IRPF, IRRF, IPI, IOF, SIMPLES, IBS/CBS, PRECOS DE "
        "TRANSFERENCIA, TRIBUTACAO INTERNACIONAL, PREVIDENCIARIAS, PROCESSO FISCAL, CTN, ICMS/ISS), "
        'combinação ("IRPJ/CSLL") ou texto livre.'))],
    termos_extras: Annotated[list[Annotated[str, Field(max_length=200)]] | None, Field(
        max_length=30, description="Termos adicionais (sintaxe de buscar_normas).")] = None,
    normas_extras: Annotated[list[Annotated[str, Field(max_length=300)]] | None, Field(max_length=50, description=(
        'Normas adicionais para o núcleo; aceita escopo: "Lei 9.430/1996, arts. 18 a 24-C".'))] = None,
    tipos: Tipos = None,
    ano_inicio: Annotated[int | None, Field(ge=1800, le=2100)] = None,
    ano_fim: Annotated[int | None, Field(ge=1800, le=2100)] = None,
    profundidade: Annotated[int, Field(ge=1, le=2, description=(
        "2 também segue quem alterou as principais alteradoras (mais amplo e mais lento)."))] = 1,
    exportar_como: Annotated[Literal["xlsx", "csv", "nenhum"], Field(
        description="Gera planilha com a lista completa.")] = "xlsx",
    max_resultados: Annotated[int, Field(ge=1, le=500, description="Normas incluídas na resposta.")] = 60,
) -> dict:
    """Mapeia o acervo de legislação federal de um tema e exporta para planilha.

    Combina: (1) normas-núcleo do tema (algumas com escopo por dispositivo); (2) normas que alteraram, revogaram
    ou regulamentaram o núcleo, pelo grafo de alterações do Senado (pega leis de ementa genérica como "Altera a
    Lei nº 9.430..."); (3) normas cuja ementa, apelido ou indexação batem com os termos do tema. Cada norma vem
    com camada, relevância e o motivo de ter entrado.
    """
    s = await _servico()
    r = await _rodar(s.mapear_tema, tema, termos_extras, normas_extras, tipos, ano_inicio, ano_fim,
                     True, profundidade)
    if exportar_como != "nenhum" and r.get("normas"):
        caminho = await _rodar(exportar, r["normas"], s.config.export_dir, f"acervo-{r['tema']}", exportar_como,
                               None, f"Acervo de legislação federal: {r['tema']}", notas_metodologicas(r))
        r["arquivo_exportado"] = str(caminho)
    r["normas"] = r["normas"][:max_resultados]
    if r["total"] > max_resultados:
        r["observacao"] = f"Mostrando {max_resultados} de {r['total']}; a lista completa está na planilha."
    return r


def notas_metodologicas(r: dict) -> list[str]:
    return [
        f"Tema: {r['tema']}",
        "Termos: " + " | ".join(r["termos_usados"]),
        "Núcleo: " + "; ".join(r["nucleo"]),
        "Camadas: núcleo (normas estruturantes; algumas com escopo por dispositivo), alteradora/"
        "regulamentadora (alterou ou regulamentou o núcleo, pelo grafo do Senado), relacionada (ementa, "
        "apelido ou indexação contém termos do tema) e alteradora de 2º nível.",
        "Relevância: soma de pontos das evidências; ordena dentro da camada, não é juízo jurídico.",
        f"Cobertura do catálogo: {r['cobertura']}",
        *r.get("avisos", []),
    ]


@_ferramenta(annotations=LEITURA)
async def listar_temas() -> dict:
    """Lista os temas tributários pré-configurados para mapear_tema e novidades_legislativas."""
    return {
        t.sigla: {"nome": t.nome, "normas_nucleo": list(t.nucleo), "termos": list(t.termos)}
        for t in TEMAS.values()
    }


@_ferramenta(annotations=LEITURA)
async def status_indice() -> dict:
    """Estado do catálogo local: quantas normas, quando foi sincronizado e se há sincronização em curso."""
    s = await _servico()
    return await _rodar(s.status)


@_ferramenta(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True, openWorldHint=True))
async def sincronizar_catalogo(
    detalhes_senado: Annotated[bool, Field(description=(
        "Também baixa indexação temática e alterações do Senado das normas ainda sem esses dados "
        "(melhora busca e mapeamento; ~0,5 s por norma)."))] = False,
    tipos: Tipos = None,
    ano_inicio: Annotated[int | None, Field(ge=1800, le=2100)] = None,
    limite_detalhes: Annotated[int, Field(ge=1, le=100000, description=(
        "Máximo de normas a detalhar nesta rodada (a sincronização é retomável)."))] = 2000,
) -> dict:
    """Atualiza o catálogo local em segundo plano (retorna na hora; acompanhe com status_indice)."""
    s = await _servico()
    tipos = await _rodar(normalizar_tipos, tipos)
    with _lock:
        if s.sincronizando.is_set() or _sync_ativa.is_set():
            return {"status": "já existe uma sincronização em andamento", **(await _rodar(s.status))}
        _sync_ativa.set()

    def rodar():
        try:
            s.sincronizar_catalogo(tipos, ano_inicio)
            if detalhes_senado:
                s.sincronizar_detalhes(tipos, ano_inicio, limite_detalhes)
        except Exception:
            log.exception("Falha na sincronização")
        finally:
            _sync_ativa.clear()

    threading.Thread(target=rodar, daemon=True, name="sincronizacao").start()
    return {"status": "sincronização iniciada em segundo plano", **(await _rodar(s.status))}


def _auto_sincronizar() -> None:
    """Ao iniciar: monta o catálogo se a carga completa nunca terminou (inclusive se foi interrompida);
    senão, revalida os quadros do ano corrente."""
    s = servico()
    if not s.config.auto_sync:
        return
    with _lock:
        if _sync_ativa.is_set():
            return
        _sync_ativa.set()
    try:
        if not s.db.meta("catalogo_em"):
            log.info("Catálogo incompleto: sincronizando quadros do Planalto (alguns minutos)...")
            s.sincronizar_catalogo()
        else:
            ultimo = float(s.db.meta("recentes_em", "0"))
            if time.time() - ultimo > 6 * 3600:
                s.atualizar_recentes()
    except Exception:
        log.exception("Falha na sincronização automática")
    finally:
        _sync_ativa.clear()


def main(transporte: str = "stdio", host: str = "127.0.0.1", porta: int = 8000, verboso: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verboso else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    threading.Thread(target=_auto_sincronizar, daemon=True, name="auto-sync").start()
    if transporte == "stdio":
        mcp.run("stdio")
        return
    try:
        mcp.run("streamable-http", host=host, port=porta)
    except TypeError:  # SDK 1.x: host/porta vêm das settings, e a proteção de Host é ligada no construtor
        mcp.settings.host = host
        mcp.settings.port = porta
        if host not in ("127.0.0.1", "localhost", "::1"):
            try:
                from mcp.server.transport_security import TransportSecuritySettings

                mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
            except ImportError:
                pass
        mcp.run("streamable-http")
