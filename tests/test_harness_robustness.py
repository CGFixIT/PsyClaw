"""Robustness tests for the harness entry point and console error paths.

Covers the previously-untested ``harness.server.main()`` bind/port guards, the
``/api/harness/runs`` glob->mtime-sort race, the missing ``static/harness.html``
startup path, and malformed ``usage`` blocks from an OpenAI-compatible proxy.
No live services: chat goes over httpx MockTransport, routes over TestClient.
"""

from __future__ import annotations

import os

from unittest.mock import patch

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from harness import server as harness_server
from harness.config import HarnessConfig, HarnessConfigError
from harness.ollama import HarnessChatClient
from harness.server import create_app, main

_TEST_KEY = "harness-test-key"


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("CYCLAW_API_KEY", _TEST_KEY)


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CYCLAW_HOME", str(tmp_path / ".CyClaw"))
    return HarnessConfig.load()


def _chat_client(payload: dict) -> HarnessChatClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3.6:27b",
        transport=httpx.MockTransport(handler),
    )


# -- /api/harness/runs race ------------------------------------------------------


def test_runs_listing_survives_file_removed_mid_sort(cfg, tmp_path, monkeypatch):
    """Regression: a run artifact deleted between glob and mtime-sort raised
    OSError straight out of the route as a bare 500. Same race SessionStore
    guards in harness/sessions.py."""
    accepted = tmp_path / "runs" / "accepted"
    accepted.mkdir(parents=True)
    keep = accepted / "aaaaaaaaaaaaaaaa.json"
    keep.write_text("{}", encoding="utf-8")
    doomed = accepted / "bbbbbbbbbbbbbbbb.json"
    doomed.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(harness_server, "_RUNS_DIR", tmp_path / "runs")
    real_getmtime = os.path.getmtime

    def _getmtime_racing(path):
        if os.path.basename(str(path)) == doomed.name:
            raise OSError("file vanished between glob and sort")
        return real_getmtime(path)

    monkeypatch.setattr(os.path, "getmtime", _getmtime_racing)
    app = create_app(cfg, _chat_client({}))
    client = TestClient(app, base_url="http://127.0.0.1")
    resp = client.get("/api/harness/runs")
    assert resp.status_code == 200
    assert "aaaaaaaaaaaaaaaa" in [run["run_id"] for run in resp.json()["runs"]]


# -- missing console asset -------------------------------------------------------


def test_create_app_missing_console_asset_raises_typed_error(cfg, tmp_path, monkeypatch):
    """A trimmed checkout without static/harness.html must fail with the typed
    HarnessConfigError, not a bare FileNotFoundError traceback."""
    monkeypatch.setattr(harness_server, "_STATIC", tmp_path / "no-static")
    with pytest.raises(HarnessConfigError) as excinfo:
        create_app(cfg, _chat_client({}))
    assert "harness.html" in str(excinfo.value)


# -- main() bind/port guards -----------------------------------------------------


@pytest.fixture()
def _uvicorn_recorder(cfg, monkeypatch):
    calls: list[dict] = []

    def _record(app, host, port):
        calls.append({"host": host, "port": port})

    monkeypatch.setattr(uvicorn, "run", _record)
    monkeypatch.delenv("CYCLAW_HARNESS_HOST", raising=False)
    monkeypatch.delenv("CYCLAW_HARNESS_PORT", raising=False)
    return calls


def test_main_refuses_non_loopback_host(_uvicorn_recorder, monkeypatch):
    monkeypatch.setenv("CYCLAW_HARNESS_HOST", "0.0.0.0")  # noqa: S104 - asserting the refusal
    with pytest.raises(SystemExit, match="loopback"):
        main()
    assert _uvicorn_recorder == []


@pytest.mark.parametrize("bad_port", ["abc", "-1", "8790x", "70000", "0"])
def test_main_refuses_bad_port_env(_uvicorn_recorder, monkeypatch, bad_port):
    """Regression: a non-numeric CYCLAW_HARNESS_PORT was silently discarded, so
    the harness bound the stored port instead of failing closed like the
    adjacent out-of-range check."""
    monkeypatch.setenv("CYCLAW_HARNESS_PORT", bad_port)
    with pytest.raises(SystemExit, match="CYCLAW_HARNESS_PORT"):
        main()
    assert _uvicorn_recorder == []


def test_main_uses_valid_port_env(_uvicorn_recorder, monkeypatch):
    monkeypatch.setenv("CYCLAW_HARNESS_PORT", "8791")
    main()
    assert _uvicorn_recorder == [{"host": "127.0.0.1", "port": 8791}]


def test_main_defaults_to_stored_port(cfg, _uvicorn_recorder, monkeypatch):
    monkeypatch.delenv("CYCLAW_HARNESS_PORT", raising=False)
    main()
    assert _uvicorn_recorder == [{"host": "127.0.0.1", "port": cfg.port}]


# -- malformed usage from an OpenAI-compatible proxy -----------------------------


@pytest.mark.parametrize(
    ("usage", "expected_prompt", "expected_completion"),
    [
        ("none", 0, 0),  # non-dict usage: .get() used to AttributeError -> bare 500
        ({"prompt_tokens": "abc", "completion_tokens": 5}, 0, 5),  # int() used to ValueError
        ({"prompt_tokens": None}, 0, 0),
        (None, 0, 0),
    ],
)
def test_chat_survives_malformed_usage_block(usage, expected_prompt, expected_completion):
    """Token tallies are cosmetic: a malformed field in a proxy's usage block
    must degrade that count to 0, never turn a good answer into a 500 — and a
    still-valid sibling field keeps its real value."""
    chat = _chat_client(
        {
            "model": "qwen3.6:27b",
            "choices": [{"message": {"role": "assistant", "content": "hello"}}],
            "usage": usage,
        }
    )
    result = chat.chat(system_prompt="s", messages=[{"role": "user", "content": "hi"}])
    assert result.body_text == "hello"
    assert result.prompt_tokens == expected_prompt
    assert result.completion_tokens == expected_completion


