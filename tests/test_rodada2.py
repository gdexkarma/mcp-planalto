"""Regressões dos achados da 2ª rodada de verificação."""

from mcp_planalto.db import Relacao, consulta_fts
from mcp_planalto.fontes.planalto_texto import ler_documento
from mcp_planalto.fontes.senado import ler_detalhe
from mcp_planalto.http import decodificar
from mcp_planalto.referencias import chave_dispositivo, interpretar, interpretar_citacao
from mcp_planalto.servico import (
    _cita_incorporada, _ementa_altera, _incluido_por_outra, _relacao_no_escopo, _termo_livre, classificar_relacao,
)
from mcp_planalto.temas import escopo_de


def test_utf16_com_bom_e_tamanho_impar():
    corpo = "﻿<p>Art. 12-C. Verificada a existência</p>".encode("utf-16-le") + b"\x00"
    assert decodificar(corpo).lstrip("﻿").startswith("<p>Art. 12-C")


def _xml_vides(vides: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?><DetalheDocumento><documentos><documento id="1">
<identificacao><tipo>LEI-</tipo><normaNome>Lei nº 14.790 de 29/12/2023</normaNome>
<dataassinatura>29/12/2023</dataassinatura></identificacao><vides>{vides}</vides></documento></documentos>
</DetalheDocumento>""".encode()


def _vide(cod, nome, data, comentario, itens=""):
    return (f"<vide><codnormaposterior>{cod}</codnormaposterior><datAssinatura>{data}</datAssinatura>"
            f"<nomeNormaPosterior>{nome}</nomeNormaPosterior><comentario>{comentario}</comentario>"
            f"<itens>{itens}</itens></vide>")


def test_vide_vazia_duplicada_nao_vira_alteracao():
    item = "<item><dispositivo>Art. 17</dispositivo><comentario>Ressalva</comentario></item>"
    xml = _xml_vides(
        _vide(9, "Lei nº 15.421 de 10/06/2026", "10/06/2026", "Declaração de Alteração Permanente", item)
        + _vide(9, "Lei nº 15.421 de 10/06/2026", "10/06/2026", "Declaração de Alteração Permanente")
        + _vide(8, "Lei nº 15.500 de 10/08/2026", "10/08/2026", "Declaração de Revogação Permanente da Norma no Todo"))
    det = ler_detalhe(xml)
    classes = {(r.origem, classificar_relacao(r)) for r in det.relacoes_recebidas}
    assert classes == {("LEI:15421:2026", "ressalva"), ("LEI:15500:2026", "altera")}
    assert det.norma.situacao.startswith("Revogada")


def test_perda_de_eficacia_sem_no_todo_e_data_impossivel():
    xml = _xml_vides(
        _vide(1, "Excerto de Ata de 18/02/2021", "18/02/2021", "Declaração de Perda de Eficácia")
        + _vide(2, "Decreto nº 7.212 de 08/03/1879", "08/03/1879", "Declaração de Alteração Permanente",
                "<item><dispositivo>Art. 1</dispositivo><comentario>Alteração</comentario></item>"))
    det = ler_detalhe(xml)
    assert det.norma.situacao.startswith("Sem eficácia")
    dec = [r for r in det.relacoes_recebidas if r.origem.startswith("DEC:")]
    assert dec and dec[0].data is None  # data anterior à norma alterada: erro de cadastro


def test_conversao_e_reedicao_nao_sao_alteracao():
    base = dict(origem="LEI:14514:2022", destino="MPV:1133:2022", acao="", dispositivo="")
    assert classificar_relacao(Relacao(declaracao="Declaração de Conversão em Lei com Alteração", **base)) == "conversao"
    assert classificar_relacao(Relacao(declaracao="Declaração de Reedição com Alteração", **base)) == "reedicao"


def test_dispositivo_em_ordem_invertida_e_sufixo_o():
    assert chave_dispositivo("§ 1º do art. 44") == (("art", "44"), ("par", "1"))
    assert chave_dispositivo("inciso II do § 2º do art. 3") == (("art", "3"), ("par", "2"), ("inc", "2"))
    assert chave_dispositivo("caput do art. 22") == (("art", "22"), ("caput", ""))
    assert chave_dispositivo("Art. 19-O, § 2") == (("art", "19o"), ("par", "2"))
    assert chave_dispositivo("art. 1o") == (("art", "1"),)
    assert chave_dispositivo("art. 359-M-A") == (("art", "359ma"),)


def test_ementa_finalidade_nao_conta_como_alteracao():
    e = ("Altera a Lei nº 11.671, de 8 de maio de 2008, para incluir o preso pela prática do crime previsto no "
         "inciso VII do § 2º do art. 121 do Decreto-Lei nº 2.848, de 7 de dezembro de 1940 (Código Penal), e a "
         "Lei nº 7.210, de 11 de julho de 1984, para dispor sobre o regime disciplinar diferenciado.")
    assert not _ementa_altera(e, interpretar("DL 2.848/1940"))
    assert _ementa_altera(e, interpretar("Lei 7.210/1984"))
    assert _ementa_altera(e, interpretar("Lei 11.671/2008"))


def test_vide_no_artigo_nao_e_incorporacao():
    r = interpretar("Lei 15.498/2026")
    assert not _cita_incorporada("Art. 2º Os valores ... (Vide Lei nº 15.498, de 2026) Vigência", r)
    assert _cita_incorporada("§ 1º ... (Redação dada pela Lei nº 15.498, de 2026)", r)


def test_acrescimo_homonimo_incluido_por_outra_lei():
    doc = ler_documento("""<html><body><p>Art. 73-A. Ao segurado é devido salário-maternidade.
        (Incluído pela Lei nº 15.415, de 2026)</p></body></html>""", "x")
    alvo = doc.selecionar("art. 73-A")
    assert _incluido_por_outra(alvo, interpretar("Lei 15.371/2026"))
    assert not _incluido_por_outra(alvo, interpretar("Lei 15.415/2026"))


def test_parser_pena_fica_no_artigo_e_artigo_incluido_apos_citacao():
    doc = ler_documento("""<html><body>
      <p>Art. 1º Esta Lei altera o Código.</p>
      <p>Art. 2º Praticar, na presença de alguém menor de 14 anos, ato libidinoso:</p>
      <p>Pena – reclusão, de 5 (cinco) a 12 (doze) anos, e multa. (Redação dada pela Lei nº 15.280, de 2025)</p>
      <p>Favorecimento da prostituição (Incluído pela Lei nº 12.015, de 2009)</p>
      <p>Art. 3º Submeter, induzir ou atrair à prostituição:</p>
      <p>Art. 4º O inciso XVI do artigo 105 do Decreto-Lei nº 37 passa a vigorar com a seguinte redação:</p>
      <p>"XVI - fracionada em duas ou mais remessas postais".</p>
      <p>Art. 4º-A. As plataformas digitais adotarão mecanismos. (Incluído pela Lei nº 15.502, de 2026)</p>
    </body></html>""", "x")
    assert "15.280" in doc.renderizar(doc.selecionar("art. 2º"))
    assert "Favorecimento" in doc.renderizar(doc.selecionar("art. 3º"))
    assert "plataformas" in doc.renderizar(doc.selecionar("art. 4º-A"))


def test_escopo_exceto_e_relacoes_sem_dispositivo():
    _ref, disp = interpretar_citacao("Lei 8.981/1995, exceto arts. 7 a 24")
    esc = escopo_de(disp)
    assert esc("1") and esc("25") and not esc("7") and not esc("24")
    vazia = Relacao(origem="LEI:1:2020", destino="LEI:8981:1995", declaracao="Declaração de Alteração Permanente",
                    acao="", dispositivo="")
    assert not _relacao_no_escopo(vazia, esc)
    assert _relacao_no_escopo(vazia, None)
    anexo = Relacao(origem="LEI:1:2020", destino="LEI:8981:1995", declaracao="", acao="Alteração",
                    dispositivo="Anexo 2")
    assert not _relacao_no_escopo(anexo, esc)
    reg = Relacao(origem="DEC:1:2020", destino="LEI:8981:1995",
                  declaracao="Declaração de Regulamentação Permanente de Norma", acao="", dispositivo="")
    assert _relacao_no_escopo(reg, esc, "Regulamenta o art. 30 da Lei nº 8.981, de 1995.")
    assert not _relacao_no_escopo(reg, esc, "Regulamenta o art. 10 da Lei nº 8.981, de 1995.")


def test_busca_barra_flexao_e_tema_livre():
    assert consulta_fts("SUDENE/SUDAM") == '(("sudene" OR "sudam"))'
    assert _termo_livre("Reporto") == "reporto"
    assert _termo_livre("Pilar 2 / tributação mínima global").count(" OU ") == 1
