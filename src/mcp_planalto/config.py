"""Configuração por variáveis de ambiente.

MCP_PLANALTO_HOME        diretório de dados (padrão: ~/.mcp-planalto)
MCP_PLANALTO_CACHE_HORAS validade do cache HTTP de páginas de texto (padrão: 24)
MCP_PLANALTO_AUTO_SYNC   "0" desliga a atualização automática do catálogo ao iniciar
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_float(nome: str, padrao: float) -> float:
    try:
        return float(os.environ.get(nome, padrao))
    except ValueError:
        return padrao


@dataclass
class Config:
    home: Path = field(
        default_factory=lambda: Path(
            os.environ.get("MCP_PLANALTO_HOME", Path.home() / ".mcp-planalto")
        ).expanduser()
    )
    cache_horas: float = field(default_factory=lambda: _env_float("MCP_PLANALTO_CACHE_HORAS", 24))
    auto_sync: bool = field(default_factory=lambda: os.environ.get("MCP_PLANALTO_AUTO_SYNC", "1") != "0")
    # O Planalto recusa conexões de User-Agents que não começam com "Mozilla/5.0".
    user_agent: str = "Mozilla/5.0 (compatible; mcp-planalto/0.1; +https://github.com/gdexkarma/mcp-planalto)"

    @property
    def db_path(self) -> Path:
        return self.home / "legislacao.db"

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"

    @property
    def export_dir(self) -> Path:
        return self.home / "exportacoes"

    def preparar(self) -> "Config":
        for p in (self.home, self.cache_dir, self.export_dir):
            p.mkdir(parents=True, exist_ok=True)
        return self
