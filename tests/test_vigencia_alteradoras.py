"""Cláusula de vigência da lei recente que deu a redação lida."""

import datetime as dt

from mcp_planalto import servico
from mcp_planalto.fontes.planalto_texto import ler_documento
from mcp_planalto.servico import Legislacao

URL_ALT = "https://www.planalto.gov.br/ccivil_03/_Ato2023-2026/2026/Lei/L99999.htm"


def _doc(corpo: str, url: str):
    return ler_documento(f"<html><body>{corpo}</body></html>", url)


ALTERADORA = _doc("""<p>LEI Nº 99.999, DE 2 DE MARÇO DE 2026</p>
  <p>Art. 1º A Lei nº 1.234 passa a vigorar com alterações.</p>
  <p>Art. 2º Ficam revogados os arts. 9 e 10 da Lei nº 1.234.</p>
  <p>Art. 3º Esta Lei entra em vigor na data de sua publicação, produzindo efeitos a partir de 1º de janeiro
  de 2099.</p><p>Brasília, 2 de março de 2026; 205º da Independência e 138º da República.</p>
  <p>FULANO DE TAL</p>""", URL_ALT)

LIDA = _doc(f"""<p>LEI Nº 1.234, DE 5 DE MAIO DE 1990</p>
  <p>Art. 5º A alíquota é de 10%. <a href="{URL_ALT}#art1">(Redação dada pela Lei nº 99.999, de 2026)</a></p>
  <p>Art. 6º Texto antigo. <a href="../leis/L8000.htm#art2">(Redação dada pela Lei nº 8.000, de 1990)</a></p>""",
            "https://www.planalto.gov.br/ccivil_03/leis/L1234.htm")


def test_vigencia_da_alteradora_recente(monkeypatch):
    monkeypatch.setattr(servico, "hoje", lambda: dt.date(2026, 10, 8))
    leg = Legislacao.__new__(Legislacao)
    leg._doc_por_url = lambda url: ALTERADORA
    out = leg._vigencia_das_alteradoras(LIDA, LIDA.selecionar("art. 5"))
    assert len(out) == 1
    v = out[0]
    assert v["artigo"] == "art. 3º" and v["dispositivos"] == ["art. 5º"]
    assert v["clausula"].startswith("Art. 3º Esta Lei entra em vigor") and "Brasília" not in v["clausula"]
    assert v["data_futura"] == "2099-01-01"
    # redação dada por lei antiga: nada a buscar
    assert leg._vigencia_das_alteradoras(LIDA, LIDA.selecionar("art. 6")) == []


URL_LC = "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp999.htm"
LC = _doc("""<p>LEI COMPLEMENTAR Nº 999, DE 26 DE DEZEMBRO DE 2025</p>
  <p>Reduz benefícios e revoga a Medida Provisória nº 1.111, de 2025.</p>
  <p><a name="art7"></a>Art. 7º Texto do artigo sete.</p>
  <p><a name="art8"></a>Art. 8º Texto do artigo oito.</p>
  <p>Art. 14. Esta Lei Complementar entra em vigor na data de sua publicação e produzirá efeitos:</p>
  <p>I - a partir do primeiro dia do quarto mês subsequente ao de sua publicação, em relação:</p>
  <p>a) ao disposto no art. 4º; e</p><p>b) aos arts. 7º e 9º;</p>
  <p>II - a partir de 1º de janeiro de 2026, em relação aos demais dispositivos.</p>""", URL_LC)

LIDA2 = _doc(f"""<p>LEI Nº 1.234, DE 5 DE MAIO DE 1990</p>
  <p>Art. 9º Os juros. <a href="{URL_LC}#art8">(Redação dada pela Lei Complementar nº 999, de 2025)</a></p>
  <p>Art. 10. A alíquota. <a href="{URL_LC}#art7">(Redação dada pela Lei Complementar nº 999, de 2025)</a></p>""",
             "https://www.planalto.gov.br/ccivil_03/leis/L1234.htm")


def test_inciso_da_clausula_pelo_artigo_alterador(monkeypatch):
    monkeypatch.setattr(servico, "hoje", lambda: dt.date(2026, 2, 1))
    leg = Legislacao.__new__(Legislacao)
    leg._doc_por_url = lambda url: LC
    v = leg._vigencia_das_alteradoras(LIDA2, LIDA2.selecionar("art. 9"))[0]
    assert "revoga a Medida Provisória" in v["ementa"]
    e = v["efeitos_no_trecho_lido"][0]
    assert e["artigo_alterador"] == "art. 8º" and e["data"] == "2026-01-01" and "demais" in e["regra"]
    assert "data_futura" not in v
    e = leg._vigencia_das_alteradoras(LIDA2, LIDA2.selecionar("art. 10"))[0]["efeitos_no_trecho_lido"][0]
    assert e["artigo_alterador"] == "art. 7º" and e["data"] == "2026-04-01" and "quarto mês" in e["regra"]


def test_so_vigencia_e_termo_no_caput(monkeypatch):
    monkeypatch.setattr(servico, "hoje", lambda: dt.date(2026, 2, 1))
    leg = Legislacao.__new__(Legislacao)
    leg._doc_por_url = lambda url: LC
    e = leg._vigencia_das_alteradoras(LIDA2, LIDA2.selecionar("art. 9"))[0]["efeitos_no_trecho_lido"][0]
    assert e["so_vigencia"] is False
    simples = _doc("""<p>LEI Nº 99.998, DE 11 DE AGOSTO DE 2025</p><p><a name="art2"></a>Art. 2º Texto.</p>
      <p>Art. 4º Esta Lei entra em vigor na data de sua publicação.</p>""", URL_LC)
    leg._doc_por_url = lambda url: simples
    e = leg._vigencia_das_alteradoras(LIDA2, LIDA2.selecionar("art. 10"))
    assert e == [] or e[0].get("efeitos_no_trecho_lido") is None or e[0]["efeitos_no_trecho_lido"][0]["so_vigencia"]
    escal = _doc("""<p>LEI Nº 99.997, DE 21 DE NOVEMBRO DE 2025</p><p><a name="art33"></a>Art. 33. Texto.</p>
      <p>Art. 41. Esta Lei entra em vigor na data da sua publicação e produz efeitos a partir de:</p>
      <p>I – 1º de janeiro de 2026, quanto aos arts. 18 a 30 e aos arts. 33 e 34;</p>
      <p>II – na data de sua publicação, quanto aos demais dispositivos.</p>""", URL_LC)
    leg._doc_por_url = lambda url: escal
    lida = _doc(f"""<p>Art. 17. Texto. <a href="{URL_LC}#art33">(Redação dada pela Lei nº 99.997, de 2025)</a></p>""",
                "https://www.planalto.gov.br/ccivil_03/leis/L1234.htm")
    e = leg._vigencia_das_alteradoras(lida, lida.selecionar("art. 17"))[0]["efeitos_no_trecho_lido"][0]
    assert e["data"] == "2026-01-01" and not e["so_vigencia"]
