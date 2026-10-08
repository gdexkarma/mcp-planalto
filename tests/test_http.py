"""Cliente HTTP contra um servidor local."""

import http.server
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from mcp_planalto import http as mhttp
from mcp_planalto.http import ClienteHTTP, ErroHTTP, decodificar


class _Handler(http.server.BaseHTTPRequestHandler):
    contagem: dict[str, int] = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        n = self.contagem[self.path] = self.contagem.get(self.path, 0) + 1
        if self.path == "/truncado":
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"pouco")
            return
        if self.path == "/instavel" and n == 1:
            self.send_response(503)
            self.send_header("Retry-After", "0")
            self.end_headers()
            return
        if self.path == "/404":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/etag" and self.headers.get("If-None-Match") == '"v1"':
            self.send_response(304)
            self.end_headers()
            return
        corpo = f"ok {self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("ETag", '"v1"')
        self.end_headers()
        self.wfile.write(corpo)


@pytest.fixture(scope="module")
def base():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    mhttp.INTERVALO_HOST["127.0.0.1"] = 0
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    return ClienteHTTP(tmp_path / "cache", "Mozilla/5.0 teste", timeout=5, tentativas=3, prazo_total=20)


def test_reintento_e_cache(base, cli):
    r = cli.get(base + "/instavel")
    assert r.corpo == b"ok /instavel" and _Handler.contagem["/instavel"] == 2
    assert cli.get(base + "/instavel", max_idade=None).do_cache


def test_revalidacao_304(base, cli):
    cli.get(base + "/etag")
    r = cli.get(base + "/etag", max_idade=0)
    assert r.corpo == b"ok /etag" and not r.do_cache


def test_truncado_vira_erro_tratado(base, cli):
    t = time.monotonic()
    with pytest.raises(ErroHTTP):
        cli.get(base + "/truncado")
    assert time.monotonic() - t < 15


def test_404_sem_reintento(base, cli):
    with pytest.raises(ErroHTTP) as e:
        cli.get(base + "/404")
    assert e.value.status == 404 and _Handler.contagem["/404"] == 1


def test_copia_em_cache_quando_portal_cai(base, cli):
    cli.get(base + "/cai")
    url_morta = base.replace("127.0.0.1", "127.0.0.1") + "/cai"
    cli.tentativas = 1
    _Handler.contagem["/cai"] = 0
    # simula queda: aponta a mesma chave de cache para uma porta fechada
    cli._caminho = (lambda orig: (lambda u: orig(url_morta)))(cli._caminho)
    r = cli.get("http://127.0.0.1:9/cai", max_idade=0)
    assert r.corpo == b"ok /cai" and r.do_cache


def test_falha_ao_gravar_cache_nao_perde_download(base, cli, monkeypatch):
    def falha(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(mhttp.tempfile, "mkstemp", falha)
    assert cli.get(base + "/disco").corpo == b"ok /disco"


def test_gravacao_concorrente(base, cli):
    with ThreadPoolExecutor(8) as ex:
        rs = list(ex.map(lambda _: cli.get(base + "/conc", max_idade=0), range(16)))
    assert all(r.corpo == b"ok /conc" for r in rs)
    assert cli._ler_cache(base + "/conc").corpo == b"ok /conc"


def test_decodificar():
    assert decodificar("ação “x”".encode("cp1252"), "text/html; charset=iso-8859-1") == "ação “x”"
    assert decodificar(b"\xef\xbb\xbfol\xc3\xa1") == "olá"
    assert decodificar("ação".encode("cp1252")) == "ação"
