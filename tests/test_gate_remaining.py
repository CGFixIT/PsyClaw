"""Remaining gate.py branches the main gateway suite does not hit.

Imports ``gate`` inside tests (never at module top) so collection does not
boot the app a second time. Uses the shared ``client`` fixture from conftest
on a dedicated loopback peer so these posts do not starve test_gate.py's
rate-limit bucket.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Dedicated loopback peer — see tests/conftest.py::_mocked_gateway.
pytestmark = pytest.mark.parametrize("client", [("127.0.0.51", 51234)], indirect=True)


_SOUL_KEY = "gate-remaining-key"
_SOUL_AUTH = {"Authorization": f"Bearer {_SOUL_KEY}"}
_SOUL_BODY = {"new_soul": "# Soul\n\ncoverage body", "reason": "coverage reason"}


def test_model_provider_for_maps_known_prefixes(client):
    import gate

    assert gate._model_provider_for("grok-4.5") == "xai"
    assert gate._model_provider_for("claude-sonnet-5") == "anthropic"
    assert gate._model_provider_for("qwen3.8:27b-mlx") == "ollama"


def test_audit_query_attaches_username(client):
    import gate

    request = MagicMock()
    with patch.object(gate, "_request_username", return_value="alice"):
        with patch.object(gate, "_audit", new_callable=AsyncMock) as audit:
            asyncio.run(gate._audit_query(request, {"event": "prompt_injection_blocked"}))
    audit.assert_awaited_once()
    event = audit.await_args.args[0]
    assert event["username"] == "alice"
    assert event["event"] == "prompt_injection_blocked"


def test_query_stamps_username_on_graph_state(client, monkeypatch):
    import gate

    test_client, mock_graph = client
    monkeypatch.setattr(gate, "_request_username", lambda _request: "alice")
    resp = test_client.post("/query", json={"query": "What is Veeam immutability?"})
    assert resp.status_code == 200
    state = mock_graph.invoke.call_args.args[0]
    assert state["username"] == "alice"


def test_cel_monitor_failure_fail_opens(client, caplog):
    import gate

    test_client, _ = client
    gate.cfg["numbat"] = {"cel": {"enabled": True}}
    with patch("gate.monitor_request", side_effect=RuntimeError("cel down")):
        with caplog.at_level("WARNING", logger="cyclaw.gate"):
            resp = test_client.post("/query", json={"query": "What is Veeam immutability?"})
    assert resp.status_code == 200
    assert "CEL monitor request failed" in caplog.text


def test_cel_monitor_uses_grok_and_claude_provider_labels(client):
    import gate

    test_client, mock_graph = client
    gate.cfg["numbat"] = {"cel": {"enabled": True}}
    mock_graph.invoke.return_value = {
        "query": "q",
        "answer": "from grok",
        "answer_model": "grok-4.5",
        "answer_sources": [],
        "retrieved_docs": [],
        "top_score": 0.9,
        "retrieval_mode": "hybrid",
        "needs_user_confirm": False,
        "audit_event": {},
    }
    with patch("gate.monitor_request") as mock_monitor:
        assert test_client.post("/query", json={"query": "q"}).status_code == 200
    assert mock_monitor.call_args.kwargs["model_provider"] == "xai"

    mock_graph.invoke.return_value["answer_model"] = "claude-sonnet-5"
    with patch("gate.monitor_request") as mock_monitor:
        assert test_client.post("/query", json={"query": "q"}).status_code == 200
    assert mock_monitor.call_args.kwargs["model_provider"] == "anthropic"


def test_invalid_content_length_is_ignored(client):
    import gate

    mw = gate._MaxBodySizeMiddleware(app=None, max_bytes=64)
    request = MagicMock()
    request.headers.get.return_value = "not-an-int"
    call_next = AsyncMock(return_value="ok")
    assert asyncio.run(mw.dispatch(request, call_next)) == "ok"
    call_next.assert_awaited_once()


def test_lifespan_closes_clients_and_survives_close_failures(client, caplog):
    import gate

    llm = MagicMock()
    grok = MagicMock()
    grok.close.side_effect = RuntimeError("grok close")
    names = (
        "local_llm", "grok", "claude", "retriever",
        "personality", "auth_manager", "_rate_limiter",
    )
    saved = {name: getattr(gate, name) for name in names}
    gate.local_llm = llm
    gate.grok = grok
    gate.claude = None
    gate.retriever = None
    gate.personality = MagicMock()
    gate.auth_manager = MagicMock()
    gate._rate_limiter = MagicMock()
    try:
        with caplog.at_level("WARNING", logger="cyclaw.gate"):
            with patch.object(gate, "close_http_client", side_effect=RuntimeError("http")):
                with TestClient(gate.app, base_url="http://localhost") as nested:
                    nested.get("/health")
        llm.close.assert_called_once()
        grok.close.assert_called_once()
        gate.personality.close.assert_called_once()
        assert "shutdown close failed for grok" in caplog.text
        assert "shutdown close failed for health http client" in caplog.text
    finally:
        for name, value in saved.items():
            setattr(gate, name, value)


def test_init_retrieval_boot_banner_on_missing_index(client, capsys):
    import gate
    from utils.errors import IndexNotFoundError

    saved_retriever = gate.retriever
    saved_graph = gate.compiled_graph
    try:
        with patch.object(
            gate, "HybridRetriever", side_effect=IndexNotFoundError("index missing")
        ):
            ready = gate._init_retrieval(boot=True)
        assert ready is False
        err = capsys.readouterr().err
        assert "index missing" in err
        assert "python -m retrieval.indexer" in err
    finally:
        gate.retriever = saved_retriever
        gate.compiled_graph = saved_graph


def test_init_retrieval_survives_previous_retriever_close_failure(client, caplog):
    import gate

    old = MagicMock()
    old.close.side_effect = RuntimeError("close fail")
    new_retriever = MagicMock()
    new_graph = object()
    saved_retriever = gate.retriever
    saved_graph = gate.compiled_graph
    gate.retriever = old
    try:
        with patch.object(gate, "HybridRetriever", return_value=new_retriever), \
             patch.object(gate, "build_graph", return_value=new_graph):
            with caplog.at_level("WARNING", logger="cyclaw.gate"):
                assert gate._init_retrieval() is True
        assert gate.retriever is new_retriever
        old.close.assert_called_once()
        assert "hot-init close failed for previous retriever" in caplog.text
    finally:
        gate.retriever = saved_retriever
        gate.compiled_graph = saved_graph


def test_soul_propose_apply_reload_restore_when_disabled(client, monkeypatch):
    import gate

    test_client, _ = client
    monkeypatch.setenv("CYCLAW_API_KEY", _SOUL_KEY)
    original = gate.personality
    gate.personality = None
    try:
        assert test_client.post("/soul/propose", json=_SOUL_BODY, headers=_SOUL_AUTH).status_code == 404
        assert test_client.post("/soul/apply", json=_SOUL_BODY, headers=_SOUL_AUTH).status_code == 404
        assert test_client.post("/soul/restore", headers=_SOUL_AUTH).status_code == 404
    finally:
        gate.personality = original


def test_soul_propose_apply_reload_restore_success(client, monkeypatch):
    import gate

    test_client, _ = client
    monkeypatch.setenv("CYCLAW_API_KEY", _SOUL_KEY)
    personality = gate.personality
    assert personality is not None

    with patch.object(personality, "propose_evolution", return_value={"status": "proposed"}) as propose:
        resp = test_client.post("/soul/propose", json=_SOUL_BODY, headers=_SOUL_AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "proposed"
    propose.assert_called_once()

    with patch.object(personality, "apply_evolution", return_value={"version": 7}) as apply_ev:
        resp = test_client.post("/soul/apply", json=_SOUL_BODY, headers=_SOUL_AUTH)
    assert resp.status_code == 200
    assert resp.json()["version"] == 7
    apply_ev.assert_called_once()

    with patch.object(personality, "reload") as reload, \
         patch.object(personality, "get_version", return_value=11):
        resp = test_client.post("/soul/reload", headers=_SOUL_AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"status": "reloaded", "version": 11}
    reload.assert_called_once()

    with patch.object(personality, "restore_from_backup", return_value={"restored": True}) as restore:
        resp = test_client.post("/soul/restore", headers=_SOUL_AUTH)
    assert resp.status_code == 200
    assert resp.json()["restored"] is True
    restore.assert_called_once()


def test_query_route_without_dependant_is_not_enforced(client):
    import gate
    from types import SimpleNamespace

    fake_app = SimpleNamespace(routes=[SimpleNamespace(path="/query", dependant=None)])
    with patch.object(gate, "app", fake_app):
        assert gate._request_path_enforcement_active() is False


def test_request_path_enforcement_false_when_no_query_route(client):
    import gate
    from types import SimpleNamespace

    fake_app = SimpleNamespace(routes=[SimpleNamespace(path="/health", dependant=object())])
    with patch.object(gate, "app", fake_app):
        assert gate._request_path_enforcement_active() is False


def test_tls_ssl_kwargs_relative_and_missing(client, tmp_path, monkeypatch):
    import gate

    monkeypatch.setattr(gate, "cfg", {"api": {"tls": {"enabled": True}}})
    kwargs, err = gate._tls_ssl_kwargs()
    assert kwargs is None
    assert "certfile/keyfile" in err

    cert = tmp_path / "c.pem"
    key = tmp_path / "k.pem"
    cert.write_bytes(b"c")
    key.write_bytes(b"k")
    monkeypatch.setattr(gate, "_BASE_DIR", tmp_path)
    monkeypatch.setattr(
        gate,
        "cfg",
        {"api": {"tls": {"enabled": True, "certfile": "c.pem", "keyfile": "k.pem"}}},
    )
    kwargs, err = gate._tls_ssl_kwargs()
    assert err is None
    assert kwargs["ssl_certfile"].endswith("c.pem")
    assert kwargs["ssl_keyfile"].endswith("k.pem")
