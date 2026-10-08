"""Índice local (SQLite + FTS5) do catálogo de normas e do grafo de alterações."""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Iterator

from .referencias import Referencia, normalizar

ESQUEMA = """
CREATE TABLE IF NOT EXISTS normas (
    chave TEXT PRIMARY KEY,
    tipo TEXT NOT NULL,
    numero TEXT NOT NULL,
    ano INTEGER,
    data TEXT,                 -- assinatura, AAAA-MM-DD
    ementa TEXT,
    apelido TEXT,
    situacao TEXT,             -- ex.: "Revogada", "Em tramitação", "Convertida na Lei ..."
    url_planalto TEXT,
    senado_id TEXT,
    urn TEXT,
    indexacao TEXT,            -- termos do tesauro do Senado (separados por " ; ")
    catalogo TEXT,
    observacao TEXT,
    publicacao TEXT,
    origem TEXT,               -- "planalto", "senado" ou "planalto+senado"
    atualizado_em REAL,
    detalhe_em REAL            -- quando o detalhe do Senado (vides/indexação) foi obtido
);
CREATE INDEX IF NOT EXISTS ix_normas_tipo_ano ON normas(tipo, ano);
CREATE INDEX IF NOT EXISTS ix_normas_data ON normas(data);
CREATE INDEX IF NOT EXISTS ix_normas_senado ON normas(senado_id);

CREATE VIRTUAL TABLE IF NOT EXISTS normas_fts USING fts5(
    chave UNINDEXED, nome, ementa, apelido, indexacao,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- Grafo de alterações: `origem` (norma posterior) age sobre `destino` (norma anterior).
CREATE TABLE IF NOT EXISTS relacoes (
    origem TEXT NOT NULL,
    destino TEXT NOT NULL,
    declaracao TEXT,           -- "Declaração de Alteração Permanente", "... Revogação ... no Todo"
    acao TEXT NOT NULL DEFAULT '',  -- "Alteração", "Acréscimo", "Revogação", "Regulamentado" ...
    dispositivo TEXT NOT NULL DEFAULT '',  -- dispositivo da norma de destino, se houver
    data TEXT,                 -- assinatura da norma de origem
    origem_senado_id TEXT,
    destino_senado_id TEXT,
    PRIMARY KEY (origem, destino, declaracao, acao, dispositivo)
);
CREATE INDEX IF NOT EXISTS ix_rel_destino ON relacoes(destino);

CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT);

-- Medidas provisórias reeditadas (antes da EC 32/2001): número da edição -> última edição da família
CREATE TABLE IF NOT EXISTS mp_reedicoes (numero TEXT PRIMARY KEY, chave_final TEXT NOT NULL);
"""

CAMPOS = [
    "chave", "tipo", "numero", "ano", "data", "ementa", "apelido", "situacao", "url_planalto",
    "senado_id", "urn", "indexacao", "catalogo", "observacao", "publicacao", "origem",
    "atualizado_em", "detalhe_em",
]


