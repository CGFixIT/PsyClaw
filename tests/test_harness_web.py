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
    _MAX_BYTES,
    _allowlist_target,
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
            "model": "qwen3.8:27b-mlx",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    return HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3.8:27b-mlx",
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


def test_allowlist_target_preserves_nondefault_ports_and_ipv6_authorities():
    port = parse_allow_entry("https://example.com:8443/docs")
    assert port["raw"] == "https://example.com:8443/docs"
    assert _allowlist_target(port) == "https://example.com:8443/docs"

    ipv6 = parse_allow_entry("https://[2606:4700:4700::1111]/docs")
    assert ipv6["raw"] == "https://[2606:4700:4700::1111]/docs"
    assert _allowlist_target(ipv6) == "https://[2606:4700:4700::1111]/docs"


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


def test_url_is_allowed_returns_none_for_unparseable():
    assert url_is_allowed("ftp://docs.python.org/", []) is None
    assert url_is_allowed("", []) is None


def test_url_is_allowed_matches_host_and_path_prefix():
    entries = [parse_allow_entry("https://docs.python.org/3/")]
    assert url_is_allowed("https://docs.python.org/3/library/os.html", entries)
    assert url_is_allowed("https://www.docs.python.org/3/", entries)
    assert url_is_allowed("https://docs.python.org/", entries) is None
    assert url_is_allowed("https://evil.example/", entries) is None


def test_url_is_allowed_matches_the_allowlisted_nondefault_port():
    entries = [parse_allow_entry("https://docs.python.org:8443/3/")]

    assert url_is_allowed("https://docs.python.org:8443/3/library/os.html", entries)
    assert url_is_allowed("https://docs.python.org/3/library/os.html", entries) is None


def test_assert_public_host_refuses_loopback(monkeypatch):
    def fake_getaddrinfo(host, _port):
        return [(0, 0, 0, "", ("127.0.0.1", 0))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(WebToolError) as exc:
        assert_public_host("docs.python.org")
    assert exc.value.code == "WEB_SSRF_DENIED"


def test_assert_public_host_accepts_global(monkeypatch):
    def fake_getaddrinfo(host, _port):
        return [(0, 0, 0, "", ("1.1.1.1", 0))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
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


def test_fetch_stops_reading_at_the_byte_cap(cfg):
    class CountingStream(httpx.SyncByteStream):
        def __init__(self) -> None:
            self.chunks_yielded = 0

        def __iter__(self):
            for _ in range(5):
                self.chunks_yielded += 1
                yield b"x" * 65_536

    stream = CountingStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, headers={"content-type": "text/plain"})

    cfg.web_enabled = True
    tool = WebTool(cfg, transport=httpx.MockTransport(handler), resolver=_noop_resolve)
    tool.allow("https://docs.python.org/")

    page = tool.fetch("https://docs.python.org/")

    assert page["chars"] == _MAX_BYTES
    assert stream.chunks_yielded < 5


def test_search_records_last_as_the_matching_hit_not_the_last_iterated_entry(cfg):
    """A multi-host search must not let a non-matching later entry clobber
    web_last.json with an irrelevant page.

    Before this fix, _get() unconditionally overwrote _last_path on every
    successful fetch inside search()'s loop, so /web inject after a search
    across 2+ allowlisted hosts injected whichever host happened to be LAST
    in allowlist order -- regardless of whether it actually matched the
    query. Here "b.example" (second in allowlist order, and thus the one
    that would win under the old unconditional-overwrite behavior) contains
    no match; only "a.example" does.
    """
    cfg.web_enabled = True

    def handler(request: httpx.Request) -> httpx.Response:
        if "a.example" in str(request.url):
            body = "<html><body><p>CyClaw allowlist docs</p></body></html>"
        else:
            body = "<html><body><p>unrelated content</p></body></html>"
        return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "text/html"})

    tool = WebTool(cfg, transport=httpx.MockTransport(handler), resolver=_noop_resolve)
    tool.allow("https://a.example/")
    tool.allow("https://b.example/")

    found = tool.search("allowlist")
    assert len(found["hits"]) == 1
    assert found["hits"][0]["url"] == "https://a.example/"

    injected = tool.inject()
    assert injected["injected"] is True
    source = next(line for line in tool.context_text().splitlines() if line.startswith("Source: "))
    assert source == "Source: https://a.example/"


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


