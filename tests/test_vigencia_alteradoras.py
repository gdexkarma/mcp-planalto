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
