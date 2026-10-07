"""Testes do serviço sem rede: um cliente HTTP falso serve as fixtures."""

import time
from pathlib import Path

import pytest

from mcp_planalto.config import Config
from mcp_planalto.db import Norma, consulta_fts
from mcp_planalto.exportar import exportar
from mcp_planalto.http import ErroHTTP, Resposta
from mcp_planalto.servico import Legislacao, paginar

FIX = Path(__file__).parent / "fixtures"
URL_LEI = "https://www.planalto.gov.br/ccivil_03/leis/l9430.htm"


class HTTPFalso:
    def __init__(self, rotas: dict[str, tuple[bytes, str]]):
        self.rotas = rotas
        self.pedidos: list[str] = []

    def get(self, url, max_idade=0, aceitar=None):
        self.pedidos.append(url)
        for prefixo, (corpo, ct) in self.rotas.items():
            if url.startswith(prefixo):
                return Resposta(url, 200, corpo, {"content-type": ct, "last-modified": "Tue, 29 Sep 2026 15:19:52 GMT"},
                                time.time())
        raise ErroHTTP(url, 404, "Não encontrado")


@pytest.fixture
def L(tmp_path):
    cfg = Config(home=tmp_path, auto_sync=False)
    leg = Legislacao(cfg)
    falso = HTTPFalso({
        URL_LEI: ((FIX / "lei_teste.htm").read_bytes(), "text/html"),
        "https://legis.senado.leg.br/dadosabertos/legislacao/LEI/9430/1996":
            ((FIX / "senado_lei9430.xml").read_bytes(), "application/xml; charset=UTF-8"),
    })
    leg.http = leg.indices.http = leg.senado.http = falso
    leg.db.salvar([
        Norma(chave="LEI:9430:1996", tipo="LEI", numero="9430", ano=1996, data="1996-12-27",
              ementa="Dispõe sobre a legislação tributária federal.", url_planalto=URL_LEI, origem="planalto"),
        Norma(chave="LEI:12973:2014", tipo="LEI", numero="12973", ano=2014, data="2014-05-13",
              ementa="Altera a legislação tributária federal relativa ao Imposto sobre a Renda das Pessoas "
                     "Jurídicas - IRPJ.", origem="planalto"),
        Norma(chave="LEI:15600:2026", tipo="LEI", numero="15600", ano=2026, data=time.strftime("%Y-%m-%d"),
              ementa="Altera a Lei nº 9.430, de 27 de dezembro de 1996, para dispor sobre compensação.",
              origem="planalto"),
    ])
    return leg


def test_resolver_sem_ano(L):
    assert L.resolver("Lei 9.430").chave == "LEI:9430:1996"


def test_texto_e_paginacao(L):
    r = L.texto("Lei 9.430/96", dispositivo="art. 2, § 1")
    assert r["texto"] == "§ 1º A alíquota é de quinze por cento."
    assert r["planalto_atualizado_em"] == "2026-09-29"
    r = L.texto("Lei 9.430/96", max_caracteres=300, pagina=2)
    assert r["pagina"] == 2 and r["total_paginas"] > 2
    r = L.texto("Lei 9.430/96", termo="capital próprio")
    assert r["artigos_com_o_termo"] == 1
    r = L.texto("Lei 9.430/96", dispositivo="art. 99")
    assert "aviso" in r


def test_ficha_e_historico(L):
    f = L.ficha("Lei 9.430/1996")
    assert f["apelido"] == "Lei do Ajuste Tributário (1996)"
    assert f["ementa"] == "Dispõe sobre a legislação tributária federal."  # a do Planalto prevalece
    assert f["alterada_por"] == 2  # 12.973 e 15.525; a alteração vetada não conta
    h = L.historico("Lei 9.430/1996", dispositivo="art. 2")
    assert [a["norma"] for a in h["alteracoes"]] == ["Lei nº 15.525/2026", "Lei nº 12.973/2014"]
    h = L.historico("Lei 9.430/1996", dispositivo="art. 74, § 12")
    assert h["alteracoes"][0]["dispositivos"] == ["Alteração: Art. 74, § 12, Inciso 2, Alínea f"]
    h = L.historico("Lei 9.430/1996", direcao="feitas")
    assert [a["chave"] for a in h["alteracoes"]] == ["LEI:4502:1964"]  # correlata fica de fora


def test_verificar_atualizacao(L):
    r = L.verificar_atualizacao("Lei 9.430/1996")
    assert r["alteracoes_conhecidas"] == 2
    assert [p["norma"] for p in r["nao_refletidas_no_texto"]] == ["Lei nº 15.525/2026"]
    assert [p["norma"] for p in r["normas_recentes_que_citam_na_ementa"]] == ["Lei nº 15.600/2026"]
    assert r["conclusao"].startswith("ATENÇÃO")


def test_busca_sem_acento(L):
    r = L.buscar("legislacao tributaria")
    assert {x["chave"] for x in r["resultados"]} == {"LEI:9430:1996", "LEI:12973:2014"}
    r = L.buscar('"imposto sobre a renda das pessoas juridicas"')
    assert [x["chave"] for x in r["resultados"]] == ["LEI:12973:2014"]
    r = L.buscar("IRPJ OU compensação", tipos=["LEI"], ano_inicio=2014)
    assert {x["chave"] for x in r["resultados"]} == {"LEI:12973:2014", "LEI:15600:2026"}


def test_consulta_fts_segura():
    assert consulta_fts('lucro "real" OU IRPJ/CSLL') == '("lucro" AND "real") OR ("irpj csll")'
    assert consulta_fts("tribut*") == '("tribut"*)'
    assert consulta_fts('"') == ""


def test_mapear_tema_livre(L):
    r = L.mapear_tema("tema de teste", termos_extras=['"lucro real"'], normas_extras=["Lei 9.430/1996"])
    por_chave = {n["chave"]: n for n in r["normas"]}
    assert por_chave["LEI:9430:1996"]["camada"] == "núcleo"
    assert por_chave["LEI:12973:2014"]["camada"] == "alteradora/regulamentadora"
    assert por_chave["LEI:15525:2026"]["camada"] == "alteradora/regulamentadora"
    assert por_chave["LEI:14000:2021"]["relevancia"] < por_chave["LEI:12973:2014"]["relevancia"]
    assert r["normas"][0]["chave"] == "LEI:9430:1996"


def test_exportar(tmp_path):
    linhas = [{"posicao": 1, "norma": "Lei nº 9.430/1996", "motivos": ["a", "b"], "url": "https://x"}]
    p = exportar(linhas, tmp_path, "Acervo IRPJ", "xlsx", titulo="Teste")
    from openpyxl import load_workbook

    ws = load_workbook(p).active
    assert ws["B5"].value == "Lei nº 9.430/1996" and ws["I5"].value == "a | b"
    c = exportar(linhas, tmp_path, "Acervo IRPJ", "csv")
    assert "Lei nº 9.430/1996" in c.read_text("utf-8-sig")


def test_paginar():
    p = paginar("\n".join(f"linha {i}" for i in range(100)), 100, 3)
    assert p.pagina == 3 and p.total_paginas > 3 and p.texto.startswith("linha")