@pytest.mark.parametrize("path,body", [
    ("/api/web/fetch", {"url": "https://docs.python.org/3/"}),
    ("/api/web/search", {"query": "allowlist"}),
])
def test_web_request_consumes_one_normal_limiter_token(cfg, monkeypatch, path, body):
    monkeypatch.setattr(
        "harness.server._rate_limit_settings",
        lambda: {"max_requests": 2, "window_seconds": 300},
    )
    cfg.web_enabled = True
    web = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    # Configure directly so setup does not consume the two-request allowance.
    web.allow("https://docs.python.org/")
    client = _client(cfg, web)

    assert client.post(path, json=body).status_code == 200
    assert client.post(path, json=body).status_code == 200
    refused = client.post(path, json=body)
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "RATE_LIMIT"


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


def test_corrupt_allowlist_is_not_overwritten(cfg):
    path = cfg.tools_dir / "web_allowlist.json"
    garbage = b"{not-json"
    path.write_bytes(garbage)
    tool = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    with pytest.raises(WebToolError) as allowed:
        tool.allow("https://docs.python.org/")
    assert allowed.value.code == "WEB_ALLOWLIST_UNREADABLE"
    assert path.read_bytes() == garbage
    with pytest.raises(WebToolError) as denied:
        tool.deny("https://docs.python.org/")
    assert denied.value.code == "WEB_ALLOWLIST_UNREADABLE"
    assert path.read_bytes() == garbage
    with pytest.raises(WebToolError) as status:
        tool.status()
    assert status.value.code == "WEB_ALLOWLIST_UNREADABLE"
    assert path.read_bytes() == garbage


def test_parse_allow_entry_rejects_out_of_range_port():
    with pytest.raises(WebToolError) as exc:
        parse_allow_entry("https://docs.python.org:99999/")
    assert exc.value.code == "WEB_BAD_URL"


def test_allowlist_target_rejects_bad_port_and_path():
    with pytest.raises(WebToolError) as port:
        _allowlist_target({"scheme": "https", "host": "docs.python.org", "port": "99999", "path": "/"})
    assert port.value.code == "WEB_BAD_URL"
    with pytest.raises(WebToolError) as path:
        _allowlist_target({"scheme": "https", "host": "docs.python.org", "port": "", "path": "/a b"})
    assert path.value.code == "WEB_BAD_URL"


def test_allowlist_target_prefixes_path_missing_leading_slash():
    assert (
        _allowlist_target(
            {"scheme": "https", "host": "docs.python.org", "port": "", "path": "library"}
        )
        == "https://docs.python.org/library"
    )


