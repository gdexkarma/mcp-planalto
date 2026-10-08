"""Sincronização automática ao iniciar o servidor."""

import time

from mcp_planalto import server


class _Db:
    def __init__(self, meta):
        self._meta = meta

    def meta(self, chave, padrao=None):
        return self._meta.get(chave, padrao)


class _Cfg:
    auto_sync = True


class _Servico:
    def __init__(self, meta):
        self.db, self.config, self.chamadas = _Db(meta), _Cfg(), []

    def sincronizar_catalogo(self):
        self.chamadas.append("catalogo")

    def atualizar_recentes(self):
        self.chamadas.append("recentes")

    def sincronizar_detalhes(self, limite=None):
        self.chamadas.append(("detalhes", limite))
        return {"processadas": 0}


def _rodar(monkeypatch, meta):
    s = _Servico(meta)
    monkeypatch.setattr(server, "servico", lambda: s)
    server._auto_sincronizar()
    return s.chamadas


def test_detalhes_incrementais_so_depois_da_primeira_carga(monkeypatch):
    agora = time.time()
    # nunca rodou --detalhes: não dispara a carga longa sozinho
    assert _rodar(monkeypatch, {"catalogo_em": "1", "recentes_em": str(agora)}) == []
    # carga recente: nada a fazer
    assert _rodar(monkeypatch, {"catalogo_em": "1", "recentes_em": str(agora), "detalhes_em": str(agora)}) == []
    # carga com mais de 3 dias: completa as normas novas
    velho = str(agora - 4 * 24 * 3600)
    assert _rodar(monkeypatch, {"catalogo_em": "1", "recentes_em": velho, "detalhes_em": velho}) == \
        ["recentes", ("detalhes", 3000)]
