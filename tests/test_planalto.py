from pathlib import Path

from mcp_planalto.fontes.planalto_indices import ler_quadro
from mcp_planalto.fontes.planalto_texto import citacoes_de_normas, ler_documento

FIX = Path(__file__).parent / "fixtures"


def _doc():
    html = (FIX / "lei_teste.htm").read_bytes().decode("cp1252")
    return ler_documento(html, "https://www.planalto.gov.br/ccivil_03/leis/l9999.htm", "Tue, 29 Sep 2026 15:19:52 GMT")


def test_cabecalho():
    d = _doc()
    assert d.epigrafe == "LEI Nº 9.999, DE 30 DE DEZEMBRO DE 1996."
    assert d.ementa.startswith("Dispõe sobre a legislação tributária federal de teste")
    assert "(Vide Lei nº 15.100, de 2025)" in d.notas_gerais
    assert not any("Presidência" in n for n in d.notas_gerais)


def test_vigente_remove_redacao_riscada():
    d = _doc()
    txt = d.renderizar(d.selecionar("art. 2"))
    assert "regra antiga" not in txt
    assert "pagamento mensal por estimativa" in txt
    assert "(Redação dada pela Lei nº 12.973, de 2014)" in txt
    # dispositivo revogado aparece só como rótulo + nota
    assert "§ 2º (Revogado pela Lei nº 9.888, de 1999)" in txt
    assert "R$ 10.000,00" not in txt


def test_historico_mantem_redacao_riscada():
    d = _doc()
    txt = d.renderizar(d.selecionar("art. 2"), modo="historico")
    assert "~~Art. 2º A pessoa jurídica poderá optar pelo pagamento mensal, conforme regra antiga.~~" in txt
    assert "~~§ 2º O adicional" in txt


def test_selecao_de_dispositivos():
    d = _doc()
    assert d.renderizar(d.selecionar("art. 2º, § 3º, II")).splitlines() == [
        "II - do imposto retido na fonte; (Incluído pela Lei nº 9.888, de 1999)",
        "a) sobre receitas financeiras;",
        "b) sobre serviços;",
    ]
    # "§ 4º - O fato..." não pode virar "§ 4º-O"
    assert "§ 4º - O fato" in d.renderizar(d.selecionar("art. 2, § 4"))
    assert d.renderizar(d.selecionar("art. 2-A")).startswith("Lucro Presumido\nArt. 2º-A.")
    assert "milhar" in d.renderizar(d.selecionar("art. 1.001"))
    assert len(d.selecionar("arts. 1 a 2")) > len(d.selecionar("art. 2"))


def test_sem_notas():
    d = _doc()
    txt = d.renderizar(d.selecionar("art. 2-A"), notas=False)
    assert "Incluído" not in txt and "juros sobre o capital próprio" in txt


def test_citacao_de_outra_lei_fica_no_artigo():
    d = _doc()
    txt = d.renderizar(d.selecionar("art. 3"))
    assert "“Art. 7º" in txt and "Texto incluído na lei alterada" in txt
    # o "§ 5º" citado não vira dispositivo do art. 3 desta lei
    assert d.selecionar("art. 3, § 5") == []
    assert "entra em vigor" in d.renderizar(d.selecionar("art. 4"))


def test_estrutura_e_busca():
    d = _doc()
    est = d.estrutura()
    assert est[0]["titulo"].startswith("CAPÍTULO I")
    assert est[1] == {"titulo": "Seção I Base de Cálculo", "primeiro_art": "1", "ultimo_art": "1001"}
    blocos, n = d.buscar("capital proprio")
    assert n == 1 and blocos[-1].artigo == "2a"
    assert "l15100.htm" in d.arquivos_citados


def test_citacoes_de_normas():
    assert citacoes_de_normas("(Redação dada pela Lei nº 12.973, de 2014) e Medida Provisória nº 2.158-35, de 2001") == {
        "LEI:12973:2014", "MPV:2158-35:2001"}


def test_quadro():
    html = (FIX / "quadro_teste.htm").read_bytes().decode("cp1252")
    es = ler_quadro(html, "LEI", "https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/lei/_leis2025.htm")
    assert [(e.numero, e.ano, e.data) for e in es] == [
        ("15321", 2025, "2025-12-31"), ("9430", 1996, "1996-12-27"), ("2158-35", 2001, "2001-08-24")]
    assert es[0].url == "https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/lei/L15321.htm"
    assert es[1].url == "https://www.planalto.gov.br/ccivil_03/LEIS/L9430.htm"
    assert es[0].ementa == "Dispõe sobre as diretrizes orçamentárias de 2026."  # sem "Mensagem de veto"
    assert es[2].situacao == "Em vigor"


def test_quadro_mp_reeditada():
    html = """<table><tr><td><a href="../Antigas_2001/2189-49.htm">2.189-49, de 23.8.2001</a></td>
    <td>Altera a legislação do imposto de renda. Em Tramitação</td>
    <td>Originária: <a href="1636.htm">1.636</a> (Del nº 2.474, 1988) Edições: <a href="1636-1.htm">1.636-1</a>,
    <a href="2132-46.htm">2.132-46</a></td></tr></table>"""
    (e,) = ler_quadro(html, "MPV", "https://www.planalto.gov.br/ccivil_03/mpv/Quadro/x.htm")
    assert e.numero == "2189-49" and e.ementa == "Altera a legislação do imposto de renda."
    assert e.reedicoes == ["1636", "1636-1", "2132-46"]
    assert e.situacao.endswith("Em Tramitação")


def test_busca_por_expressao_exata():
    d = _doc()
    _, n = d.buscar("pessoa jurídica sujeita")
    assert n == 1
    _, n = d.buscar('"jurídica pessoa"')  # entre aspas: só a expressão exata
    assert n == 0


def test_quadro_anos_e_links():
    html = """<table>
    <tr><td><a href="L4230.htm">4.230, de 31.12.20</a></td><td>Orça a Receita.</td></tr>
    <tr><td><a href="L1.htm">2, de 1º.2.46</a></td><td>Link errado no quadro.</td></tr>
    <tr><td><a href="L41.htm">41, de 16 6.1935</a> Publicada no DOU de 20.6.1935</td><td>Sem data legível.</td></tr>
    </table>"""
    es = {e.numero: e for e in ler_quadro(html, "LEI", "https://www.planalto.gov.br/ccivil_03/leis/q.htm", 1901, 1946)}
    assert es["4230"].ano == 1920  # ano de 2 dígitos dentro do intervalo do quadro
    html91 = """<table><tr><td><a href="L8253.htm">8.2 53 , de 31 .10.91</a></td>
    <td>Dispõe sobre X. Vide texto compilado</td></tr></table>"""
    (e,) = ler_quadro(html91, "LEI", "https://www.planalto.gov.br/ccivil_03/leis/quadro/1991.htm", 1991, 1991)
    assert (e.numero, e.ano, e.ementa) == ("8253", 1991, "Dispõe sobre X.")
    assert es["2"].url is None  # link aponta para a Lei 1: descartado
    assert es["41"].ano == 1935  # ano da publicação no DOU
