"""Regressões dos achados da 3ª rodada de verificação."""

import datetime as dt

from mcp_planalto.db import Relacao, consulta_fts
from mcp_planalto.efeitos import data_de_publicacao, data_efeitos
from mcp_planalto.fontes.planalto_texto import ler_documento
from mcp_planalto.fontes.senado import ler_detalhe
from mcp_planalto.referencias import chave_dispositivo, interpretar, rotulo_dispositivo
from mcp_planalto.servico import (
    _cita_blocos, _dispositivo_refletido, _ementa_altera, _familia, _revogacoes_diferidas, cita_norma,
    classificar_relacao,
)


def test_datas_de_efeitos():
    pub = dt.date(2025, 12, 26)
    assert data_efeitos("I - a partir do primeiro dia do quarto mês subsequente ao de sua publicação", pub) == \
        dt.date(2026, 4, 1)
    assert data_efeitos("III - a partir de 1º de janeiro de 2027, em relação aos arts. 450", pub) == dt.date(2027, 1, 1)
    assert data_efeitos("entra em vigor após decorridos 90 (noventa) dias de sua publicação oficial", pub) == \
        dt.date(2026, 3, 26)
    assert data_efeitos("entra em vigor na data de sua publicação e produz efeitos: I - a partir de 1º de janeiro "
                        "de 2027, quanto aos arts. 2º e 5º", pub) == dt.date(2027, 1, 1)
    assert data_efeitos("na data de sua publicação, produzindo efeitos a partir de 1º de janeiro do ano "
                        "subsequente", pub) == dt.date(2026, 1, 1)
    assert data_de_publicacao("LEI Nº 15.525, DE 28 DE SETEMBRO DE 2026") == dt.date(2026, 9, 28)


def _doc(corpo: str, url: str = "https://www.planalto.gov.br/ccivil_03/leis/x.htm"):
    return ler_documento(f"<html><body>{corpo}</body></html>", url)


def test_links_das_notas_e_revogacao_diferida():
    revogadora = _doc("""<p>LEI COMPLEMENTAR Nº 214, DE 16 DE JANEIRO DE 2025</p>
      <p><a name="art1"></a>Art. 1º Esta Lei Complementar institui o IBS.</p>
      <p><a name="art542"></a>Art. 542. Ficam revogados a partir de 1º de janeiro de 2027:</p>
      <p>X - os arts. 2º a 8º-B da Lei nº 9.718, de 27 de novembro de 1998;</p>
      <p>XXI - os seguintes dispositivos da Lei nº 10.833, de 29 de dezembro de 2003:</p>
      <p>a) os arts. 1º a 16;</p><p>b) o art. 25;</p>""")
    lida = _doc("""<p>Art. 1º Texto.</p><p>Art. 3º Do valor apurado <a href="../lcp/Lcp214.htm#art542">(Vide Lei
      Complementar nº 214, de 2025)</a></p><p>IX - energia elétrica;</p>""")
    caput = lida.selecionar("art. 3º")[0]
    assert caput.links and caput.links[0][1].endswith("Lcp214.htm#art542")
    assert revogadora.bloco_da_ancora("art542") is not None
    revs = _revogacoes_diferidas(revogadora, ["542"], interpretar("Lei 10.833/2003"), {"3"}, None, None)
    assert revs and revs[0]["data"] == "2027-01-01" and "arts. 1º a 16" in revs[0]["trecho"]
    assert not _revogacoes_diferidas(revogadora, ["542"], interpretar("Lei 10.833/2003"), {"30"}, None, None)
    assert _revogacoes_diferidas(revogadora, ["542"], interpretar("Lei 9.718/1998"), {"3"}, None, None)


def test_situacao_disposicoes_em_contrario_ressalva_e_revigoracao():
    def xml(vides):
        return f"""<?xml version="1.0"?><D><documentos><documento id="1"><identificacao><tipo>LEI-</tipo>
        <normaNome>Lei nº 9.649 de 27/05/1998</normaNome><dataassinatura>27/05/1998</dataassinatura></identificacao>
        <vides>{vides}</vides></documento></documentos></D>""".encode()

    def vide(cod, nome, data, com):
        return (f"<vide><codnormaposterior>{cod}</codnormaposterior><datAssinatura>{data}</datAssinatura>"
                f"<nomeNormaPosterior>{nome}</nomeNormaPosterior><comentario>{com}</comentario><itens/></vide>")

    d = ler_detalhe(xml(vide(1, "Lei nº 10.683 de 28/05/2003", "28/05/2003",
                             "Declaração de Revogação Permanente das Disposições em Contrário da Norma no Todo")))
    assert d.norma.situacao is None
    d = ler_detalhe(xml(vide(1, "Lei nº 9.472 de 16/07/1997", "16/07/1998",
                             "Declaração de Revogação Permanente com Ressalva da Norma no Todo")))
    assert d.norma.situacao.startswith("Revogada com ressalvas")
    d = ler_detalhe(xml(vide(1, "Lei nº 1 de 01/01/1999", "01/01/1999", "Declaração de Revogação Permanente da Norma no Todo")
                        + vide(2, "Lei nº 2 de 01/01/2000", "01/01/2000",
                               "Declaração de Revigoração Permanente da Norma no Todo")))
    assert d.norma.situacao is None