def test_atomic_write_text_cleans_staged_on_failure(tmp_path, monkeypatch):
    from harness import web_search as web_mod

    target = tmp_path / "web_context.txt"
    staged = tmp_path / ".staged.web_context.txt.tmp"

    def boom(_staged, _path, _text):
        staged.write_text("partial", encoding="utf-8")
        raise RuntimeError("disk full")

    monkeypatch.setattr(web_mod, "_stage_and_replace", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        web_mod._atomic_write_text(target, "hello")
    assert not staged.exists()


def test_web_tool_allowlist_empty_when_disabled(cfg):
    cfg.web_enabled = False
    tool = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    assert tool._web_tool_allowlist() == frozenset()


def test_load_entries_non_list_is_empty(cfg):
    path = cfg.tools_dir / "web_allowlist.json"
    path.write_text('{"entries": "nope"}', encoding="utf-8")
    tool = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    assert tool.status()["allowlist"] == []


def test_assert_public_host_dns_failures(monkeypatch):
    import socket

    monkeypatch.setattr("socket.getaddrinfo", lambda *_a, **_k: (_ for _ in ()).throw(socket.gaierror("nxdomain")))
    with pytest.raises(WebToolError) as dns:
        assert_public_host("docs.python.org")
    assert dns.value.code == "WEB_DNS"

    monkeypatch.setattr("socket.getaddrinfo", lambda *_a, **_k: [])
    with pytest.raises(WebToolError) as empty:
        assert_public_host("docs.python.org")
    assert empty.value.code == "WEB_DNS"

    with pytest.raises(WebToolError) as blocked:
        assert_public_host("localhost")
    assert blocked.value.code == "WEB_SSRF_DENIED"


def test_extract_text_plain_and_script_end_tags():
    assert "hello" in extract_text("  hello   world  ", "text/plain")
    html = "<html><script>steal()</script><style>x</style><div>Visible</div></html>"
    text = extract_text(html, "text/html")
    assert "Visible" in text
    assert "steal" not in text


def test_allow_duplicate_and_cap(cfg):
    cfg.web_enabled = True
    tool = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    first = tool.allow("https://docs.python.org/")
    again = tool.allow("https://docs.python.org/")
    assert first["allowlist"] == again["allowlist"]
    from harness import web_search as web_mod

    original = web_mod._MAX_ALLOW
    web_mod._MAX_ALLOW = 1
    try:
        with pytest.raises(WebToolError) as exc:
            tool.allow("https://example.com/")
        assert exc.value.code == "WEB_ALLOWLIST_FULL"
    finally:
        web_mod._MAX_ALLOW = original


def test_fetch_refuses_query_string_and_http_error(cfg):
    cfg.web_enabled = True

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    tool = WebTool(cfg, transport=httpx.MockTransport(fail), resolver=_noop_resolve)
    tool.allow("https://docs.python.org/")
    with pytest.raises(WebToolError) as query:
        tool.fetch("https://docs.python.org/?q=1")
    assert query.value.code == "WEB_BAD_URL"
    with pytest.raises(WebToolError) as failed:
        tool.fetch("https://docs.python.org/")
    assert failed.value.code == "WEB_FETCH_FAILED"


def test_read_text_response_rejects_http_error_and_non_text(cfg):
    cfg.web_enabled = True

    def handler(request: httpx.Request) -> httpx.Response:
        if "image" in str(request.url):
            return httpx.Response(200, content=b"\x00\x01", headers={"content-type": "image/png"})
        return httpx.Response(503, text="down", headers={"content-type": "text/plain"})

    tool = WebTool(cfg, transport=httpx.MockTransport(handler), resolver=_noop_resolve)
    tool.allow("https://docs.python.org/")
    with pytest.raises(WebToolError) as http_err:
        tool.fetch("https://docs.python.org/")
    assert http_err.value.code == "WEB_FETCH_FAILED"

    tool.deny("https://docs.python.org/")
    tool.allow("https://example.com/image")
    # url_is_allowed matches path prefix; fetch still uses allowlist target.
    with pytest.raises(WebToolError) as not_text:
        tool.fetch("https://example.com/image")
    assert not_text.value.code in {"WEB_NOT_TEXT", "WEB_HOST_DENIED", "WEB_FETCH_FAILED"}


def test_inject_rejects_empty_last_extract(cfg):
    from harness.web_search import _atomic_write_json, _last_path

    _atomic_write_json(_last_path(cfg), {"url": "https://docs.python.org/", "text": "   "})
    tool = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    with pytest.raises(WebToolError) as exc:
        tool.inject()
    assert exc.value.code == "WEB_NO_LAST"


def test_fetch_maps_tool_denied(cfg, monkeypatch):
    from utils.tool_broker import ToolDenied

    cfg.web_enabled = True
    tool = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    tool.allow("https://docs.python.org/")
    monkeypatch.setattr(
        "harness.web_search.assert_allowed",
        lambda *_a, **_k: (_ for _ in ()).throw(ToolDenied("no")),
    )
    with pytest.raises(WebToolError) as exc:
        tool.fetch("https://docs.python.org/")
    assert exc.value.code == "WEB_TOOL_DENIED"


def test_search_rejects_empty_query_and_inject_without_last(cfg):
    cfg.web_enabled = True
    tool = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    tool.allow("https://docs.python.org/")
    with pytest.raises(WebToolError) as query:
        tool.search("")
    assert query.value.code == "WEB_BAD_QUERY"
    with pytest.raises(WebToolError) as inject:
        tool.inject()
    assert inject.value.code == "WEB_NO_LAST"


def test_forget_and_context_survive_missing_files(cfg):
    tool = WebTool(cfg, transport=_page_transport(), resolver=_noop_resolve)
    assert tool.context_text() == ""
    assert tool.forget()["injected"] is False


def test_snippets_add_ellipsis_when_match_is_interior():
    from harness.web_search import _snippets

    leading = _snippets("x" * 80 + "needle", "needle")
    assert leading and leading[0].startswith("…")
    trailing = _snippets("needle" + "y" * 200, "needle")
    assert trailing and trailing[0].endswith("…")
