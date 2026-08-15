"""Allowlist-only /web fetch: unit + harness route tests. No live network."""

from __future__ import annotations

import ipaddress

import httpx
import pytest
from fastapi.testclient import TestClient

from harness.config import HarnessConfig
from harness.ollama import HarnessChatClient
from harness.server import create_app
from harness.web_search import (
    WebTool,
    WebToolError,
    assert_public_host,
    extract_text,
    parse_allow_entry,
    url_is_allowed,
)

_TEST_KEY = "harness-test-key"


def _chat() -> HarnessChatClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "model": "qwen3.6:27b",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    return HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3.6:27b",
        transport=httpx.MockTransport(handler),
    )


def _page_transport(body: str = "<html><body><p>CyClaw allowlist docs</p></body></html>", content_type: str = "text/html"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": content_type})

    return httpx.MockTransport(handler)


def _noop_resolve(_host: str) -> None:
    return None


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CYCLAW_HOME", str(tmp_path / ".CyClaw"))
    monkeypatch.setenv("CYCLAW_API_KEY", _TEST_KEY)
    return HarnessConfig.load()


def _client(cfg: HarnessConfig, web: WebTool) -> TestClient:
    app = create_app(cfg, _chat(), web)
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {_TEST_KEY}", "X-CyClaw-CSRF": app.state.csrf_token},
    )


def test_parse_allow_entry_accepts_host_and_url():
    host = parse_allow_entry("docs.python.org")
    assert host["host"] == "docs.python.org"
    assert host["scheme"] == "https"
    path = parse_allow_entry("https://docs.python.org/3/library/")
    assert path["path"] == "/3/library/"


@pytest.mark.parametrize("raw", [
    "http://localhost/secret",
    "https://127.0.0.1/",
    "https://10.0.0.5/admin",
    "https://169.254.169.254/latest/meta-data",
    "https://user:pass@docs.python.org/",
    "ftp://docs.python.org/",
    "",
])
def test_parse_allow_entry_refuses_ssrf_shapes(raw):
    with pytest.raises(WebToolError) as exc:
        parse_allow_entry(raw)
    assert exc.value.code in {"WEB_BAD_URL", "WEB_SSRF_DENIED"}


def test_url_is_allowed_matches_host_and_path_prefix():
    entries = [parse_allow_entry("https://docs.python.org/3/")]
    assert url_is_allowed("https://docs.python.org/3/library/os.html", entries)
    assert url_is_allowed("https://www.docs.python.org/3/", entries)
    assert url_is_allowed("https://docs.python.org/", entries) is None
    assert url_is_allowed("https://evil.example/", entries) is None


def test_assert_public_host_refuses_loopback(monkeypatch):
    def fake_getaddrinfo(host, _port):
        return [(0, 0, 0, "", ("127.0.0.1", 0))]

    monkeypatch.setattr("harness.web_search.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(WebToolError) as exc:
        assert_public_host("docs.python.org")
    assert exc.value.code == "WEB_SSRF_DENIED"


def test_assert_public_host_accepts_global(monkeypatch):
    def fake_getaddrinfo(host, _port):
        return [(0, 0, 0, "", ("1.1.1.1", 0))]

    monkeypatch.setattr("harness.web_search.socket.getaddrinfo", fake_getaddrinfo)
    assert_public_host("one.one.one.one")
    assert ipaddress.ip_address("1.1.1.1").is_global


def test_extract_text_strips_script():
    html = "<html><script>steal()</script><p>Visible</p></html>"
    assert "Visible" in extract_text(html, "text/html")
    assert "steal" not in extract_text(html, "text/html")


def test_fetch_refuses_when_disabled(cfg):
    tool = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    tool.allow("https://docs.python.org/")
    with pytest.raises(WebToolError) as exc:
        tool.fetch("https://docs.python.org/")
    assert exc.value.code == "WEB_DISABLED"


def test_fetch_refuses_empty_allowlist(cfg):
    cfg.web_enabled = True
    tool = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    with pytest.raises(WebToolError) as exc:
        tool.fetch("https://docs.python.org/")
    assert exc.value.code == "WEB_ALLOWLIST_EMPTY"


def test_fetch_and_search_and_inject(cfg):
    cfg.web_enabled = True
    tool = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    tool.allow("https://docs.python.org/")
    page = tool.fetch("https://docs.python.org/3/")
    assert "CyClaw allowlist docs" in page["text"]
    found = tool.search("allowlist")
    assert found["hits"]
    assert "allowlist" in found["hits"][0]["snippets"][0].casefold()
    injected = tool.inject()
    assert injected["injected"] is True
    source = next(line for line in tool.context_text().splitlines() if line.startswith("Source: "))
    assert source == "Source: https://docs.python.org/"
    tool.forget()
    assert tool.context_text() == ""


def test_search_error_record_is_code_only(cfg, monkeypatch, caplog):
    """Per-URL failures must not leak exception text into the search payload
    (CodeQL py/stack-trace-exposure, alert 1091)."""
    cfg.web_enabled = True
    tool = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    tool.allow("https://docs.python.org/")
    tool.allow("https://broken.example/")

    real_get = tool._get

    def flaky_get(url, entries):
        if "broken.example" in url:
            raise WebToolError("DNS failed for broken.example", code="WEB_DNS")
        return real_get(url, entries)

    monkeypatch.setattr(tool, "_get", flaky_get)
    with caplog.at_level("INFO", logger="cyclaw.harness.web_search"):
        found = tool.search("allowlist")
    assert found["hits"], "successful URLs still produce hits"
    assert len(found["errors"]) == 1
    record = found["errors"][0]
    assert record["code"] == "WEB_DNS"
    assert set(record) == {"url", "code"}
    assert "message" not in record
    assert "DNS failed" not in str(found)
    assert str(found["errors"]).count("broken.example") == 1  # the url key only
    assert any("DNS failed for broken.example" in r.message for r in caplog.records)


def test_routes_default_off_and_open_status(cfg):
    web = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    client = _client(cfg, web)
    bare = TestClient(client.app, base_url="http://127.0.0.1")
    status = bare.get("/api/web").json()
    assert status["enabled"] is False
    assert status["allowlist"] == []
    denied = client.post("/api/web/fetch", json={"url": "https://docs.python.org/"})
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "WEB_DISABLED"


def test_routes_allow_on_fetch_search(cfg):
    web = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    client = _client(cfg, web)
    client.post("/api/web/allow", json={"url": "https://docs.python.org/"})
    client.post("/api/web", json={"enabled": True})
    page = client.post("/api/web/fetch", json={"url": "https://docs.python.org/3/"})
    assert page.status_code == 200
    assert "CyClaw allowlist docs" in page.json()["text"]
    off_host = client.post("/api/web/fetch", json={"url": "https://evil.example/"})
    assert off_host.status_code == 400
    assert off_host.json()["detail"]["code"] == "WEB_HOST_DENIED"
    hits = client.post("/api/web/search", json={"query": "allowlist"})
    assert hits.status_code == 200
    assert hits.json()["hits"]
    injected = client.post("/api/web/inject")
    assert injected.status_code == 200
    chat = client.post("/api/chat", json={"message": "summarize the extract"})
    assert chat.status_code == 200
    client.post("/api/web/forget")
    assert client.get("/api/web").json()["injected"] is False
