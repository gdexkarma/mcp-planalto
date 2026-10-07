"""Cliente HTTP com cache em disco, revalidação condicional, limite de taxa e reintentos.

Usa apenas a biblioteca padrão: respeita HTTPS_PROXY/SSL_CERT_FILE do ambiente.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import random
import threading
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# Intervalo mínimo entre requisições ao mesmo host (segundos). Os portais são públicos
# e lentos; não queremos sobrecarregá-los nem ser bloqueados.
INTERVALO_HOST = {
    "www.planalto.gov.br": 0.35,
    "legis.senado.leg.br": 0.25,
}
INTERVALO_PADRAO = 0.5


class ErroHTTP(Exception):
    def __init__(self, url: str, status: int | None, mensagem: str):
        super().__init__(f"{mensagem} ({status}) em {url}" if status else f"{mensagem} em {url}")
        self.url = url
        self.status = status


@dataclass
class Resposta:
    url: str  # URL final, após redirecionamentos
    status: int
    corpo: bytes
    cabecalhos: dict[str, str]
    obtido_em: float  # epoch da última confirmação junto ao servidor
    do_cache: bool = False

    @property
    def last_modified(self) -> str | None:
        return self.cabecalhos.get("last-modified")

    def texto(self, codificacao: str | None = None) -> str:
        return decodificar(self.corpo, self.cabecalhos.get("content-type", ""), codificacao)


def decodificar(corpo: bytes, content_type: str = "", forcar: str | None = None) -> str:
    """Decodifica HTML/XML. O Planalto serve windows-1252 sem declarar; o Senado, UTF-8."""
    if forcar:
        return corpo.decode(forcar, "replace")
    ct = content_type.lower()
    if "charset=" in ct:
        cs = ct.split("charset=")[-1].split(";")[0].strip()
        try:
            return corpo.decode(cs)
        except (LookupError, UnicodeDecodeError):
            pass
    cabeca = corpo[:2048].lower()
    if b"charset=utf-8" in cabeca or b"encoding='utf-8'" in cabeca or b'encoding="utf-8"' in cabeca:
        try:
            return corpo.decode("utf-8")
        except UnicodeDecodeError:
            pass
    try:
        return corpo.decode("utf-8")
    except UnicodeDecodeError:
        return corpo.decode("cp1252", "replace")


class ClienteHTTP:
    def __init__(self, cache_dir: Path | None, user_agent: str, timeout: float = 45, tentativas: int = 5):
        self.cache_dir = cache_dir
        self.user_agent = user_agent
        self.timeout = timeout
        self.tentativas = tentativas
        self._ultimo: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- cache
    def _caminho(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        h = hashlib.sha256(url.encode()).hexdigest()
        return self.cache_dir / h[:2] / f"{h}.bin"

    def _ler_cache(self, url: str) -> Resposta | None:
        p = self._caminho(url)
        if not p or not p.exists():
            return None
        try:
            meta = json.loads(p.with_suffix(".json").read_text("utf-8"))
            return Resposta(
                url=meta["url"],
                status=meta["status"],
                corpo=gzip.decompress(p.read_bytes()),
                cabecalhos=meta["cabecalhos"],
                obtido_em=meta["obtido_em"],
                do_cache=True,
            )
        except Exception:  # cache corrompido: ignora
            return None

    def _gravar_cache(self, pedido_url: str, r: Resposta) -> None:
        p = self._caminho(pedido_url)
        if not p:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(r.corpo, 6))
        tmp.replace(p)
        meta = {"url": r.url, "status": r.status, "cabecalhos": r.cabecalhos, "obtido_em": r.obtido_em}
        p.with_suffix(".json").write_text(json.dumps(meta), "utf-8")

    # ---------------------------------------------------------------- rede
    def _aguardar_vez(self, host: str) -> None:
        with self._lock:
            lk = self._locks.setdefault(host, threading.Lock())
        with lk:
            intervalo = INTERVALO_HOST.get(host, INTERVALO_PADRAO)
            espera = self._ultimo.get(host, 0) + intervalo - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            self._ultimo[host] = time.monotonic()

    def get(self, url: str, max_idade: float | None = 0, aceitar: str | None = None) -> Resposta:
        """Busca `url`.

        max_idade: segundos em que o cache é aceito sem consultar o servidor.
          0 = sempre revalida (usa ETag/Last-Modified quando houver cache);
          None = cache vale para sempre.
        """
        cache = self._ler_cache(url)
        if cache and (max_idade is None or time.time() - cache.obtido_em <= max_idade):
            return cache

        cab = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": aceitar or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9",
        }
        if cache:
            if et := cache.cabecalhos.get("etag"):
                cab["If-None-Match"] = et
            if lm := cache.cabecalhos.get("last-modified"):
                cab["If-Modified-Since"] = lm

        host = urlsplit(url).hostname or ""
        ultimo_erro: Exception | None = None
        for tentativa in range(self.tentativas):
            self._aguardar_vez(host)
            try:
                req = urllib.request.Request(url, headers=cab)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    corpo = resp.read()
                    cabecalhos = {k.lower(): v for k, v in resp.headers.items()}
                    enc = cabecalhos.get("content-encoding", "")
                    if enc == "gzip":
                        corpo = gzip.decompress(corpo)
                    elif enc == "deflate":
                        corpo = zlib.decompress(corpo)
                    r = Resposta(resp.geturl(), resp.status, corpo, cabecalhos, time.time())
                    self._gravar_cache(url, r)
                    return r
            except urllib.error.HTTPError as e:
                if e.code == 304 and cache:
                    cache.obtido_em = time.time()
                    cache.do_cache = False
                    self._gravar_cache(url, cache)
                    return cache
                if e.code in (429, 500, 502, 503, 504):
                    ultimo_erro = e
                else:
                    raise ErroHTTP(url, e.code, "Erro HTTP") from e
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
                ultimo_erro = e
            pausa = min(30, (2**tentativa) + random.random())
            log.info("Falha em %s (%s); nova tentativa em %.1fs", url, ultimo_erro, pausa)
            time.sleep(pausa)

        if cache:  # rede indisponível: devolve a última cópia conhecida
            log.warning("Usando cópia em cache de %s após falhas: %s", url, ultimo_erro)
            return cache
        raise ErroHTTP(url, getattr(ultimo_erro, "code", None), f"Falha de rede: {ultimo_erro}")
