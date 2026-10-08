"""Ferramentas MCP chamadas diretamente (sem transporte), com o serviço das fixtures."""

import asyncio

import pytest

from mcp_planalto import server


@pytest.fixture
def srv(L, monkeypatch):
    monkeypatch.setattr(server, "_instancia", L)
    return server


def rodar(coro):
    return asyncio.run(coro)


def test_ler_norma_formata_cabecalho(srv):
    txt = rodar(srv.ler_norma("art. 2º, § 1º da Lei 9.430/96"))
    assert txt.startswith("# Lei nº 9.430/1996") and "§ 1º A alíquota é de quinze por cento." in txt


def test_erros_viram_toolerror(srv):
    with pytest.raises(srv.ToolError, match="ainda não chegou"):
        rodar(srv.ler_norma("Lei 99.999/2099"))
    with pytest.raises(srv.ToolError, match="Tipo de norma desconhecido"):
        rodar(srv.buscar_normas("lucro", tipos=["XYZ"]))
    with pytest.raises(srv.ToolError, match="desde inválida"):
        rodar(srv.novidades_legislativas(desde="ontem"))


def test_erro_de_rede_tem_mensagem(srv, monkeypatch):
    from mcp_planalto.http import ErroHTTP

    def cai(*a, **k):
        raise ErroHTTP("https://www.planalto.gov.br/x", None, "Portal indisponível")

    monkeypatch.setattr(srv._instancia, "ficha", cai)
    with pytest.raises(srv.ToolError, match="Portal indisponível"):
        rodar(srv.consultar_norma("Lei 9.430/96"))


def test_listar_temas(srv):
    t = rodar(srv.listar_temas())
    assert "IRPJ" in t and "Lei 9.430/1996, arts. 18 a 24-C" in t["PRECOS DE TRANSFERENCIA"]["normas_nucleo"]
