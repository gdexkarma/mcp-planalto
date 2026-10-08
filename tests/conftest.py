"""Fixtures compartilhadas: serviço com cliente HTTP falso que serve as fixtures."""

import time
from pathlib import Path

import pytest

from mcp_planalto.config import Config
from mcp_planalto.db import Norma
from mcp_planalto.http import ErroHTTP, Resposta
from mcp_planalto.servico import Legislacao

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


