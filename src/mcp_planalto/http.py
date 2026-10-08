"""Cliente HTTP com cache em disco, revalidação condicional, limite de taxa e reintentos.

Usa apenas a biblioteca padrão: respeita HTTPS_PROXY/SSL_CERT_FILE do ambiente.
"""

from __future__ import annotations

import gzip
import http.client
import os
import tempfile
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
    if corpo.startswith(b"\xef\xbb\xbf"):
        return corpo[3:].decode("utf-8", "replace")
    ct = content_type.lower()
    if "charset=" in ct:
        cs = ct.split("charset=")[-1].split(";")[0].strip().strip('"')
        if cs in ("iso-8859-1", "latin-1", "latin1", "us-ascii", "ascii"):
            cs = "cp1252"  # na prática os portais servem windows-1252
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
    """Cliente com cache em disco (um arquivo por URL, gravado de forma atômica), revalidação por
    ETag/Last-Modified, intervalo mínimo por host, reintentos com espera exponencial e falha rápida
    quando um portal está fora do ar."""

    MAGICO = b"MCPP1"

    def __init__(self, cache_dir: Path | None, user_agent: str, timeout: float = 30, tentativas: int = 4,
                 prazo_total: float = 90):
        self.cache_dir = cache_dir
        self.user_agent = user_agent
        self.timeout = timeout
        self.tentativas = tentativas
        self.prazo_total = prazo_total
        self._ultimo: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._falhas: dict[str, tuple[int, float]] = {}  # host -> (falhas seguidas, quando)
        self._lock = threading.Lock()
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- cache
    def _caminho(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        h = hashlib.sha256(url.encode()).hexdigest()
        return self.cache_dir / h[:2] / f"{h}.cache"

    def _ler_cache(self, url: str) -> Resposta | None:
        p = self._caminho(url)
        if not p or not p.exists():
            return None
        try:
            dados = p.read_bytes()
            if not dados.startswith(self.MAGICO):
                return None
            tam = int.from_bytes(dados[5:9], "big")
            meta = json.loads(dados[9:9 + tam].decode("utf-8"))
            corpo = gzip.decompress(dados[9 + tam:])
            if hashlib.sha256(corpo).hexdigest() != meta.get("sha256"):
                return None
            return Resposta(url=meta["url"], status=meta["status"], corpo=corpo, cabecalhos=meta["cabecalhos"],
                            obtido_em=meta["obtido_em"], do_cache=True)
        except Exception:  # cache corrompido ou de versão antiga: ignora
            return None

    def _gravar_cache(self, pedido_url: str, r: Resposta) -> None:
        p = self._caminho(pedido_url)
        if not p:
            return
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            meta = json.dumps({"url": r.url, "status": r.status, "cabecalhos": r.cabecalhos,
                               "obtido_em": r.obtido_em, "sha256": hashlib.sha256(r.corpo).hexdigest()}).encode()
            fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(self.MAGICO + len(meta).to_bytes(4, "big") + meta + gzip.compress(r.corpo, 6))
                os.replace(tmp, p)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:  # disco cheio etc.: o download continua valendo
            log.warning("Não foi possível gravar o cache de %s: %s", pedido_url, e)

    def esquecer(self, url: str) -> None:
        p = self._caminho(url)
        if p:
            try:
                p.unlink()
            except OSError:
                pass

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

    def _host_fora(self, host: str) -> bool:
        n, quando = self._falhas.get(host, (0, 0.0))
        return n >= 3 and time.monotonic() - quando < 120

    def _registrar(self, host: str, ok: bool) -> None:
        with self._lock:
            if ok:
                self._falhas.pop(host, None)
            else:
                n, _ = self._falhas.get(host, (0, 0.0))
                self._falhas[host] = (n + 1, time.monotonic())

    def get(self, url: str, max_idade: float | None = 0, aceitar: str | None = None) -> Resposta:
        """Busca `url`.

        max_idade: segundos em que o cache é aceito sem consultar o servidor.
          0 = sempre revalida (usa ETag/Last-Modified quando houver cache);
          None = cache vale para sempre.
        Se o servidor falhar, devolve a última cópia em cache, quando houver.
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
        tentativas = 1 if self._host_fora(host) else self.tentativas
        limite = time.monotonic() + self.prazo_total
        ultimo_erro: Exception | None = None
        status_erro: int | None = None
        for tentativa in range(tentativas):
            self._aguardar_vez(host)
            espera_servidor = None
            try:
                req = urllib.request.Request(url, headers=cab)
                timeout = max(5.0, min(self.timeout, limite - time.monotonic()))
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    corpo = resp.read()
                    cabecalhos = {k.lower(): v for k, v in resp.headers.items()}
                    corpo = _descomprimir(corpo, cabecalhos.get("content-encoding", ""))
                    r = Resposta(resp.geturl(), resp.status, corpo, cabecalhos, time.time())
                self._registrar(host, True)
                self._gravar_cache(url, r)
                return r
            except urllib.error.HTTPError as e:
                if e.code == 304 and cache:
                    self._registrar(host, True)
                    cache.obtido_em = time.time()
                    cache.do_cache = False
                    self._gravar_cache(url, cache)
                    return cache
                if e.code not in (408, 429, 500, 502, 503, 504):
                    raise ErroHTTP(url, e.code, "Erro HTTP") from e
                ultimo_erro, status_erro = e, e.code
                ra = e.headers.get("Retry-After") if e.headers else None
                if ra and ra.strip().isdigit():
                    espera_servidor = min(60.0, float(ra))
            except (urllib.error.URLError, http.client.HTTPException, zlib.error, EOFError, OSError) as e:
                ultimo_erro = e
            if tentativa == tentativas - 1:
                break
            pausa = espera_servidor or min(20.0, (2 ** tentativa) + random.random())
            if time.monotonic() + pausa >= limite:
                break
            log.info("Falha em %s (%s); nova tentativa em %.1fs", url, ultimo_erro, pausa)
            time.sleep(pausa)

        self._registrar(host, False)
        if cache:  # rede indisponível: devolve a última cópia conhecida
            log.warning("Usando cópia em cache de %s após falhas: %s", url, ultimo_erro)
            return cache
        raise ErroHTTP(url, status_erro, f"Portal indisponível ({ultimo_erro})")


def _descomprimir(corpo: bytes, codificacao: str) -> bytes:
    if codificacao == "gzip":
        return gzip.decompress(corpo)
    if codificacao == "deflate":
        try:
            return zlib.decompress(corpo)
        except zlib.error:
            return zlib.decompress(corpo, -15)  # deflate "cru", sem cabeçalho zlib
    return corpo
