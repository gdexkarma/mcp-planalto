"""Testes contra os portais reais. Rode com: pytest -m live"""

import pytest

from mcp_planalto.config import Config
from mcp_planalto.servico import Legislacao

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def L(tmp_path_factory):
    return Legislacao(Config(home=tmp_path_factory.mktemp("live"), auto_sync=False))


def test_ler_lei_9430(L):
    r = L.texto("Lei 9.430/1996", dispositivo="art. 2")
    assert "\nArt. 2" in r["texto"] and "Pagamento por Estimativa" in r["texto"]
    assert "Redação dada pela Lei nº 12.973, de 2014" in r["texto"]


def test_resolver_tipos_e_epocas(L):
    for ref in ["DL 1.598/1977", "LC 214/2025", "Decreto 9.580/2018", "MP 2.158-35/2001", "EC 132/2023", "CTN"]:
        n = L.resolver(ref)
        assert n.url_planalto, ref


def test_senado(L):
    f = L.ficha("Lei 9.249/1995")
    assert f["alterada_por"] > 5
    assert "IMPOSTO DE RENDA" in f["indexacao"]


def test_verificar_atualizacao(L):
    r = L.verificar_atualizacao("Lei 9.249/1995")
    assert r["alteracoes_conhecidas"] > 5
    assert "conclusao" in r
