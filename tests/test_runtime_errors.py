"""Runtime error handling tests — gaps identified in the main test suite.

Covers error paths not exercised by existing tests:
  - gate.py: generic 500 from unexpected graph exception, rate-limit 429,
    error-message sanitization, graph-error audit event written
  - graph.py: audit_logger_node personality-DB failure swallowed non-fatally,
    LLM/Grok error state returned as HTTP 200 (not 500)
  - utils/health.py: config-parse failure propagates from check_all
  - llm/client.py: retry exhaustion raises LLMServiceError
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import yaml

from tests.conftest import (
    MOCK_HIGH_SCORE_RESULTS,
    MockLocalLLM,
    MockRetriever,
    TEST_CONFIG,
)
from utils.errors import LLMServiceError


# ---------------------------------------------------------------------------
# Shared fixture — mirrors test_gate.py's client but named to avoid collision
# ---------------------------------------------------------------------------

@pytest.fixture()
def gate_client(tmp_path):
    """FastAPI TestClient with mocked runtime deps. Yields (client, mock_graph)."""
    import copy

    from fastapi.testclient import TestClient

    from utils.logger import reset_config_cache

    reset_config_cache()
    cfg = copy.deepcopy(TEST_CONFIG)
    cfg["logging"]["audit_file"] = str(tmp_path / "audit.jsonl")
    cfg["logging"]["log_file"] = str(tmp_path / "gateway.log")

    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "query": "test",
        "answer": "ok",
        "answer_model": "local",
        "answer_sources": [],
        "retrieved_docs": [],
        "top_score": 0.9,
        "retrieval_mode": "hybrid",
        "needs_user_confirm": False,
        "audit_event": {},
    }

    with patch("gate.yaml.safe_load", return_value=cfg), \
         patch("gate.cfg", cfg), \
         patch("gate.HybridRetriever"), \
         patch("gate.LocalLLMClient"), \
         patch("gate.build_graph", return_value=mock_graph), \
         patch("gate.check_input", side_effect=lambda q: q), \
         patch("gate.check_all", return_value=[]):

        import gate
        gate.cfg = cfg
        _globals = ("retriever", "local_llm", "grok", "compiled_graph")
        _saved = {k: getattr(gate, k, None) for k in _globals}
        try:
            gate.retriever = MockRetriever(MOCK_HIGH_SCORE_RESULTS)
            gate.local_llm = MockLocalLLM()
            gate.grok = None
            gate.compiled_graph = mock_graph

            client = TestClient(gate.app, base_url="http://localhost")  # DevSkim: ignore DS162092,DS137138
            yield client, mock_graph
        finally:
            for k, v in _saved.items():
                setattr(gate, k, v)

    reset_config_cache()


# ---------------------------------------------------------------------------
# Group 1: gate.py — untested HTTP error paths
# ---------------------------------------------------------------------------

class TestGateRuntimeErrors:
    def test_query_500_on_unexpected_graph_exception(self, gate_client):
        """graph.invoke() raising a bare Exception → 500 GRAPH_ERROR (not propagated raw)."""
        test_client, mock_graph = gate_client
        mock_graph.invoke.side_effect = RuntimeError("unexpected internal failure")
        resp = test_client.post("/query", json={"query": "anything"})
        assert resp.status_code == 500
        body = resp.json()
        assert body["detail"]["code"] == "GRAPH_ERROR"
        # Must have an error message (not empty)
        assert body["detail"]["error"]

    def test_query_429_when_rate_limit_exceeded(self, gate_client):
        """_check_rate_limit_async returning False → 429 RATE_LIMIT with audit event."""
        test_client, _ = gate_client
        with patch("gate._check_rate_limit_async", new=AsyncMock(return_value=False)):
            resp = test_client.post("/query", json={"query": "anything"})
        assert resp.status_code == 429
        assert resp.json()["detail"]["code"] == "RATE_LIMIT"

    def test_query_500_error_message_sanitized(self, gate_client, monkeypatch):
        """Exception message containing a live env-var secret must be redacted in the 500 body."""
        test_client, mock_graph = gate_client
        secret = "cyclaw-secret-runtime-token-xyz1234"
        monkeypatch.setenv("CYCLAW_API_KEY", secret)
        mock_graph.invoke.side_effect = RuntimeError(f"auth failed: key={secret}")
        resp = test_client.post("/query", json={"query": "anything"})
        assert resp.status_code == 500
        assert secret not in resp.text

    def test_graph_error_audit_event_written(self, gate_client, tmp_path):
        """When graph raises, gate writes a 'graph_error' audit event before returning 500."""
        import json

        import gate

        test_client, mock_graph = gate_client
        audit_file = tmp_path / "audit_graph_error.jsonl"
        gate.cfg["logging"]["audit_file"] = str(audit_file)
        mock_graph.invoke.side_effect = RuntimeError("boom")

        resp = test_client.post("/query", json={"query": "trigger error"})
        assert resp.status_code == 500

        events = [json.loads(line) for line in audit_file.read_text().splitlines() if line.strip()]
        error_events = [e for e in events if e.get("event") == "graph_error"]
        assert error_events, "Expected a graph_error audit event but found none"


# ---------------------------------------------------------------------------
# Group 2: gate.py — LLM/Grok error state returned as HTTP 200 (not 500)
# ---------------------------------------------------------------------------

class TestGateHandlesErrorStateFromGraph:
    def test_llm_error_state_returns_http_200_with_error_answer(self, gate_client):
        """Graph returning an LLM-error state (answer contains stub, error field set)
        must produce HTTP 200, not a 500 — graph completed, just degraded."""
        test_client, mock_graph = gate_client
        mock_graph.invoke.return_value = {
            "query": "test",
            "answer": "[LLM Error: LM Studio timeout]",
            "answer_model": "local",
            "answer_sources": [],
            "retrieved_docs": [],
            "top_score": 0.85,
            "retrieval_mode": "hybrid",
            "needs_user_confirm": False,
            "audit_event": {},
            "error": "LLM_SERVICE_ERROR: LM Studio timeout",
        }
        resp = test_client.post("/query", json={"query": "anything"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["model_used"] == "local"
        assert "[LLM Error:" in body["answer"]

    def test_grok_error_state_returns_http_200_with_error_answer(self, gate_client):
        """Graph returning a Grok-error state must produce HTTP 200, not 500."""
        test_client, mock_graph = gate_client
        mock_graph.invoke.return_value = {
            "query": "test",
            "answer": "[Grok Error: GROK_API_KEY not set]",
            "answer_model": "grok",
            "answer_sources": [],
            "retrieved_docs": [],
            "top_score": 0.3,
            "retrieval_mode": "hybrid",
            "needs_user_confirm": False,
            "audit_event": {},
            "error": "GROK_SERVICE_ERROR: GROK_API_KEY not set",
        }
        resp = test_client.post("/query", json={"query": "anything"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["model_used"] == "grok"
        assert "[Grok Error:" in body["answer"]


# ---------------------------------------------------------------------------
# Group 3: graph.py — audit_logger_node personality-DB failure is non-fatal
# ---------------------------------------------------------------------------

class TestAuditLoggerNodePersonalityFailure:
    def test_audit_logger_node_swallows_personality_db_error(self):
        """personality.record_interaction() raising must not propagate out of the node.

        The node must return the audit_event dict normally — personality-DB errors
        are logged at ERROR level (non-fatal invariant, graph.py:547-550).
        """
        from graph import audit_logger_node

        mock_personality = MagicMock()
        mock_personality.record_interaction.side_effect = RuntimeError("DB connection lost")

        state = {
            "query": "test query",
            "answer_model": "local",
            "answer_sources": [],
            "retrieved_docs": [],
            "top_score": 0.9,
            "retrieval_mode": "hybrid",
            "needs_user_confirm": False,
        }

        with patch("graph.audit_log"):
            result = audit_logger_node(state, cfg={}, personality=mock_personality)

        assert "audit_event" in result, "Node must return audit_event even when personality DB fails"
        mock_personality.record_interaction.assert_called_once()


# ---------------------------------------------------------------------------
# Group 4: utils/health.py — config-parse failure propagates from check_all
# ---------------------------------------------------------------------------

class TestHealthConfigFailure:
    def test_health_cfg_failure_degrades_from_check_all(self):
        """_health_cfg() failure returns a degraded config status, not a 500 trigger."""
        from utils import health

        # Clear the module-level TTL cache so the patched function is actually called.
        health._cfg_cache.clear()

        with patch.object(health, "_health_cfg", side_effect=yaml.YAMLError("bad yaml")):
            statuses = health.check_all(config_path="fake_path.yaml")

        assert len(statuses) == 1
        assert statuses[0].name == "config"
        assert statuses[0].healthy is False
        assert "bad yaml" in statuses[0].error


# ---------------------------------------------------------------------------
# Group 5: llm/client.py — retry exhaustion raises LLMServiceError
# ---------------------------------------------------------------------------

class TestLLMClientRetryExhaustion:
    def test_all_retries_exhausted_raises_llm_service_error(self):
        """TransportError on every attempt (max_retries=2) → LLMServiceError after 3 tries.

        Covers the retry-exhaustion branch in _post_with_retry (currently marked
        # pragma: no cover because the loop guarantees each iteration returns or
        raises — but the typed-error raise at line 184 is structurally unreachable).
        The real coverage target is the ``raise on_other(e)`` terminal-failure path
        at line 174 after all retries are spent.
        """
        from llm.client import LocalLLMClient

        cfg = {
            "models": {
                "local_llm": {
                    "base_url": "http://127.0.0.1:1234/v1",  # DevSkim: ignore DS162092,DS137138
                    "model": "test-model",
                    "timeout_sec": 5,
                    "max_tokens": 512,
                    "temperature": 0.0,
                    "retry": {
                        "max_retries": 2,
                        "backoff_base_sec": 0.0,
                        "backoff_max_sec": 0.0,
                    },
                }
            }
        }

        client = LocalLLMClient(cfg=cfg)
        call_count = 0

        def always_fail(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.TransportError("connection refused")

        with patch.object(client._client, "post", side_effect=always_fail):
            with pytest.raises(LLMServiceError):
                client.generate("test prompt")

        # Verify all 3 attempts (1 initial + 2 retries) were made.
        assert call_count == 3, f"Expected 3 total attempts, got {call_count}"
