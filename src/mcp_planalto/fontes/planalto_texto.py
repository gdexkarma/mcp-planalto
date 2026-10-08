"""Leitura do texto das normas publicado no Planalto.

O Planalto publica o texto "multivigente": a redação atual convive com as
redações anteriores riscadas (<strike>) e com notas como "(Redação dada pela
Lei nº 12.973, de 2014)". Este módulo separa as duas coisas e organiza o texto
em blocos com o dispositivo (art., §, inciso, alínea) de cada um.

Cada bloco pertence a um "espaço" de numeração:
- ""        articulado principal da norma;
- "adct"    Ato das Disposições Constitucionais Transitórias (na CF);
- "reg"     regulamento aprovado por decreto e publicado como anexo (RIR, RPS...);
- "anexo:X" anexos sem artigos (tabelas, listas).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import html as lhtml

from ..referencias import (
    chave_dispositivo,
    dispositivo_contem,
    normalizar,
    romano_para_int,
    rotulo_dispositivo,
)

_BLOCOS = {
    "p", "div", "center", "blockquote", "li", "ul", "ol", "dd", "dt", "pre", "table", "tbody",
    "thead", "h1", "h2", "h3", "h4", "h5", "h6", "body", "hr", "form", "dl",
}
_RISCADOS = {"strike", "s", "del"}
_IGNORAR = {"script", "style", "head", "title", "noscript", "object", "iframe"}

_RE_NOTA = re.compile(
    r"\((?:\s|<)*(?:Reda[çc][ãa]o|Inclu[íi]d|Acrescid|Revogad|Vide\b|Vig[êe]ncia|Produ[çc][ãa]o\s+de\s+efeito|"
    r"Regulament|Renumerad|Revigorad|Restabelecid|Promulga|Convertid|Suspens|Execu[çc][ãa]o\s+suspensa|"
    r"Mantid|Declarad|Express[ãa]o|Tornad|Retificad|Prorrogad|Transformad|Em\s+vigor|Inclus[ãa]o|"
    r"Vetad[oa]\s+pel|Partes?\s+mantid|Revoga[çc][ãa]o|Efic[áa]cia|Texto\s+compilado|Ver\s|"
    r"Inconstitucional|Vide\s|Norma\s|Dispositivo|Par[áa]grafo\s+renumerad|Artigo\s+renumerad|Sem\s+efic)"
    r"[^()]*(?:\([^()]*\)[^()]*)*\)",
    re.I,
)
# Textos de links soltos que o Planalto põe ao lado dos dispositivos
_RE_LINKS_SOLTOS = re.compile(
    r"\b(Vig[êe]ncia(\s+encerrada)?|Produ[çc][ãa]o\s+de\s+efeitos?|Sem\s+efic[áa]cia|Regulamento|"
    r"Regulamenta[çc][ãa]o|Mensagem\s+de\s+veto|Texto\s+compilado|Convers[ãa]o|Efeitos?)\b",
    re.I,
)

# Rótulos. O sufixo de letra ("10-A", "22A", "359-M-A") é aceito colado ao número;
# "Art. 7º - O lucro" e "Art. 46 -A renda" NÃO têm sufixo (dash seguido de palavra).
_SUFIXO = (r"(?:(?:(?<=[\dº°o])-|(?<=\d))(?P<suf>[A-Z]{1,2}(?:-[A-Z])?)(?![A-Za-zÀ-ÿ])(?!\s+[a-zà-ÿ]))?")
_RE_ART = re.compile(
    r"^(?:Art(?:igo)?|ART(?:IGO)?|A?rt(?=\.))(?:\s*\.){0,2}\s*(?P<num>\d{1,3}(?:\.\d{3})+|\d+)\s*(?:º|°|o(?=\W))?" + _SUFIXO
)
_RE_PAR = re.compile(r"^§\s*(?P<num>\d+)\s*(?:º|°|o(?=\W))?" + _SUFIXO)
_RE_PU = re.compile(r"^Par[áa]grafo\s+[úu]nico", re.I)
_RE_INC = re.compile(r"^([IVXLC]+)(?:\s*-\s*([A-Z])(?=\s*[-–—]))?\s*[-–—]")
_RE_ALI = re.compile(r"^([a-z])(?:\s*-\s*([A-Z0-9]))?\s*\)")
_RE_ITEM = re.compile(r"^(\d{1,3})(?:\s*-\s*([A-Z]))?\s*(?:[.)]|\s[-–—])\s")
_RE_ESTRUTURA = re.compile(
    r"^(LIVRO|PARTE|T[ÍI]TULO|CAP[ÍI]TULO|SE[ÇC][ÃA]O|SUBSE[ÇC][ÃA]O|ANEXOS?|ATO DAS DISPOSI[ÇC][ÕO]ES|PROTOCOLO)"
    r"(?![^\W\d_])", re.I
)
_RE_ANEXO = re.compile(
    r"^(?:ANEXOS?(?![^\W\d_])(?:\s+(?:N[º°o]\.?\s*)?([IVXLC]+|\d+|[A-Z]|[ÚU]NICO)(?![^\W\d_]))?|"
    r"(?:LISTA|TABELA|QUADRO|RELA[ÇC][ÃA]O|DEMONSTRATIVO)\b[^.]{0,120}?\bANEX[OA]S?\b|PROTOCOLO\b)", re.I)
_RE_ADCT = re.compile(r"^ATO DAS DISPOSI[ÇC][ÕO]ES CONSTITUCIONAIS TRANSIT", re.I)
_RE_PREAMBULO = re.compile(
    r"^O\s+GENERAL[ÍI]SSIMO|^O\s+CHEFE\s+DO\s+GOVERNO\s+PROVIS|^D(OM|\.)\s+PEDRO|^A\s+PRINCEZA|"
    r"^(O|A)\s+(VICE[-–— ]\s?)?PRESIDENT[EA]\s+DA\s+REP[ÚU]BLICA|^O\s+CONGRESSO\s+NACIONAL|^As\s+Mesas\s+da\s+C[âa]mara|"
    r"^Fa[çc]o\s+saber|^O\s+PRESIDENTE\s+DO\s+SENADO|^N[óo]s,\s+representantes|^O\s+PRESIDENTE\s+DA\s+C[ÂA]MARA|"
    r"^OS\s+MINISTROS|^O\s+CHEFE\s+DO\s+GOVERNO|^O\s+PRESIDENTE\s+DOS\s+ESTADOS",
    re.I,
)
_RE_EPIGRAFE = re.compile(
    r"^(LEI|DECRETO|MEDIDA PROVIS[ÓO]RIA|EMENDA CONSTITUCIONAL|LEI COMPLEMENTAR|DECRETO-LEI|LEI DELEGADA|"
    r"CONSTITUI[ÇC][ÃA]O)\b.*\b(1[89]\d\d|20\d\d)\b",
    re.I,
)
_BOILERPLATE = re.compile(
    r"^[|\s]*(Presid[êe]ncia da Rep[úu]blica|Casa Civil|Subchefia|Secretaria[- ]Geral|Secretaria Especial|"
    r"Brastra|Este texto n[ãa]o substitui|Texto para impress[ãa]o)",
    re.I,
)
# Linha de local e data da assinatura: depois dela vêm assinaturas e notas de rodapé.
_RE_ASSINATURA = re.compile(
    r"^(Bras[íi]lia|Rio de Janeiro|Petr[óo]polis|Palácio do Planalto|Pal[áa]cio do Catete)\s*,?\s*(em\s+)?"
    r"\d{1,2}[º°]?\s*(de\s+[a-zç]+|\.\d)|^Sala das sess[õo]es do Governo|^Carta de Lei\b|"
    r"^Pal[áa]cio do Rio de Janeiro",
    re.I,
)
_RE_INICIO_EMENTA = re.compile(
    r"^(Disp[õo]e|Altera|Institui|Estabelece|Regulamenta|Aprova|Cria|Autoriza|Abre|Concede|Acrescenta|Revoga|"
    r"Define|Prorroga|Fixa|Promulga|Torna|Denomina|Reajusta|Modifica|Inclui|Declara|Reconhece|Consolida|"
    r"D[áa] nova|Organiza|Trata|Veda|Converte|Reabre|Regula|Determina|Ratifica|Introduz|Efetua|C[óo]digo|"
    r"Estatuto|Acresce|Disciplina|Atualiza|Prov[êe]|Isenta|Reduz|Suspende|Extingue|Transforma|Assegura)\b"
)
_RE_ALTERADOR = re.compile(
    r"passa(m)?\s+a\s+vigorar|acrescid[oa]s?\s+d|com\s+as\s+seguintes\s+altera|com\s+a\s+seguinte\s+reda|"
    r"seguintes?\s+(arts?\.|artigos?|dispositivos?|par[áa]grafos?|incisos?)|vigorar\s+acrescid",
    re.I,
)


@dataclass
class Bloco:
    vigente: str  # texto sem trechos riscados
    completo: str  # texto com trechos riscados entre ~~ ~~
    ancoras: list[str] = field(default_factory=list)
    tipo: str = "texto"  # dispositivo, estrutura, texto, preambulo, citacao, assinatura, rodape
    chave: tuple = ()
    rotulo: str = ""
    notas: list[str] = field(default_factory=list)
    revogado: bool = False  # dispositivo revogado (só restou a nota)
    obsoleto: bool = False  # redação anterior inteiramente riscada
    artigo: str | None = None  # artigo a que o bloco pertence
    espaco: str = ""  # "", "adct", "reg", "anexo:I"
    titulo_estrutura: str | None = None

    def texto(self, modo: str = "vigente", notas: bool = True) -> str:
        if modo == "historico":
            return self.completo
        if self.tipo == "estrutura" and self.titulo_estrutura:
            return self.titulo_estrutura
        if self.revogado:
            nota = next((n for n in self.notas if "revog" in n.lower()), "(Revogado)")
            return f"{self.rotulo_original()} {nota}".strip()
        t = self.vigente
        if not notas:
            for n in self.notas:
                t = t.replace(n, "")
            t = _espacos(_RE_LINKS_SOLTOS.sub(" ", t) if len(t) < 400 else t)
        return t

    def crua(self) -> str:
        return _espacos(self.completo.replace("~~", ""))

    def rotulo_original(self) -> str:
        m = re.match(r"^((?:Art|ART|Artigo)\.?\s*[\d.]*\d\s*[º°o]?(?:-[A-Z]{1,2}\b|[A-Z](?=[.\s]))?\.?|"
                     r"§\s*\d+\s*[º°o]?(?:-[A-Z]{1,2}\b)?|[Pp]ar[áa]grafo\s+[úu]nico|PAR[ÁA]GRAFO\s+[ÚU]NICO|"
                     r"[IVXLC]+(?:\s*-\s*[A-Z])?(?=\s*[-–—])|[a-z]\))", self.crua())
        if not m:
            return ""
        r = m.group(0).strip()
        return r + " -" if re.fullmatch(r"[IVXLC]+(?:\s*-\s*[A-Z])?", r) else r


@dataclass
class Documento:
    url: str
    epigrafe: str
    ementa: str | None
    notas_gerais: list[str]
    blocos: list[Bloco]
    last_modified: str | None = None
    arquivos_citados: set[str] = field(default_factory=set)  # nomes de arquivo dos links (l12973.htm...)

    # ----------------------------------------------------------- seleção
    def artigos(self, espaco: str | None = None) -> list[str]:
        """Artigos na ordem do texto. Fora do articulado principal vêm prefixados ("adct:76", "reg:1")."""
        vistos, out = set(), []
        for b in self.blocos:
            if b.artigo and b.tipo != "rodape" and (espaco is None or b.espaco == espaco) \
                    and not b.espaco.startswith("anexo:"):
                k = f"{b.espaco}:{b.artigo}" if b.espaco else b.artigo
                if k not in vistos:
                    vistos.add(k)
                    out.append(k)
        return out

    def espacos(self) -> list[str]:
        out = []
        for b in self.blocos:
            if b.espaco not in out and b.tipo != "rodape":
                out.append(b.espaco)
        return out

    def _espaco_padrao(self, artigo: str) -> str:
        """Sem indicação: regulamento anexo tem prioridade (RIR, RPS); senão o articulado principal."""
        if any(b.espaco == "reg" and b.artigo == artigo for b in self.blocos):
            return "reg"
        return ""

    def anexos(self) -> list[str]:
        return [e.split(":", 1)[1] for e in self.espacos() if e.startswith("anexo:")]

    def selecionar(self, especificacao: str) -> list[Bloco]:
        """Seleciona blocos por dispositivo.

        Aceita: "art. 15", "arts. 15 a 20", "arts. 74 e 80", "art. 2º, § 4º, III", "§ 1º do art. 44",
        "art. 44, caput", "art. 76 do ADCT", "decreto, art. 1" (artigo do decreto que aprova um
        regulamento), "anexo I"; vários separados por ";".
        """
        escolhidos: list[int] = []
        for parte in _dividir_especificacao(especificacao):
            n = normalizar(parte)
            # espaço de numeração indicado
            esp: str | None = None
            if re.search(r"\badct\b|disposicoes (constitucionais )?transitorias", n):
                esp = "adct"
            elif re.search(r"\b(do |da )?(decreto|decreto-lei)\b(?! que)|\bdecreto\s*,", n) and \
                    any(b.espaco == "reg" for b in self.blocos):
                esp = ""
            elif re.search(r"\bregulamento\b", n):
                esp = "reg"
            ma = re.search(r"\banexo\s*([ivxlc]+|\d+|unico|[a-z])?\b", n)
            if ma:
                alvo = (ma.group(1) or "").upper()
                anexos = [e for e in self.espacos() if e.startswith("anexo:")]
                if (not alvo or alvo == "UNICO" or f"anexo:{alvo}" not in anexos) and len(anexos) == 1:
                    alvo = anexos[0].split(":", 1)[1]
                if not re.search(r"\bart", n):
                    escolhidos += [i for i, b in enumerate(self.blocos) if b.espaco == f"anexo:{alvo}"]
                    continue
                esp = f"anexo:{alvo}" if f"anexo:{alvo}" in anexos else esp  # "art. 1 do anexo II"
                n = n[:ma.start()] + " " + n[ma.end():]
            n = re.sub(r"\b(do|da)?\s*(adct|regulamento|decreto(-lei)?)\b\s*,?", " ", n)
            # intervalo "arts. 15 a 20" (com milhar "1.052 a 1.054")
            m = re.search(r"arts?\.?\s*(\d{1,3}(?:\.\d{3})+|\d+)\s*(?:-\s*([a-z])\b)?[º°o]?\s*(?:a|ao|at[ée])\s+"
                          r"(?:o\s+)?(?:art\.?\s*)?(\d{1,3}(?:\.\d{3})+|\d+)\s*(?:-\s*([a-z])\b)?", n)
            if m:
                ini = _ordem(m.group(1).replace(".", "") + (m.group(2) or ""))
                fim = _ordem(m.group(3).replace(".", "") + (m.group(4) or ""))
                e = esp if esp is not None else self._espaco_padrao(m.group(1).replace(".", ""))
                escolhidos += [i for i, b in enumerate(self.blocos)
                               if b.artigo and b.espaco == e and b.tipo not in ("estrutura", "rodape")
                               and ini <= _ordem(b.artigo) <= fim]
                continue
            caput = bool(re.search(r"\bcaput\b", n))
            chave = chave_dispositivo(n if re.search(r"\bart", n) else "art. " + n)
            if chave and chave[-1][0] == "caput":
                chave, caput = chave[:-1], True
            if not chave:
                continue
            art = chave[0][1]
            e = esp if esp is not None else self._espaco_padrao(art)
            if len(chave) == 1:
                for i, b in enumerate(self.blocos):
                    if b.artigo != art or b.espaco != e or b.tipo in ("estrutura", "rodape"):
                        continue
                    if caput and b.tipo == "dispositivo" and len(b.chave) > 1 and b.chave[1][0] == "par":
                        break  # caput acaba no primeiro parágrafo
                    escolhidos.append(i)
                continue
            dentro = False
            for i, b in enumerate(self.blocos):
                if b.espaco != e:
                    dentro = False
                    continue
                if b.tipo == "dispositivo":
                    dentro = dispositivo_contem(chave, b.chave)
                elif b.tipo in ("estrutura", "rodape") or b.artigo != art:
                    dentro = False
                if dentro:
                    escolhidos.append(i)
        return [self.blocos[i] for i in sorted(set(escolhidos))]

    def buscar(self, termo: str, max_artigos: int = 15) -> tuple[list[Bloco], int]:
        """Artigos (ou anexos) inteiros que contêm o termo, sem acento/caixa. Retorna (blocos, nº)."""
        alvo = normalizar(termo).strip('"')
        palavras = [p for p in re.split(r"\s+", alvo) if p]

        def unidade(b: Bloco) -> tuple | None:
            if b.tipo == "rodape":
                return None
            if b.espaco.startswith("anexo:"):
                return (b.espaco, None)
            return (b.espaco, b.artigo) if b.artigo else None

        def achar(cond) -> list[tuple]:
            achadas: list[tuple] = []
            for b in self.blocos:
                u = unidade(b)
                if u and not b.obsoleto and u not in achadas and cond(normalizar(b.vigente)):
                    achadas.append(u)
            return achadas

        # primeiro a expressão exata; se não houver, todas as palavras no mesmo parágrafo
        achadas = achar(lambda t: alvo in t)
        if not achadas and len(palavras) > 1 and not termo.strip().startswith('"'):
            achadas = achar(lambda t: all(p in t for p in palavras))
        sel = set(achadas[:max_artigos])
        return [b for b in self.blocos if unidade(b) in sel], len(achadas)

    def estrutura(self) -> list[dict]:
        """Sumário: livros/títulos/capítulos/seções com o intervalo de artigos de cada um."""
        out: list[dict] = []
        pilha: list[tuple[int, dict]] = []
        for b in self.blocos:
            if b.obsoleto or b.tipo == "rodape":
                continue
            if b.tipo == "estrutura":
                item = {"titulo": b.texto(), "primeiro_art": None, "ultimo_art": None}
                if b.espaco:
                    item["espaco"] = b.espaco
                nivel = _nivel_estrutura(item["titulo"])
                while pilha and pilha[-1][0] >= nivel:
                    pilha.pop()
                pilha.append((nivel, item))
                out.append(item)
            elif b.tipo == "dispositivo" and b.artigo:
                for _n, item in pilha:
                    item["primeiro_art"] = item["primeiro_art"] or b.artigo
                    item["ultimo_art"] = b.artigo
        return out

    # ----------------------------------------------------------- saída
    def renderizar(self, blocos: list[Bloco] | None = None, modo: str = "vigente",
                   notas: bool = True, omitir_revogados: bool = False) -> str:
        linhas: list[str] = []
        for b in self.blocos if blocos is None else blocos:
            if b.tipo == "estrutura_cont":
                continue
            if modo == "vigente" and b.obsoleto:
                continue
            if omitir_revogados and b.revogado:
                continue
            t = b.texto(modo, notas)
            if not t:
                continue
            if b.tipo == "estrutura":
                linhas.append("")
                linhas.append(f"## {t}")
            elif b.tipo == "citacao":
                linhas.append(f"    {t}")
            else:
                linhas.append(t)
        return "\n".join(linhas).strip()

    def tudo_riscado(self) -> bool:
        disp = [b for b in self.blocos if b.tipo == "dispositivo"]
        return bool(disp) and all(b.obsoleto for b in disp)


_NIVEIS = [("ANEXO", 0), ("ATO DAS", 0), ("REGULAMENTO", 0), ("CONSOLIDA", 0), ("PARTE", 1), ("LIVRO", 2),
           ("TITULO", 3), ("TÍTULO", 3), ("CAPITULO", 4), ("CAPÍTULO", 4), ("SECAO", 5), ("SEÇÃO", 5),
           ("SUBSECAO", 6), ("SUBSEÇÃO", 6)]


def _nivel_estrutura(titulo: str) -> int:
    t = titulo.upper()
    for prefixo, nivel in _NIVEIS:
        if t.startswith(prefixo):
            return nivel
    return 7


def _ordem(art: str) -> tuple[int, str]:
    m = re.match(r"(\d+)([a-z]*)", art or "")
    return (int(m.group(1)), m.group(2)) if m else (0, art or "")


def _dividir_especificacao(espec: str) -> list[str]:
    """Separa pedidos múltiplos e reordena "§ 1º do art. 44" -> "art. 44, § 1º"."""
    saida = []
    for parte in espec.split(";"):
        parte = parte.strip()
        if not parte:
            continue
        n = normalizar(parte)
        # "arts. 74 e 80", "arts. 1, 5 e 9"
        m = re.fullmatch(r"arts?\.?\s*((?:[\d.]+(?:-[a-z])?[º°o]?\s*(?:,|e)\s*)+[\d.]+(?:-[a-z])?[º°o]?)(.*)", n)
        if m and not re.search(r"\s(a|ao|ate)\s", m.group(1)):
            nums = re.split(r"\s*(?:,|\be\b)\s*", m.group(1))
            saida += [f"art. {x}{m.group(2)}" for x in nums if x]
            continue
        # "art. 74 e art. 80"
        if re.search(r"\be\s+art", n):
            saida += [p for p in re.split(r"\s*,?\s*\be\s+(?=art)", parte) if p.strip()]
            continue
        # "alínea a do inciso II do art. 3" -> "art. 3, inciso II, alínea a"
        pedacos = re.split(r"\s+d[oa]s?\s+(?=(?:art|§|inciso|par[aá]grafo|al[ií]nea|caput|item))", parte, flags=re.I)
        if len(pedacos) > 1:
            parte = ", ".join(reversed([p.strip(" ,") for p in pedacos]))
        saida.append(parte)
    return saida


# ======================================================================= parser

_BR = object()  # marcador de quebra de linha dentro de um parágrafo


class _Coletor:
    def __init__(self):
        self.blocos: list[Bloco] = []
        self.segmentos: list = []  # (texto, riscado) | _BR | ("#", nome da âncora)
        self.em_tr = 0

    def texto(self, s: str | None, riscado: bool) -> None:
        if s:
            self.segmentos.append((s, riscado))

    def descarregar(self) -> None:
        if not self.segmentos:
            return
        # divide em linhas nos <br>; uma linha só vira bloco novo se começar com rótulo
        grupos: list[list] = [[]]
        for s in self.segmentos:
            if s is _BR:
                grupos.append([])
            else:
                grupos[-1].append(s)
        juntos: list[list] = []
        for g in grupos:
            cru = _espacos("".join(x[0] for x in g if x[0] != "#"))
            if juntos and not (_inicio_dispositivo(cru) or _RE_ESTRUTURA.match(cru)):
                juntos[-1] += [(" ", False)] + g
            else:
                juntos.append(g)
        for g in juntos:
            ancoras = [x[1] for x in g if x[0] == "#"]
            segs = [x for x in g if x[0] != "#"]
            vig = _espacos("".join(s for s, r in segs if not r))
            partes = []
            for s, r in segs:
                partes.append(f"~~{s.strip()}~~ " if r and s.strip() else s)
            comp = _espacos(re.sub(r"~~\s*~~", " ", "".join(partes)))
            if comp:
                self.blocos.append(Bloco(vigente=vig, completo=comp, ancoras=ancoras))
        self.segmentos = []

    def percorrer(self, el, riscado: bool = False) -> None:
        tag = el.tag.lower() if isinstance(el.tag, str) else ""
        if not tag or tag in _IGNORAR:  # comentários, scripts, estilos
            self.texto(el.tail, riscado)
            return
        estilo = (el.get("style") or "").lower().replace(" ", "")
        r = riscado or tag in _RISCADOS or "line-through" in estilo
        bloco = tag in _BLOCOS and not self.em_tr
        if tag == "tr" and not self.em_tr:
            self.descarregar()
            self.em_tr += 1
            primeira = True
            for celula in el:
                if not isinstance(celula.tag, str):
                    continue
                if not primeira:
                    self.texto(" | ", r)
                primeira = False
                self.percorrer(celula, r)
            self.em_tr -= 1
            self.descarregar()
            self.texto(el.tail, riscado)
            return
        if bloco:
            self.descarregar()
        elif self.em_tr and tag in _BLOCOS:
            self.texto(" ", r)  # parágrafos dentro de células: separa com espaço
        if tag == "br":
            if self.em_tr:
                self.texto(" ", r)
            else:
                self.segmentos.append(_BR)
        if tag == "a" and el.get("name"):
            self.segmentos.append(("#", el.get("name")))
        self.texto(el.text, r)
        for filho in el:
            self.percorrer(filho, r)
        if bloco:
            self.descarregar()
        self.texto(el.tail, riscado)


def _espacos(s: str) -> str:
    s = s.replace("\xa0", " ").replace("​", "").replace("﻿", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"(?<=\S)\s+([,;:)])", r"\1", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"(?<=[\w)])\s+\.(?=\s|$)", ".", s)
    return s


def _sem_riscos(s: str) -> str:
    return _espacos(re.sub(r"~~[^~]*~~", " ", s))


def _inicio_dispositivo(s: str) -> bool:
    return bool(_casa_art(s) or _RE_PAR.match(s) or _RE_PU.match(s) or _RE_INC.match(s) or _RE_ALI.match(s))


def _casa_art(s: str):
    """Rótulo de artigo no início do bloco, rejeitando citações ("art. 13 da Lei nº...")."""
    m = _RE_ART.match(s)
    if not m:
        return None
    resto = s[m.end():]
    # depois do rótulo vem ponto, travessão, maiúscula, aspas ou parêntese; nunca "da Lei", ", ", " e art."
    if re.match(r"\s*(,|e\s+(\d|art)|d[aoe]s?\s|deste|desta|ao\s|à\s|a\s+\d|[a-zà-ÿ])", resto):
        return None
    return m


_ASPAS_ABRE = ("“", '"', "‘", "'", "«", "”")


def _fecha_citacao(t: str) -> bool:
    if re.search(r"\((NR|AC)\)\s*[.;]?\s*(\(.*\))?\s*$", t):
        return True
    fim = re.sub(r"(\s*\([^()]*\))+\s*$", "", t).rstrip(" .;")  # ignora notas finais
    if fim.endswith(("”", "»", "’")):
        return True
    if fim.endswith('"'):
        return t.count('"') % 2 == 1 or t.lstrip().startswith(("“", "”"))
    return False


def _juntar_fragmentos(blocos: list[Bloco]) -> list[Bloco]:
    """HTML mal aninhado parte um parágrafo no meio ("...o " <i>caput</i> " deste artigo"). Um bloco sem rótulo
    que começa em minúscula, ou com aspa seguida de minúscula, continua o anterior quando este não termina
    em pontuação final."""
    out: list[Bloco] = []
    for b in blocos:
        t = b.crua()
        if out and t and " | " not in t and not _inicio_dispositivo(t) and not _RE_ESTRUTURA.match(t):
            ant = out[-1]
            ta = ant.crua()
            continua = re.match(r"^([a-zà-ÿ]|[\"'“”]\s*[a-zà-ÿ(]|[,;)])", t) and not re.match(r"^[a-z]\)", t)
            if continua and ta and " | " not in ta and not re.search(r"[.;:!?]\s*$|\(NR\)\s*$", ta) and \
                    not _RE_ESTRUTURA.match(ta) and not _RE_PREAMBULO.match(ta):
                ant.vigente = _espacos(f"{ant.vigente} {b.vigente}")
                ant.completo = _espacos(f"{ant.completo} {b.completo}")
                ant.ancoras += b.ancoras
                continue
        out.append(b)
    return out


def _rotular(blocos: list[Bloco]) -> None:
    """Atribui espaço, artigo e dispositivo (art./§/inciso/alínea/item) a cada bloco."""
    art = par = inc = ali = None
    espaco = ""
    em_citacao = False
    alterador = False  # o artigo corrente introduz texto de outra norma
    rodape = False
    anexos_vistos = 0
    anexo_atual = ""
    anexos_com_artigos: list[str] = []
    reg_de = ""  # anexo cujo articulado virou "reg"

    def proximo_esperado(num: str) -> bool:
        if not art:
            return num == "1"
        a, _ = _ordem(art)
        return int(num) in (a, a + 1)

    anterior = ""
    for i, b in enumerate(blocos):
        base = b.vigente or ""
        if not (_inicio_dispositivo(base) or _RE_ESTRUTURA.match(base)):
            base = b.crua()
        texto_anterior, anterior = anterior, (base or anterior)
        # ---------------------------------------------------------- rodapé
        if _RE_ASSINATURA.match(base) and not em_citacao and art:
            rodape = True
            b.tipo = "assinatura"
            continue
        ma = _RE_ANEXO.match(base) if len(base) < 250 else None
        m = _casa_art(base)
        if rodape:
            # Depois da assinatura, só um anexo, o ADCT ou o regulamento/consolidação aprovado pela norma
            # (RPS, CLT: "REGULAMENTO DA ...", "LIVRO I", "Art. 1º") reabrem o articulado.
            titulo_reg = re.match(r"^(REGULAMENTO|CONSOLIDA[ÇC][ÃA]O|C[ÓO]DIGO)\b", base) and base[:40].isupper()
            if (titulo_reg or (_RE_ESTRUTURA.match(base) and not ma and not _RE_ADCT.match(base)) or
                    (m and m.group("num") == "1")) and _art1_adiante(blocos, i):
                rodape, espaco, art = False, "reg", None
                reg_de = anexo_atual or "I"
                par = inc = ali = None
                if titulo_reg:
                    b.tipo, b.espaco = "estrutura", espaco
                    continue
            elif not (ma or _RE_ADCT.match(base)):
                b.tipo = "rodape"
                continue
            rodape = False
        # ---------------------------------------------------------- citação de outra norma
        # Aspa só abre citação dentro de artigo que altera outra norma (ou depois de ":"); uma aspa solta
        # num parágrafo comum não pode engolir o resto do artigo.
        com_aspa = base.startswith(_ASPAS_ABRE) or base.startswith(("......", "…"))
        abre = com_aspa and (alterador or texto_anterior.rstrip().endswith(":"))
        if not em_citacao and alterador and m and not abre:
            num = m.group("num").replace(".", "")
            if not proximo_esperado(num):
                abre = True  # "Art. 27. ......" sem aspas dentro de artigo alterador
        if em_citacao or abre:
            if em_citacao and m and not base.startswith(_ASPAS_ABRE) and \
                    proximo_esperado(m.group("num").replace(".", "")) and \
                    int(m.group("num").replace(".", "")) == _ordem(art)[0] + 1:
                em_citacao = False  # aspas não fechadas e começou o próximo artigo
            else:
                b.tipo = "citacao"
                b.artigo, b.espaco = art, espaco
                em_citacao = not _fecha_citacao(base)
                continue
        if com_aspa:
            base = base.lstrip("“\"‘'«” ")  # aspa solta antes do rótulo
            m = _casa_art(base)
        # ---------------------------------------------------------- estrutura
        if (_RE_ESTRUTURA.match(base) or ma) and len(base) < 250 and not m:
            b.tipo = "estrutura"
            if _RE_ADCT.match(base):
                espaco, art = "adct", None
            elif ma:
                anexos_vistos += 1
                bruto = (ma.group(1) or "").upper().replace("Ú", "U")
                if bruto == "UNICO":
                    bruto = "I"
                if re.match(r"PROTOCOLO", base, re.I):
                    bruto = "PROTOCOLO"
                ident = bruto or ("I" if anexos_vistos == 1 else str(anexos_vistos))
                anexo_atual = ident
                espaco, art = f"anexo:{ident}", None
            par = inc = ali = None
            alterador = False
            b.espaco = espaco
            continue
        # ---------------------------------------------------------- dispositivos
        if m:
            num = m.group("num").replace(".", "")
            if espaco.startswith("anexo:") and num == "1":
                anexos_com_artigos.append(anexo_atual)
                if not reg_de:
                    espaco, reg_de = "reg", anexo_atual  # regulamento aprovado pelo decreto, publicado como anexo
            art = num + (m.group("suf") or "").replace("-", "").lower()
            par = inc = ali = None
            b.chave = (("art", art),)
            alterador = bool(_RE_ALTERADOR.search(base))
        elif (m := _RE_PAR.match(base)) and art:
            par = m.group("num") + (m.group("suf") or "").replace("-", "").lower()
            inc = ali = None
            b.chave = (("art", art), ("par", par))
            alterador = alterador or bool(_RE_ALTERADOR.search(base))
        elif _RE_PU.match(base) and art:
            par, inc, ali = "unico", None, None
            b.chave = (("art", art), ("par", par))
        elif (m := _RE_INC.match(base)) and art and romano_para_int(m.group(1)):
            inc = str(romano_para_int(m.group(1))) + (m.group(2) or "").lower()
            ali = None
            b.chave = (("art", art),) + ((("par", par),) if par else ()) + (("inc", inc),)
        elif (m := _RE_ALI.match(base)) and art:
            # alínea logo abaixo do caput (sem inciso/§) também existe: LINDB art. 15, DL 3.365 art. 5º
            ali = m.group(1) + (m.group(2) or "").lower()
            b.chave = (("art", art),) + ((("par", par),) if par else ()) + ((("inc", inc),) if inc else ()) + (("ali", ali),)
        elif (m := _RE_ITEM.match(base)) and art and (ali or inc or (not par and re.match(r"^\d{1,3}\s*[-–—]", base))):
            b.chave = (("art", art),) + ((("par", par),) if par else ()) + ((("inc", inc),) if inc else ()) \
                + ((("ali", ali),) if ali else ()) + (("item", m.group(1)),)
        else:
            b.artigo, b.espaco = art, espaco
            continue
        b.tipo = "dispositivo"
        b.artigo, b.espaco = art, espaco
        b.rotulo = rotulo_dispositivo(b.chave)

    # Vários anexos com articulado próprio (ex.: consolidação de convenções): cada um no seu espaço
    if len(set(anexos_com_artigos)) > 1 and reg_de:
        for b in blocos:
            if b.espaco == "reg":
                b.espaco = f"anexo:{reg_de}"


def _art1_adiante(blocos: list[Bloco], i: int, janela: int = 25) -> bool:
    for b in blocos[i:i + janela]:
        m = _casa_art(b.crua())
        if m:
            return m.group("num") == "1"
    return False


_RE_SO_ROTULO = re.compile(
    r"[^\s]+(\s+DAS\s+DISPOSI\S+)?\s+([IVXLCDM]+|\d+|[ÚU]NIC[OA]|[A-Z])[º°]?(-[A-Z])?\s*[.:\-–—]?|"
    r"PARTE\s+(GERAL|ESPECIAL)|LIVRO\s+COMPLEMENTAR", re.I)


def _ajustar_estrutura(blocos: list[Bloco]) -> None:
    """Junta "TÍTULO I" ao nome que vem no(s) parágrafo(s) seguinte(s); títulos curtos antes de um artigo
    pertencem a ele; notas soltas logo após um título ficam com o título."""
    for i, b in enumerate(blocos):
        if b.tipo == "estrutura":
            rot = b.vigente or b.crua()
            if not _RE_ESTRUTURA.match(b.vigente or "") and not _RE_ANEXO.match(b.vigente or ""):
                rot = _espacos(f"{b.crua().split('(')[0]} {' '.join(_RE_NOTA.findall(b.vigente))}")
            nucleo = _espacos(_RE_NOTA.sub(" ", rot))
            if _RE_SO_ROTULO.fullmatch(nucleo):
                # nome do título nas linhas seguintes (pode ter nota no meio e ocupar 2 linhas em maiúsculas)
                partes, usados = [], 0
                for prox in blocos[i + 1:i + 5]:
                    if prox.tipo != "texto" or not prox.vigente or prox.obsoleto:
                        if prox.obsoleto:
                            continue
                        break
                    v = prox.vigente
                    if not _sem_notas(v):  # só nota
                        prox.tipo, prox.artigo = "estrutura_nota", None
                        continue
                    sem_nota = _espacos(_RE_NOTA.sub(" ", v))
                    maiusc = sem_nota.upper() == sem_nota and len(sem_nota) < 200
                    curto = len(sem_nota) < 150 and not re.search(r"[;:]$", sem_nota) and \
                        (not sem_nota.endswith(".") or maiusc)
                    if not partes and curto or partes and maiusc and usados < 2:
                        partes.append(v)
                        usados += 1
                        prox.tipo, prox.artigo = "estrutura_cont", None
                        if not maiusc:
                            break
                    else:
                        break
                if partes:
                    rot = f"{rot} {' '.join(partes)}"
            b.titulo_estrutura = rot
            # nota da seção ("(Incluído pela EC 132)") não pertence ao próximo artigo
            for prox in blocos[i + 1:i + 3]:
                if prox.tipo == "texto" and prox.vigente and not _sem_notas(prox.vigente):
                    prox.artigo = None
                    prox.tipo = "estrutura_nota"
                elif prox.tipo not in ("estrutura_cont", "estrutura_nota"):
                    break
            continue
        if b.tipo != "texto" or not b.vigente or len(b.vigente) > 120 or re.search(r"[.;:,]$", b.vigente):
            continue
        # tratados: "Artigo 26" / "Pacta sunt servanda" / texto -> o título é do artigo que acabou de abrir
        ant = next((x for x in reversed(blocos[:i]) if x.vigente and not x.obsoleto), None)
        if ant is not None and ant.tipo == "dispositivo" and len(ant.chave) == 1 and \
                re.fullmatch(r"(Art|ART|Artigo|ARTIGO)\.?\s*[\d.]+\s*[º°o]?(-[A-Z])?\.?", ant.vigente.strip()):
            continue
        for prox in blocos[i + 1:i + 4]:
            if prox.obsoleto or not prox.vigente:
                continue
            if prox.tipo == "dispositivo" and len(prox.chave) == 1:
                b.artigo, b.espaco = prox.artigo, prox.espaco
            if prox.tipo != "texto":
                break


def _sem_notas(t: str) -> str:
    t = _RE_NOTA.sub(" ", t)
    t = _RE_LINKS_SOLTOS.sub(" ", t)
    return re.sub(r"[\s|.;:,\-–—*()]+", "", t)


def _classificar_notas(b: Bloco) -> None:
    b.notas = [_espacos(m.group(0)) for m in _RE_NOTA.finditer(b.vigente)]
    riscado = "~~" in b.completo
    if not b.vigente.strip():
        if b.completo.strip():
            b.obsoleto = True
        return
    conteudo = _sem_notas(b.vigente)
    rot = re.sub(r"[\s|.;:,\-–—*()]+", "", b.rotulo_original())
    if conteudo.startswith(rot):
        conteudo = conteudo[len(rot):]
    if len(conteudo) <= 3 and (riscado or b.tipo in ("dispositivo", "texto")):
        if any("revog" in n.lower() for n in b.notas) and b.tipo == "dispositivo":
            b.revogado = True
        elif riscado or not b.notas and not conteudo:
            b.obsoleto = True  # restos de redação riscada: "| |", "I -", "Vigência"


def ler_documento(conteudo: str, url: str, last_modified: str | None = None) -> Documento:
    # Há páginas com "</body></html>" no meio do arquivo; sem isso o parser descarta o resto.
    conteudo = re.sub(r"</\s*(body|html)\s*>", " ", conteudo, flags=re.I)
    doc = lhtml.fromstring(conteudo)
    col = _Coletor()
    col.percorrer(doc)
    col.descarregar()
    brutos = col.blocos

    # Cabeçalho: tudo antes do preâmbulo (ou do art. 1º). Considera também o texto riscado,
    # para normas inteiramente revogadas.
    inicio = 0
    for i, b in enumerate(brutos):
        t = b.crua().lstrip(" |")
        if _RE_PREAMBULO.match(t) or (i > 0 and _casa_art(t)):
            inicio = i
            break
    epigrafe = ""
    notas_gerais: list[str] = []
    ementa = None
    for b in brutos[:inicio]:
        texto = b.vigente or b.crua()
        celulas = [c.strip(" |") for c in texto.split(" | ")]
        # numa tabela de cabeçalho, a última célula longa é a ementa
        if len(celulas) > 1 and len(celulas[-1]) > 15 and not ementa and not _RE_EPIGRAFE.match(celulas[-1]) \
                and not _BOILERPLATE.match(celulas[-1]) and not celulas[-1].startswith("("):
            ementa = re.sub(r"\s*(Mensagem de [Vv]eto|Texto compilado)\s*$", "", celulas[-1])
            celulas = celulas[:-1]
        pedacos = []
        for p in celulas:
            # "Aprova o Regulamento..." não é o link "Regulamento"
            pedacos += re.split(r"(?<![a-zà-ÿ,])\s+(?=Regulamento|Vig[êe]ncia)|\s+(?=\(Vide|Texto compilado|"
                                r"Mensagem de [Vv]eto|Convers[ãa]o d|Produ[çc][ãa]o de efeito|Promulga[çc][ãa]o|"
                                r"Partes? mantid)", p)
        for parte in (p.strip(" |") for p in pedacos):
            parte = re.sub(r"\s*\($", "", parte)
            if not parte or _BOILERPLATE.match(parte):
                continue
            if not epigrafe and _RE_EPIGRAFE.match(parte) and len(parte) < 200:
                epigrafe = parte
            elif not ementa and _RE_INICIO_EMENTA.match(parte) and len(parte) > 15:
                ementa = parte
            else:
                notas_gerais.append(parte)
    blocos = [b for b in brutos[inicio:] if not _BOILERPLATE.match(b.crua())]
    corpo = blocos
    if blocos and _RE_PREAMBULO.match(blocos[0].crua().lstrip(" |")):
        blocos[0].tipo = "preambulo"
        corpo = blocos[1:]
    corpo = _juntar_fragmentos(corpo)
    if blocos and blocos[0].tipo == "preambulo":
        blocos = [blocos[0]] + corpo
    else:
        blocos = corpo
    _rotular(corpo)
    for b in corpo:
        _classificar_notas(b)
    _ajustar_estrutura(corpo)
    arquivos = set()
    for a in doc.iter("a"):
        h = (a.get("href") or "").split("#")[0].strip().lower()
        if h:
            arquivos.add(h.rsplit("/", 1)[-1])
    return Documento(url=url, epigrafe=epigrafe, ementa=ementa, notas_gerais=notas_gerais,
                     blocos=blocos, last_modified=last_modified, arquivos_citados=arquivos)


def citacoes_de_normas(texto: str) -> set[str]:
    """Extrai, de notas como "(Redação dada pela Lei nº 12.973, de 2014)", chaves "LEI:12973:2014"."""
    from ..referencias import interpretar

    out = set()
    padrao = re.compile(
        r"(Lei Complementar|Lei|Decreto-Lei|Decreto|Medida Provis[óo]ria|Emenda Constitucional)\s+n[º°o]?\.?\s*"
        r"(\d{1,3}(?:\.\d{3})*(?:-\d+)?)\s*,?\s*de\s+(?:\d{1,2}[º°]?\s+de\s+[a-zç]+\s+de\s+)?(\d{4})",
        re.I,
    )
    for m in padrao.finditer(texto):
        try:
            out.add(interpretar(f"{m.group(1)} {m.group(2)}/{m.group(3)}").chave)
        except ValueError:
            pass
    return out