@dataclass
class Norma:
    chave: str
    tipo: str
    numero: str
    ano: int | None = None
    data: str | None = None
    ementa: str | None = None
    apelido: str | None = None
    situacao: str | None = None
    url_planalto: str | None = None
    senado_id: str | None = None
    urn: str | None = None
    indexacao: str | None = None
    catalogo: str | None = None
    observacao: str | None = None
    publicacao: str | None = None
    origem: str | None = None
    atualizado_em: float | None = None
    detalhe_em: float | None = None

    @property
    def ref(self) -> Referencia:
        return Referencia(self.tipo, self.numero, self.ano)

    @property
    def nome(self) -> str:
        return self.ref.nome

    def resumo(self) -> dict:
        d = {
            "norma": self.nome,
            "chave": self.chave,
            "data": self.data,
            "ementa": self.ementa,
        }
        for k in ("apelido", "situacao", "url_planalto"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        return d

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Relacao:
    origem: str
    destino: str
    declaracao: str = ""
    acao: str = ""
    dispositivo: str = ""
    data: str | None = None
    origem_senado_id: str | None = None
    destino_senado_id: str | None = None


def _texto_fts(s: str | None) -> str:
    return s or ""


def consulta_fts(texto: str) -> str:
    """Converte texto livre em expressão FTS5 segura.

    - "frase entre aspas" vira frase exata
    - OU / OR entre termos vira alternativa
    - termo* mantém prefixo
    - demais termos são combinados com AND
    """
    texto = texto.strip()
    if not texto:
        return ""
    grupos: list[list[str]] = [[]]
    for m in re.finditer(r'"([^"]+)"|(\S+)', texto):
        if m.group(1):
            palavras = re.findall(r"[\w]+", normalizar(m.group(1)))
            if palavras:
                grupos[-1].append('"' + " ".join(palavras) + '"')
            continue
        tok = m.group(2)
        if tok.upper() in ("OU", "OR", "|"):
            grupos.append([])
            continue
        if tok.upper() in ("AND", "E", "NOT", "NEAR"):
            continue
        prefixo = tok.endswith("*")
        palavras = [p for p in re.findall(r"[\w]+", normalizar(tok)) if len(p) > 1 or p.isdigit()]
        if not palavras:
            continue
        if len(palavras) == 1:
            grupos[-1].append(f'"{palavras[0]}"' + ("*" if prefixo else ""))
        else:  # "IRPJ/CSLL", "lucro-real" -> frase
            grupos[-1].append('"' + " ".join(palavras) + '"')
    partes = ["(" + " AND ".join(g) + ")" for g in grupos if g]
    return " OR ".join(partes)


class Banco:
    def __init__(self, caminho: Path | str):
        self.caminho = str(caminho)
        self._local = threading.local()
        self._escrita = threading.Lock()
        self.conexao().executescript(ESQUEMA)

    def conexao(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.caminho, timeout=60, check_same_thread=False, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.c = c
        return c

    @contextlib.contextmanager
    def _transacao(self):
        """Transação de escrita exclusiva (BEGIN IMMEDIATE): evita corrida entre threads e processos
        (ex.: CLI sincronizando enquanto o servidor roda)."""
        with self._escrita:
            c = self.conexao()
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")

    # ------------------------------------------------------------ meta
    def meta(self, chave: str, padrao: str | None = None) -> str | None:
        r = self.conexao().execute("SELECT valor FROM meta WHERE chave=?", (chave,)).fetchone()
        return r[0] if r else padrao

    def set_meta(self, chave: str, valor) -> None:
        with self._transacao() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (chave, str(valor)))

    # ------------------------------------------------------------ normas
    def _linha(self, r: sqlite3.Row | None) -> Norma | None:
        return Norma(**{k: r[k] for k in CAMPOS}) if r else None

    def obter(self, chave: str) -> Norma | None:
        r = self.conexao().execute("SELECT * FROM normas WHERE chave=?", (chave,)).fetchone()
        return self._linha(r)

    def obter_por_senado_id(self, senado_id: str) -> Norma | None:
        r = self.conexao().execute("SELECT * FROM normas WHERE senado_id=?", (senado_id,)).fetchone()
        return self._linha(r)

    def candidatos(self, ref: Referencia) -> list[Norma]:
        """Normas com o mesmo tipo e número (para quando o ano não foi informado)."""
        sql = "SELECT * FROM normas WHERE tipo=? AND (numero=? OR numero LIKE ?)"
        args: list = [ref.tipo, ref.numero, ref.numero + "-%"]
        if ref.ano:
            sql += " AND ano=?"
            args.append(ref.ano)
        sql += " ORDER BY ano DESC, numero DESC"
        return [self._linha(r) for r in self.conexao().execute(sql, args)]

    def salvar(self, normas: Iterable[Norma], sobrescrever_vazios: bool = False,
               substituir: Iterable[str] = ()) -> int:
        """Insere ou mescla normas. Campos None não apagam valores existentes, salvo os listados em
        `substituir` (ex.: "situacao", quando o Senado deixa de apontar revogação)."""
        substituir = set(substituir)
        n = 0
        with self._transacao() as c:
            for nova in normas:
                linha = c.execute("SELECT rowid, * FROM normas WHERE chave=?", (nova.chave,)).fetchone()
                if linha:
                    atual = self._linha(linha)
                    for k in CAMPOS:
                        v = getattr(nova, k)
                        if k in substituir:
                            setattr(atual, k, v or None)
                        elif v is not None and (v != "" or sobrescrever_vazios):
                            if k == "origem" and atual.origem:
                                v = "+".join(sorted(set(atual.origem.split("+")) | set(v.split("+"))))
                            setattr(atual, k, v)
                    nova = atual
                    rowid = linha["rowid"]
                    c.execute(
                        f"UPDATE normas SET {','.join(k + '=?' for k in CAMPOS[1:])} WHERE rowid=?",
                        [getattr(nova, k) for k in CAMPOS[1:]] + [rowid],
                    )
                    c.execute("DELETE FROM normas_fts WHERE rowid=?", (rowid,))
                else:
                    rowid = c.execute(
                        f"INSERT INTO normas ({','.join(CAMPOS)}) VALUES ({','.join('?' * len(CAMPOS))})",
                        [getattr(nova, k) for k in CAMPOS],
                    ).lastrowid
                # O índice FTS usa o mesmo rowid da tabela normas (remoção e junção rápidas).
                c.execute(
                    "INSERT INTO normas_fts (rowid, chave, nome, ementa, apelido, indexacao) VALUES (?,?,?,?,?,?)",
                    (
                        rowid,
                        nova.chave,
                        nova.nome + " " + nova.numero,
                        _texto_fts(nova.ementa),
                        _texto_fts(nova.apelido),
                        _texto_fts(nova.indexacao) + " " + _texto_fts(nova.catalogo),
                    ),
                )
                n += 1
        return n

    def buscar(
        self,
        consulta: str,
        tipos: list[str] | None = None,
        ano_inicio: int | None = None,
        ano_fim: int | None = None,
        limite: int = 30,
        deslocamento: int = 0,
    ) -> tuple[list[tuple[Norma, float]], int]:
        """Busca no catálogo. Retorna ([(norma, pontuação)], total)."""
        expr = consulta_fts(consulta)
        limite = max(1, min(int(limite), 1000))
        deslocamento = max(0, int(deslocamento))
        if consulta.strip() and not expr:
            return [], 0  # só operadores/símbolos: nada a buscar (não devolve o catálogo inteiro)
        filtros, args = [], []
        if tipos:
            filtros.append(f"n.tipo IN ({','.join('?' * len(tipos))})")
            args += tipos
        if ano_inicio:
            filtros.append("n.ano >= ?")
            args.append(ano_inicio)
        if ano_fim:
            filtros.append("n.ano <= ?")
            args.append(ano_fim)
        c = self.conexao()
        if expr:
            where = " AND ".join(["normas_fts MATCH ?"] + filtros)
            base = f"FROM normas_fts f JOIN normas n ON n.rowid = f.rowid WHERE {where}"
            total = c.execute(f"SELECT count(*) {base}", [expr] + args).fetchone()[0]
            rows = c.execute(
                f"SELECT n.*, bm25(normas_fts, 0, 2.0, 1.0, 3.0, 1.5) AS score {base} "
                "ORDER BY score, n.data DESC LIMIT ? OFFSET ?",
                [expr] + args + [limite, deslocamento],
            ).fetchall()
            return [(self._linha(r), -r["score"]) for r in rows], total
        where = (" WHERE " + " AND ".join(filtros)) if filtros else ""
        total = c.execute(f"SELECT count(*) FROM normas n{where}", args).fetchone()[0]
        rows = c.execute(
            f"SELECT n.* FROM normas n{where} ORDER BY n.data DESC, n.numero DESC LIMIT ? OFFSET ?",
            args + [limite, deslocamento],
        ).fetchall()
        return [(self._linha(r), 0.0) for r in rows], total

    def recentes(self, desde: str, tipos: list[str] | None = None) -> list[Norma]:
        sql = "SELECT * FROM normas WHERE data >= ?"
        args: list = [desde]
        if tipos:
            sql += f" AND tipo IN ({','.join('?' * len(tipos))})"
            args += tipos
        sql += " ORDER BY data DESC, tipo, numero DESC"
        return [self._linha(r) for r in self.conexao().execute(sql, args)]

    def iterar(self, tipos: list[str] | None = None, ano_inicio: int | None = None,
               sem_detalhe: bool = False) -> Iterator[Norma]:
        sql, args = "SELECT * FROM normas WHERE 1=1", []
        if tipos:
            sql += f" AND tipo IN ({','.join('?' * len(tipos))})"
            args += tipos
        if ano_inicio:
            sql += " AND ano >= ?"
            args.append(ano_inicio)
        if sem_detalhe:
            sql += " AND detalhe_em IS NULL"
        sql += " ORDER BY ano DESC, numero DESC"
        for r in self.conexao().execute(sql, args).fetchall():
            yield self._linha(r)

    def estatisticas(self) -> dict:
        c = self.conexao()
        por_tipo = {r[0]: r[1] for r in c.execute("SELECT tipo, count(*) FROM normas GROUP BY tipo")}
        com_detalhe = c.execute("SELECT count(*) FROM normas WHERE detalhe_em IS NOT NULL").fetchone()[0]
        rel = c.execute("SELECT count(*) FROM relacoes").fetchone()[0]
        mais_recente = c.execute("SELECT max(data) FROM normas").fetchone()[0]
        return {
            "normas_por_tipo": por_tipo,
            "total_normas": sum(por_tipo.values()),
            "normas_com_detalhe_senado": com_detalhe,
            "relacoes_de_alteracao": rel,
            "norma_mais_recente": mais_recente,
        }

    # ------------------------------------------------------------ reedições de MP
    def salvar_reedicoes(self, mapa: dict[str, str]) -> None:
        with self._transacao() as c:
            c.executemany("INSERT OR REPLACE INTO mp_reedicoes VALUES (?,?)", list(mapa.items()))

    def familia_mp(self, numero: str) -> str | None:
        """Última edição da família da MP. Segue cadeias (528 -> 878 -> 1.472-31)."""
        c = self.conexao()
        atual, vistos = None, set()
        while numero and numero not in vistos and len(vistos) < 10:
            vistos.add(numero)
            r = c.execute("SELECT chave_final FROM mp_reedicoes WHERE numero=?", (numero,)).fetchone()
            if not r and "-" not in numero and atual is None:
                # originária que não aparece como link: usa a família de qualquer edição dela ("1991-15")
                r = c.execute("SELECT chave_final FROM mp_reedicoes WHERE numero LIKE ? LIMIT 1",
                              (numero + "-%",)).fetchone()
            if not r or r[0] == atual:
                break
            atual = r[0]
            numero = atual.split(":")[1]
        return atual

    def senado_id(self, chave: str) -> str | None:
        r = self.conexao().execute(
            "SELECT origem_senado_id FROM relacoes WHERE origem=? AND origem_senado_id IS NOT NULL "
            "UNION SELECT destino_senado_id FROM relacoes WHERE destino=? AND destino_senado_id IS NOT NULL LIMIT 1",
            (chave, chave),
        ).fetchone()
        return r[0] if r else None

    # ------------------------------------------------------------ relações
    def salvar_relacoes(self, relacoes: Iterable[Relacao], substituir_de: list[tuple[str, str]] = ()) -> None:
        """substituir_de: [(coluna, chave)] cujas relações são apagadas antes (re-sincronização)."""
        with self._transacao() as c:
            for coluna, chave in substituir_de:
                assert coluna in ("origem", "destino")
                c.execute(f"DELETE FROM relacoes WHERE {coluna}=?", (chave,))
            c.executemany(
                "INSERT OR REPLACE INTO relacoes VALUES (?,?,?,?,?,?,?,?)",
                [
                    (r.origem, r.destino, r.declaracao or "", r.acao or "", r.dispositivo or "", r.data,
                     r.origem_senado_id, r.destino_senado_id)
                    for r in relacoes
                ],
            )

    def relacoes(self, destino: str | None = None, origem: str | None = None) -> list[Relacao]:
        sql, args = "SELECT * FROM relacoes WHERE 1=1", []
        if destino:
            sql += " AND destino=?"
            args.append(destino)
        if origem:
            sql += " AND origem=?"
            args.append(origem)
        sql += " ORDER BY data, origem"
        return [Relacao(**dict(r)) for r in self.conexao().execute(sql, args)]

    def contar_alteradoras(self, chaves: list[str]) -> dict[str, int]:
        if not chaves:
            return {}
        q = ",".join("?" * len(chaves))
        return {
            r[0]: r[1]
            for r in self.conexao().execute(
                f"SELECT destino, count(DISTINCT origem) FROM relacoes WHERE destino IN ({q}) GROUP BY destino",
                chaves,
            )
        }


def agora() -> float:
    return time.time()


def json_compacto(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
