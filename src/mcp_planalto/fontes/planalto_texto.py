"""Leitura do texto das normas publicado no Planalto.

O Planalto publica o texto "multivigente": a redação atual convive com as
redações anteriores riscadas (<strike>) e com notas como "(Redação dada pela
Lei nº 12.973, de 2014)". Este módulo separa as duas coisas e organiza o texto
em blocos com o dispositivo (art., §, inciso, alínea) de cada um.
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
    r"Inconstitucional|Vide\s|Norma\s|Dispositivo|Par[áa]grafo\s+renumerad|Artigo\s+renumerad)"
    r"[^()]*(?:\([^()]*\)[^()]*)*\)",
    re.I,
)

_RE_ART = re.compile(r"^[Aa][Rr][Tt](?:igo|IGO)?\.?\s*(\d{1,3}(?:\.\d{3})+|\d+)\s*(?:º|°|o(?=\W))?\.?\s*(?:-([A-Z]{1,2})(?![A-Za-zÀ-ÿ]))?")
_RE_PAR = re.compile(r"^§\s*(\d+)\s*(?:º|°|o(?=\W))?\s*(?:-([A-Z]{1,2})(?![A-Za-zÀ-ÿ]))?")
_RE_PU = re.compile(r"^Par[áa]grafo\s+[úu]nico", re.I)
_RE_INC = re.compile(r"^([IVXLC]+)(?:\s*-\s*([A-Z])(?=\s*[-–—]))?\s*[-–—]")
_RE_ALI = re.compile(r"^([a-z])(?:\s*-\s*([A-Z]))?\s*\)")
_RE_ITEM = re.compile(r"^(\d{1,3})(?:\s*-\s*([A-Z]))?\s*[.)]\s")
_RE_ESTRUTURA = re.compile(
    r"^(LIVRO|PARTE|T[ÍI]TULO|CAP[ÍI]TULO|SE[ÇC][ÃA]O|SUBSE[ÇC][ÃA]O|ANEXO|ATO DAS DISPOSI)\b", re.I
)
_RE_PREAMBULO = re.compile(
    r"^(O|A)\s+(VICE[- ])?PRESIDENT[EA]\s+DA\s+REP[ÚU]BLICA|^O\s+CONGRESSO\s+NACIONAL|^As\s+Mesas\s+da\s+C[âa]mara|"
    r"^Fa[çc]o\s+saber|^O\s+PRESIDENTE\s+DO\s+SENADO|^N[óo]s,\s+representantes|^O\s+PRESIDENTE\s+DA\s+C[ÂA]MARA|"
    r"^OS\s+MINISTROS",
    re.I,
)
_RE_EPIGRAFE = re.compile(
    r"^(LEI|DECRETO|MEDIDA PROVIS[ÓO]RIA|EMENDA CONSTITUCIONAL|LEI COMPLEMENTAR|DECRETO-LEI|LEI DELEGADA|"
    r"CONSTITUI[ÇC][ÃA]O)\b.*\b(1[89]\d\d|20\d\d)\b",
    re.I,
)
_BOILERPLATE = re.compile(
    r"^(Presid[êe]ncia da Rep[úu]blica|Casa Civil|Subchefia|Secretaria[- ]Geral|Secretaria Especial|"
    r"Brastra|Este texto n[ãa]o substitui)",
    re.I,
)


@dataclass
class Bloco:
    vigente: str  # texto sem trechos riscados
    completo: str  # texto com trechos riscados entre ~~ ~~
    ancoras: list[str] = field(default_factory=list)
    tipo: str = "texto"  # "dispositivo", "estrutura", "texto", "preambulo", "epigrafe"
    chave: tuple = ()
    rotulo: str = ""
    notas: list[str] = field(default_factory=list)
    revogado: bool = False  # dispositivo revogado (só restou a nota)
    obsoleto: bool = False  # redação anterior inteiramente riscada
    artigo: str | None = None  # artigo a que o bloco pertence

    def texto(self, modo: str = "vigente", notas: bool = True) -> str:
        if modo == "historico":
            return self.completo
        if self.revogado:
            nota = next((n for n in self.notas if "revog" in n.lower()), "(Revogado)")
            base = f"{self.rotulo_original()} {nota}".strip()
            return base
        t = self.vigente
        if not notas:
            for n in self.notas:
                t = t.replace(n, "")
            t = re.sub(r"\s{2,}", " ", t).strip()
        return t

    def rotulo_original(self) -> str:
        crua = re.sub(r"~~", "", self.completo)
        m = re.match(r"^(Art\.?\s*[\d.]+[º°o]?(?:-[A-Z]{1,2}\b)?|§\s*\d+[º°o]?(?:-[A-Z]{1,2}\b)?|"
                     r"Par[áa]grafo\s+[úu]nico|[IVXLC]+(?:\s*-\s*[A-Z])?(?=\s*[-–—])|[a-z]\))", crua, re.I)
        return m.group(0).strip() if m else ""


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
    def artigos(self) -> list[str]:
        vistos, out = set(), []
        for b in self.blocos:
            if b.artigo and b.artigo not in vistos:
                vistos.add(b.artigo)
                out.append(b.artigo)
        return out

    def selecionar(self, especificacao: str) -> list[Bloco]:
        """Seleciona blocos por dispositivo: "art. 15", "arts. 15 a 20", "art. 2º, § 4º, III",
        vários separados por ";"."""
        escolhidos: list[int] = []
        for parte in re.split(r";|\be\s+(?=art)", especificacao):
            parte = parte.strip()
            if not parte:
                continue
            m = re.search(r"arts?\.?\s*(\d+)\s*(?:-\s*([a-z])\b)?[º°o]?\s*(?:a|ao|at[ée]|-)\s*(?:o\s+)?(?:art\.?\s*)?(\d+)\s*(?:-\s*([a-z])\b)?",
                          normalizar(parte))
            if m and m.group(3):
                ini = _ordem(m.group(1) + (m.group(2) or ""))
                fim = _ordem(m.group(3) + (m.group(4) or ""))
                escolhidos += [i for i, b in enumerate(self.blocos)
                               if b.artigo and ini <= _ordem(b.artigo) <= fim]
                continue
            chave = chave_dispositivo(parte if re.search(r"art", parte, re.I) else "art. " + parte)
            if not chave:
                continue
            if len(chave) == 1:
                escolhidos += [i for i, b in enumerate(self.blocos) if b.artigo == chave[0][1]
                               and b.tipo != "estrutura"]
                continue
            dentro = False
            for i, b in enumerate(self.blocos):
                if b.tipo == "dispositivo":
                    dentro = dispositivo_contem(chave, b.chave)
                elif b.tipo == "estrutura" or b.artigo != chave[0][1]:
                    dentro = False
                if dentro:
                    escolhidos.append(i)
        return [self.blocos[i] for i in sorted(set(escolhidos))]

    def buscar(self, termo: str, max_artigos: int = 15) -> tuple[list[Bloco], int]:
        """Artigos inteiros que contêm o termo (sem acento/caixa). Retorna (blocos, nº de artigos)."""
        alvo = normalizar(termo)
        palavras = [p for p in re.split(r"\s+", alvo) if p]
        arts: list[str] = []
        for b in self.blocos:
            if not b.artigo or b.obsoleto:
                continue
            t = normalizar(b.vigente)
            if all(p in t for p in palavras) and b.artigo not in arts:
                arts.append(b.artigo)
        sel = set(arts[:max_artigos])
        return [b for b in self.blocos if b.artigo in sel], len(arts)

    def estrutura(self) -> list[dict]:
        """Sumário: títulos/capítulos/seções com o intervalo de artigos de cada um."""
        out: list[dict] = []
        atual: dict | None = None
        for b in self.blocos:
            if b.obsoleto:
                continue
            if b.tipo == "estrutura":
                atual = {"titulo": b.vigente, "primeiro_art": None, "ultimo_art": None}
                out.append(atual)
            elif b.tipo == "dispositivo" and atual is not None and b.artigo:
                atual["primeiro_art"] = atual["primeiro_art"] or b.artigo
                atual["ultimo_art"] = b.artigo
        return out

    # ----------------------------------------------------------- saída
    def renderizar(self, blocos: list[Bloco] | None = None, modo: str = "vigente",
                   notas: bool = True, omitir_revogados: bool = False) -> str:
        linhas: list[str] = []
        for b in self.blocos if blocos is None else blocos:
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


def _ordem(art: str) -> tuple[int, str]:
    m = re.match(r"(\d+)([a-z]*)", art)
    return (int(m.group(1)), m.group(2)) if m else (0, art)


# ======================================================================= parser

class _Coletor:
    def __init__(self):
        self.blocos: list[Bloco] = []
        self.segmentos: list[tuple[str, bool]] = []
        self.ancoras: list[str] = []
        self.em_tr = 0

    def texto(self, s: str | None, riscado: bool) -> None:
        if s:
            self.segmentos.append((s, riscado))

    def descarregar(self) -> None:
        if not self.segmentos and not self.ancoras:
            return
        vig = _espacos("".join(s for s, r in self.segmentos if not r))
        partes = []
        for s, r in self.segmentos:
            if r and s.strip():
                partes.append(f"~~{s.strip()}~~ ")
            else:
                partes.append(s)
        comp = _espacos("".join(partes)).replace("~~ ~~", " ")
        if comp:
            self.blocos.append(Bloco(vigente=vig, completo=comp, ancoras=self.ancoras))
        self.segmentos, self.ancoras = [], []

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
                    self.texto(" | ", False)
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
            self.texto(" ", r)
        if tag == "a" and el.get("name"):
            self.ancoras.append(el.get("name"))
        self.texto(el.text, r)
        for filho in el:
            self.percorrer(filho, r)
        if bloco:
            self.descarregar()
        self.texto(el.tail, riscado)


def _espacos(s: str) -> str:
    s = s.replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", " ", s).strip()


def _sem_riscos(s: str) -> str:
    return _espacos(re.sub(r"~~[^~]*~~", " ", s))


_ASPAS_ABRE = ("“", '"', "‘", "'", "«")
_RE_FECHA_CITACAO = re.compile(r"(”|\"|’|»)\s*(\(NR\)|\(AC\))?\s*[.;]?\s*(\(.*\))?$|\((NR|AC)\)\s*[.;]?\s*(\(.*\))?$")


def _rotular(blocos: list[Bloco]) -> None:
    """Atribui dispositivo (art./§/inciso/alínea/item) a cada bloco.

    Trechos entre aspas (texto de outra norma, em leis que alteram leis) ficam
    como "citacao" e pertencem ao artigo que os introduz.
    """
    art = par = inc = ali = None
    em_citacao = False
    for b in blocos:
        base = b.vigente or ""
        if not _rotulo_match(base):
            base = re.sub(r"~~", "", b.completo)
        if em_citacao or base.startswith(_ASPAS_ABRE):
            m = _RE_ART.match(base)
            if em_citacao and m and art and m.group(1).isdigit() and art[:1].isdigit() \
                    and int(m.group(1).replace(".", "")) == _ordem(art)[0] + 1 and not base.startswith(_ASPAS_ABRE):
                em_citacao = False  # salvaguarda: aspas não fechadas e começou o próximo artigo
            else:
                b.tipo = "citacao"
                b.artigo = art
                em_citacao = not _RE_FECHA_CITACAO.search(base)
                continue
        if _RE_ESTRUTURA.match(base) and len(base) < 250 and not _RE_ART.match(base):
            b.tipo = "estrutura"
            if base.upper().startswith("ANEXO"):
                art = par = inc = ali = None
            continue
        m = _RE_ART.match(base)
        if m:
            art = m.group(1).replace(".", "") + (m.group(2) or "").lower()
            par = inc = ali = None
            b.chave = (("art", art),)
        elif (m := _RE_PAR.match(base)) and art:
            par = m.group(1) + (m.group(2) or "").lower()
            inc = ali = None
            b.chave = (("art", art), ("par", par))
        elif _RE_PU.match(base) and art:
            par, inc, ali = "unico", None, None
            b.chave = (("art", art), ("par", par))
        elif (m := _RE_INC.match(base)) and art and romano_para_int(m.group(1)):
            inc = str(romano_para_int(m.group(1))) + (m.group(2) or "").lower()
            ali = None
            b.chave = (("art", art),) + ((("par", par),) if par else ()) + (("inc", inc),)
        elif (m := _RE_ALI.match(base)) and art and (inc or par):
            ali = m.group(1) + (m.group(2) or "").lower()
            b.chave = (("art", art),) + ((("par", par),) if par else ()) + ((("inc", inc),) if inc else ()) + (("ali", ali),)
        elif (m := _RE_ITEM.match(base)) and art and (ali or inc):
            b.chave = (("art", art),) + ((("par", par),) if par else ()) + ((("inc", inc),) if inc else ()) \
                + ((("ali", ali),) if ali else ()) + (("item", m.group(1)),)
        else:
            b.artigo = art
            continue
        b.tipo = "dispositivo"
        b.artigo = art
        b.rotulo = rotulo_dispositivo(b.chave)


def _ligar_epigrafes_de_artigo(blocos: list[Bloco]) -> None:
    """Títulos curtos logo antes de um artigo ("Ajuste a Valor Presente") pertencem a ele."""
    for i, b in enumerate(blocos):
        if b.tipo != "texto" or not b.vigente or len(b.vigente) > 120 or re.search(r"[.;:,]$", b.vigente):
            continue
        for prox in blocos[i + 1:i + 4]:
            if prox.obsoleto or not prox.vigente:
                continue
            if prox.tipo == "dispositivo" and len(prox.chave) == 1:
                b.artigo = prox.artigo
            if prox.tipo != "texto":
                break


def _rotulo_match(s: str) -> bool:
    return bool(_RE_ART.match(s) or _RE_PAR.match(s) or _RE_PU.match(s) or _RE_INC.match(s)
                or _RE_ALI.match(s) or _RE_ESTRUTURA.match(s))


def _classificar_notas(b: Bloco) -> None:
    b.notas = [_espacos(m.group(0)) for m in _RE_NOTA.finditer(b.vigente)]
    resto = b.vigente
    for n in b.notas:
        resto = resto.replace(n, " ")
    resto = _espacos(resto)
    if not b.vigente.strip() and b.completo.strip():
        b.obsoleto = True
    elif b.tipo == "dispositivo" and any("revog" in n.lower() for n in b.notas):
        # Sobrou só o rótulo (ou nada) além da nota de revogação
        rot = b.rotulo_original()
        if not resto or resto == rot or len(resto) <= len(rot) + 3 or resto.lower() in ("(revogado)", "(revogada)"):
            b.revogado = True


def ler_documento(conteudo: str, url: str, last_modified: str | None = None) -> Documento:
    # Há páginas com "</body></html>" no meio do arquivo; sem isso o parser descarta o resto.
    conteudo = re.sub(r"</\s*(body|html)\s*>", " ", conteudo, flags=re.I)
    doc = lhtml.fromstring(conteudo)
    col = _Coletor()
    # Percorre a raiz inteira: há páginas com "</body></html>" no meio do arquivo,
    # e o restante do texto fica fora do <body> após o parse.
    col.percorrer(doc)
    col.descarregar()
    brutos = col.blocos

    # Cabeçalho: tudo antes do preâmbulo (ou do art. 1º)
    inicio = None
    for i, b in enumerate(brutos):
        t = _sem_riscos(b.completo)
        if _RE_PREAMBULO.match(t) or (inicio is None and _RE_ART.match(b.vigente or "") and i > 0):
            inicio = i
            break
    if inicio is None:
        inicio = 0
    epigrafe = ""
    notas_gerais: list[str] = []
    ementa = None
    for b in brutos[:inicio]:
        pedacos = []
        for p in b.vigente.split(" | "):
            pedacos += re.split(r"\s+(?=\(Vide|Texto compilado|Mensagem de [Vv]eto|Regulamento|Convers[ãa]o d|"
                                r"Vig[êe]ncia|Produ[çc][ãa]o de efeito|Promulga[çc][ãa]o|Partes? mantid)", p)
        for parte in (p.strip(" |") for p in pedacos):
            if not parte or _BOILERPLATE.match(parte):
                continue
            if not epigrafe and _RE_EPIGRAFE.match(parte) and len(parte) < 200:
                epigrafe = parte
            elif re.match(r"^(Disp[õo]e|Altera|Institui|Estabelece|Regulamenta|Aprova|Cria|Autoriza|Abre|"
                          r"Concede|Acrescenta|Revoga|Define|Prorroga|Fixa|Promulga|Torna|Denomina|Reajusta|"
                          r"Modifica|Inclui|Declara|Reconhece|Consolida|D[áa] nova|Organiza|Trata|Veda|"
                          r"Converte|Reabre|Regula|Determina|Ratifica|Introduz)\b", parte) and len(parte) > 30:
                ementa = ementa or parte
            else:
                notas_gerais.append(parte)
    blocos = []
    for b in brutos[inicio:]:
        t = _sem_riscos(b.completo)
        if _BOILERPLATE.match(t):
            continue
        blocos.append(b)
    if blocos and _RE_PREAMBULO.match(_sem_riscos(blocos[0].completo)):
        blocos[0].tipo = "preambulo"
        corpo_blocos = blocos[1:]
    else:
        corpo_blocos = blocos
    _rotular(corpo_blocos)
    _ligar_epigrafes_de_artigo(corpo_blocos)
    for b in corpo_blocos:
        _classificar_notas(b)
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