# -- persist failures must not escape as unparseable 500s -------------------------
# SessionStoreError used to carry one code for two unrelated failures: "unknown
# session" (the operator's bad id) and "could not persist" (the server's disk).
# Callers mapped it to 404 or, at two sites, did not catch it at all. So a full
# disk reported as "unknown session" for a perfectly valid id, and two routes
# returned text/plain 500s that static/harness.html's fetch helper cannot parse
# into an error envelope.

def _guarded_headers(app) -> dict:
    return {
        "Authorization": f"Bearer {_TEST_KEY}",
        "Origin": "http://127.0.0.1:8790",  # DevSkim: ignore DS162092,DS137138 - loopback console origin
        "x-cyclaw-csrf": app.state.csrf_token,
        "Content-Type": "application/json",
    }


@pytest.mark.parametrize(
    "target, call",
    [
        ("create", lambda c, h, sid: c.post("/api/sessions", json={"title": "x"}, headers=h)),
        ("_write", lambda c, h, sid: c.post(f"/api/sessions/{sid}/rename", json={"title": "n"}, headers=h)),
        ("record_exchange", lambda c, h, sid: c.post("/api/chat", json={"message": "hi"}, headers=h)),
    ],
)
def test_persist_failure_is_a_typed_502_not_a_bare_500(cfg, monkeypatch, target, call):
    """Every write path reports a persist failure as a parseable 502.

    create and record_exchange previously sat outside any try at all; rename was
    caught but mapped to 404.
    """
    from harness.sessions import SessionPersistError

    app = create_app(cfg, chat_client=_chat_client(
        {"choices": [{"message": {"content": "ok"}}], "model": "m",
         "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    ))
    client = TestClient(app, base_url="http://127.0.0.1:8790")  # DevSkim: ignore DS162092,DS137138 - loopback test host
    headers = _guarded_headers(app)
    sid = client.post("/api/sessions", json={"title": "real"}, headers=headers).json()["session_id"]

    monkeypatch.setattr(
        "harness.sessions.SessionStore." + target,
        lambda *a, **k: (_ for _ in ()).throw(SessionPersistError("could not persist session")),
    )
    resp = call(client, headers, sid)
    assert resp.status_code == 502, f"{target}: got {resp.status_code}"
    assert resp.headers["content-type"].startswith("application/json"), "console must be able to parse it"
    assert resp.json()["detail"]["code"] == "HARNESS_SESSION_PERSIST_ERROR"


def test_unknown_session_is_still_404_and_distinguishable(cfg):
    """The split has to cut both ways, or it just moves the confusion."""
    app = create_app(cfg, chat_client=_chat_client({"choices": [{"message": {"content": "x"}}]}))
    client = TestClient(app, base_url="http://127.0.0.1:8790")  # DevSkim: ignore DS162092,DS137138 - loopback test host
    resp = client.get("/api/sessions/aaaaaaaaaaaa", headers=_guarded_headers(app))
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "HARNESS_SESSION_ERROR"


def test_console_page_is_not_cacheable(cfg):
    """The page embeds the per-process CSRF token in a <meta> tag.

    A cached copy outlives the process that minted it, so after a harness
    restart the browser replays a stale token and every guarded POST 403s with
    CSRF_TOKEN_INVALID until a hard reload. gate.py already sends this header on
    its own console.
    """
    app = create_app(cfg, chat_client=_chat_client({"choices": [{"message": {"content": "x"}}]}))
    client = TestClient(app, base_url="http://127.0.0.1:8790")  # DevSkim: ignore DS162092,DS137138 - loopback test host
    resp = client.get("/")
    assert "no-store" in resp.headers.get("cache-control", "")


def test_store_write_actually_raises_the_persist_type(tmp_path):
    """The store must PRODUCE the distinct type, not just have callers map it.

    The route tests above inject SessionPersistError directly, so they prove the
    mapping and nothing about the source. Without this, reverting _write to the
    base SessionStoreError would leave every one of them green while a real disk
    failure went back to reporting as "unknown session" / 404.
    """
    from harness.sessions import PERSIST_ERROR_CODE, SessionPersistError, SessionStore

    store = SessionStore(tmp_path / "sessions")
    session = store.create(model="m", title="t")
    # Make the real write fail the way a full or read-only disk would.
    with patch("harness.sessions._atomic_write_json", side_effect=OSError("No space left on device")):
        with pytest.raises(SessionPersistError) as excinfo:
            store.rename(session.session_id, "new title")
    assert excinfo.value.code == PERSIST_ERROR_CODE


def test_unknown_session_does_not_use_the_persist_code(tmp_path):
    """The other half of the split, from the real store rather than a mock."""
    from harness.sessions import PERSIST_ERROR_CODE, SessionStoreError, SessionStore

    store = SessionStore(tmp_path / "sessions")
    with pytest.raises(SessionStoreError) as excinfo:
        store.get("aaaaaaaaaaaa")
    assert excinfo.value.code != PERSIST_ERROR_CODE
