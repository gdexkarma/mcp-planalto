import pytest

from mcp_planalto.referencias import (
    chave_dispositivo,
    dispositivo_contem,
    formatar_numero,
    interpretar,
    rotulo_dispositivo,
)


@pytest.mark.parametrize(
    "texto, chave",
    [
        ("Lei 9.430/96", "LEI:9430:1996"),
        ("Lei nº 9.430, de 27 de dezembro de 1996", "LEI:9430:1996"),
        ("lei 12973/2014", "LEI:12973:2014"),
        ("LC 214/2025", "LCP:214:2025"),
        ("Lei Complementar nº 123, de 14 de dezembro de 2006", "LCP:123:2006"),
        ("Decreto 9.580/2018", "DEC:9580:2018"),
        ("Decreto-Lei nº 1.598, de 26.12.1977", "DEL:1598:1977"),
        ("DL 1.598/77", "DEL:1598:1977"),
        ("MP 2.158-35/2001", "MPV:2158-35:2001"),
        ("Medida Provisória nº 1.303, de 2025", "MPV:1303:2025"),
        ("EC 132/2023", "EMC:132:2023"),
        ("Lei nº 4.502 de 30/11/1964", "LEI:4502:1964"),
        ("RIR/2018", "DEC:9580:2018"),
        ("CTN", "LEI:5172:1966"),
        ("CF", "CF:1988:1988"),
        ("LEI:9430:1996", "LEI:9430:1996"),
        ("l9430.htm", "LEI:9430:"),
        ("Lei 9.430", "LEI:9430:"),
    ],
)
def test_interpretar(texto, chave):
    assert interpretar(texto).chave == chave


def test_interpretar_invalido():
    with pytest.raises(ValueError):
        interpretar("Portaria qualquer coisa")


def test_nome_e_numero():
    assert interpretar("MP 2.158-35/2001").nome == "Medida Provisória nº 2.158-35/2001"
    assert formatar_numero("9430") == "9.430"
    assert formatar_numero("214") == "214"


@pytest.mark.parametrize(
    "a, b",
    [
        ("Art. 42, § 3, Inciso 2", "art. 42, § 3º, II"),
        ("Art. 79, Parágrafo Único", "art 79, parágrafo único"),
        ("Art. 18, caput, Inciso 2,", "art. 18, II"),
        ("Art. 2, § 5 [Lei nº 9.430 de 27/12/1996]", "art. 2º, § 5º"),
        ("art. 1.022", "Art. 1022"),
        ("Art. 74, § 12, Inciso 2, Alínea f", 'art. 74, § 12, II, "f"'),
    ],
)
def test_dispositivos_equivalentes(a, b):
    assert chave_dispositivo(a) == chave_dispositivo(b) != ()


def test_contencao_e_rotulo():
    art = chave_dispositivo("art. 74")
    inc = chave_dispositivo("art. 74, § 12, II")
    assert dispositivo_contem(art, inc)
    assert not dispositivo_contem(inc, art)
    assert rotulo_dispositivo(inc) == "art. 74, § 12, II"
    assert rotulo_dispositivo(chave_dispositivo("art. 10-A, § 2º")) == "art. 10-A, § 2º"
