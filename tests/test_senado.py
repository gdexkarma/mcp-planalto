from pathlib import Path

from mcp_planalto.fontes.senado import ler_detalhe, ref_de_nome

FIX = Path(__file__).parent / "fixtures"


def test_detalhe():
    det = ler_detalhe((FIX / "senado_lei9430.xml").read_bytes())
    n = det.norma
    assert n.chave == "LEI:9430:1996"
    assert n.data == "1996-12-27"
    assert n.apelido == "Lei do Ajuste Tributário (1996)"
    assert n.urn == "urn:lex:br:federal:lei:1996-12-27;9430"
    assert "LUCRO REAL" in n.indexacao and " ; " in n.indexacao
    assert n.senado_id == "551306"
    origens = {r.origem for r in det.relacoes_recebidas}
    # "Decreto de 10/01/2020" não é numerado: fica de fora
    assert origens == {"LEI:12973:2014", "LEI:15525:2026", "LEI:14000:2021"}
    r = next(r for r in det.relacoes_recebidas if r.origem == "LEI:15525:2026")
    assert (r.acao, r.dispositivo, r.data) == ("Acréscimo", "Art. 2, § 5", "2026-09-28")
    feitas = {(r.destino, r.dispositivo) for r in det.relacoes_feitas}
    assert ("LEI:4502:1964", "Art. 35") in feitas
    assert ("CF:1988:1988", "ADCT, Art. 76") in feitas
    assert det.dispositivos[0]["dispositivo"] == "Art. 2"


def test_ref_de_nome():
    assert ref_de_nome("Medida Provisória nº 1.563-1 de 30/01/1997").chave == "MPV:1563-1:1997"
    assert ref_de_nome("Decreto-Lei nº 5.844 de 23/09/1943").chave == "DEL:5844:1943"
    assert ref_de_nome("Constituição da República Federativa do Brasil").chave == "CF:1988:1988"
    assert ref_de_nome("Decreto de 12/03/2001") is None
    assert ref_de_nome("Decreto Legislativo nº 5 de 10/02/2000") is None