def test_classificador_vigencia_e_familia_mp():
    base = dict(origem="LEI:12859:2013", destino="LEI:10865:2004", declaracao="Declaração de Alteração Permanente",
                dispositivo="Art. 8")
    assert classificar_relacao(Relacao(acao="Encerramento de Vigência", **base)) == "altera"
    assert classificar_relacao(Relacao(acao="Vigência Determinada", **base)) == "altera"
    assert _familia("MPV:1523:1996") == _familia("MPV:1523-13:1997") == "MPV:1523"
    assert _familia("MPV:1303:2025") == "MPV:1303:2025"
    assert chave_dispositivo("Art. 22, caput, Item 1") == (("art", "22"), ("inc", "1"))


def test_citacao_estadual_e_ano_apos_barra():
    import pytest

    for t in ("Lei 6.374/89 de São Paulo", "Decreto 9.580/2018 de São Paulo", "Lei 1.234/2019 de Pernambuco"):
        with pytest.raises(ValueError):
            interpretar(t)
    assert interpretar("Lei 9.430/96 de 27 de dezembro").chave == "LEI:9430:1996"


def test_ementa_que_altera_e_leinº():
    e = ("Altera a Lei nº 15.473, de 2026, que altera a Lei nº 9.818, de 23 de agosto de 1999, e a Lei nº 12.712, "
         "de 30 de agosto de 2012.")
    assert _ementa_altera(e, interpretar("Lei 15.473/2026"))
    assert not _ementa_altera(e, interpretar("Lei 9.818/1999"))
    assert cita_norma("(Redação dada pela Leinº 15.272, de 2025)", interpretar("Lei 15.272/2025"))


def test_dispositivo_refletido_e_link_da_nota():
    doc = _doc("""<p>Art. 1º Texto.</p>
      <p>Art. 29. (VETADO).</p>
      <p>Art. 55-C. A ANPD é composta de:</p>
      <p>V-B - Auditoria; e <a href="https://www.planalto.gov.br/ccivil_03/_Ato2023-2026/2026/Lei/L15352.htm#art1">
      (Incluído pela Lei nº 15.452, de 2026)</a></p>""")
    ref = interpretar("Lei 15.352/2026")
    assert rotulo_dispositivo((("art", "55c"), ("inc", "5b"))) == "art. 55-C, V-B"
    assert _cita_blocos(doc.selecionar("art. 55-C, V-B"), ref)  # número errado na nota, link certo
    assert _dispositivo_refletido(doc, interpretar("Lei 15.041/2024"), (("art", "29"), ("par", "3")), "acrescimo")
    assert not _dispositivo_refletido(doc, ref, (("art", "55c"), ("inc", "8")), "acrescimo")


def test_parser_caput_depois_da_redacao_antiga_e_protocolo():
    doc = _doc("""<p>Art. 1º Texto.</p>
      <p><strike>Art. 18. Redação antiga.</strike></p><p><strike>§ 1º Parágrafo antigo.</strike></p>
      <p>Art. 18. Redação atual do caput.</p><p>§ 1º Parágrafo atual.</p>
      <p>Art. 223. Último antes do protocolo.</p><p>Protocolo</p>
      <p>Art. 224. As condições constarão de protocolo.</p>""")
    assert "Redação atual do caput" in doc.renderizar(doc.selecionar("art. 18, caput"))
    assert "Parágrafo atual" not in doc.renderizar(doc.selecionar("art. 18, caput"))
    assert "condições" in doc.renderizar(doc.selecionar("art. 224"))


def test_parser_inciso_vivo_apos_paragrafo_riscado_e_paragrafo_continuacao():
    doc = _doc("""<p>Art. 1º Texto.</p>
      <p>Art. 104. Aplica-se a pena:</p><p>I - primeiro;</p>
      <p><strike>Parágrafo único. Aplicam-se cumulativamente.</strike></p>
      <p>VI - quando o veículo terrestre;</p>
      <p>Art. 8º Deduções:</p><p>II - pagamentos:</p>
      <p>i) às contribuições de que trata o</p><p>§ 15 do art. 40 da Constituição Federal;</p>
      <p>j) (VETADO).</p>""")
    assert "veículo" in doc.renderizar(doc.selecionar("art. 104, inciso VI"))
    assert "Constituição" in doc.renderizar(doc.selecionar("art. 8º, inciso II, alínea i"))
    assert doc.selecionar("art. 8º, inciso II, alínea j")


def test_busca_siglas_dentro_da_consulta():
    assert '"juros sobre o capital proprio"' in consulta_fts("JCP IRRF")
    assert '"retido na fonte"' in consulta_fts("JCP IRRF")
