import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ai_planner


class _ProviderHandler(BaseHTTPRequestHandler):
    status_code = 200
    body = {"choices": [{"message": {"content": "ok"}}]}
    delay = 0.0
    raw_body = None

    def do_POST(self):
        if self.delay:
            time.sleep(self.delay)
        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            if self.raw_body is not None:
                self.wfile.write(self.raw_body)
            else:
                self.wfile.write(json.dumps(self.body).encode("utf-8"))
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Timeout chaos test intentionally lets the client disconnect first.
            pass

    def log_message(self, format, *args):
        return


def _serve(handler_cls=_ProviderHandler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/chat"


def _set_deepseek(monkeypatch, url):
    monkeypatch.setattr(ai_planner, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(ai_planner, "DEEPSEEK_URL", url)


def test_provider_chaos_http_500_returns_none(monkeypatch):
    class Handler(_ProviderHandler):
        status_code = 500
        body = {"error": "server down"}

    server, url = _serve(Handler)
    try:
        _set_deepseek(monkeypatch, url)
        out = ai_planner._call_llm([{"role": "user", "content": "hi"}], chain="deepseek", timeout=1)
        assert out is None
    finally:
        server.shutdown()


def test_provider_chaos_http_429_returns_none(monkeypatch):
    class Handler(_ProviderHandler):
        status_code = 429
        body = {"error": "rate limited"}

    server, url = _serve(Handler)
    try:
        _set_deepseek(monkeypatch, url)
        out = ai_planner._call_llm([{"role": "user", "content": "hi"}], chain="deepseek", timeout=1)
        assert out is None
    finally:
        server.shutdown()


def test_provider_chaos_malformed_json_returns_none(monkeypatch):
    class Handler(_ProviderHandler):
        raw_body = b"not-json"

    server, url = _serve(Handler)
    try:
        _set_deepseek(monkeypatch, url)
        out = ai_planner._call_llm([{"role": "user", "content": "hi"}], chain="deepseek", timeout=1)
        assert out is None
    finally:
        server.shutdown()


def test_provider_chaos_read_timeout_returns_none_quickly(monkeypatch):
    class Handler(_ProviderHandler):
        delay = 0.4

    server, url = _serve(Handler)
    try:
        _set_deepseek(monkeypatch, url)
        start = time.time()
        out = ai_planner._call_llm([{"role": "user", "content": "hi"}], chain="deepseek", timeout=0.1)
        elapsed = time.time() - start
        assert out is None
        assert elapsed < 0.35
    finally:
        server.shutdown()
