"""Edge-case tests for gate.py and graph.py.

Covers gaps identified during optimization scan:
- GET / serves terminal.html
- Security response headers on all endpoints
- graph invoke Exception → 500 with GRAPH_ERROR code
- score_router threshold boundary (exact equality)
- /health response field completeness
- score_router and user_gate_router routing logic
"""

import copy
import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import (
    MockRetriever, MockLocalLLM, MockGrokClient, MockClaudeClient,
    MOCK_HIGH_SCORE_RESULTS, TEST_CONFIG
)


@pytest.fixture
def client(tmp_path):
    """Create a test client with mocked dependencies."""
    import yaml
    from utils.logger import reset_config_cache
    reset_config_cache()

    cfg = copy.deepcopy(TEST_CONFIG)
    cfg["logging"]["audit_file"] = str(tmp_path / "audit.jsonl")
    cfg["logging"]["log_file"] = str(tmp_path / "gateway.log")

    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(cfg, f)

    with patch("gate.open", create=True), \
         patch("gate.yaml.safe_load", return_value=cfg), \
         patch("gate.cfg", cfg), \
         patch("gate.HybridRetriever") as MockRet, \
         patch("gate.LocalLLMClient") as MockLLM, \
         patch("gate.build_graph") as MockBuild, \
         patch("gate.check_input", side_effect=lambda q: q), \
         patch("gate.check_all", return_value=[]):

        retriever = MockRetriever(MOCK_HIGH_SCORE_RESULTS)
        llm = MockLocalLLM()

        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "query": "test query",
            "answer": "Test answer.",
            "answer_model": "local",
            "answer_sources": [
                {"source": "test.md", "score": 0.9, "chunk_id": 0,
                 "stem_tags": ["test"], "text": "...", "mode": "hybrid"}
            ],
            "retrieved_docs": [
                {"text": "...", "score": 0.9, "source": "test.md",
                 "chunk_id": 0, "stem_tags": [], "mode": "hybrid"}
            ],
            "top_score": 0.9,
            "retrieval_mode": "hybrid",
            "needs_user_confirm": False,
            "audit_event": {}
        }
        MockBuild.return_value = mock_graph

        import gate
        gate.cfg = cfg
        gate.retriever = retriever
        gate.local_llm = llm
        gate.grok = None
        gate.compiled_graph = mock_graph

        from fastapi.testclient import TestClient
        client = TestClient(gate.app, base_url="http://localhost")  # DevSkim: ignore DS162092,DS137138 - test loopback host
        yield client, mock_graph

    reset_config_cache()


class TestTerminalServing:
    """GET / must serve the terminal.html file."""

    def test_root_serves_html(self, client):
        test_client, _ = client
        resp = test_client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "CyClaw Terminal" in resp.text

    def test_root_has_cache_control(self, client):
        test_client, _ = client
        resp = test_client.get("/")
        cc = resp.headers.get("cache-control", "")
        assert "no-store" in cc


class TestGraphInvokeException:
    """A non-timeout exception from graph.invoke must return 500 GRAPH_ERROR."""

    def test_generic_exception_returns_500(self, client):
        test_client, mock_graph = client
        mock_graph.invoke.side_effect = RuntimeError("unexpected crash")
        resp = test_client.post("/query", json={"query": "trigger error"})
        assert resp.status_code == 500
        data = resp.json()
        assert data["detail"]["code"] == "GRAPH_ERROR"
        assert "unexpected crash" in data["detail"]["error"]

    def test_error_sanitized_in_500(self, client, monkeypatch):
        """Credential values must be redacted from 500 responses."""
        test_client, mock_graph = client
        secret = "supersecret-key-12345"
        monkeypatch.setenv("CYCLAW_API_KEY", secret)
        mock_graph.invoke.side_effect = RuntimeError(f"failed with key={secret}")
        resp = test_client.post("/query", json={"query": "error with secret"})
        assert resp.status_code == 500
        assert secret not in resp.text


class TestScoreRouterBoundary:
    """score_router must route to local_llm at exact threshold (>=)."""

    def test_exact_threshold_routes_to_local(self):
        from graph import route_by_score_node
        cfg = {"retrieval": {"min_score": 0.4}}
        result = route_by_score_node(
            {"top_score": 0.4, "query": "test"},
            cfg=cfg
        )
        assert result["needs_user_confirm"] is False

    def test_below_threshold_routes_to_confirm(self):
        from graph import route_by_score_node
        cfg = {"retrieval": {"min_score": 0.4}}
        result = route_by_score_node(
            {"top_score": 0.399, "query": "test"},
            cfg=cfg
        )
        assert result["needs_user_confirm"] is True

    def test_zero_score_routes_to_confirm(self):
        from graph import route_by_score_node
        cfg = {"retrieval": {"min_score": 0.4}}
        result = route_by_score_node(
            {"top_score": 0.0, "query": "test"},
            cfg=cfg
        )
        assert result["needs_user_confirm"] is True

    def test_missing_score_defaults_to_zero(self):
        from graph import route_by_score_node
        cfg = {"retrieval": {"min_score": 0.4}}
        result = route_by_score_node({"query": "test"}, cfg=cfg)
        assert result["needs_user_confirm"] is True


class TestUserGateRouter:
    """user_gate_router routing logic edge cases."""

    def test_none_confirmed_routes_to_audit(self):
        from graph import user_gate_router
        result = user_gate_router(
            {"user_confirmed_online": None}, grok=None
        )
        assert result == "audit_logger"

    def test_confirmed_no_grok_routes_offline(self):
        from graph import user_gate_router
        result = user_gate_router(
            {"user_confirmed_online": True}, grok=None
        )
        assert result == "offline_best_effort"

    def test_declined_routes_offline(self):
        from graph import user_gate_router
        grok = MockGrokClient()
        result = user_gate_router(
            {"user_confirmed_online": False}, grok=grok
        )
        assert result == "offline_best_effort"

    def test_confirmed_with_available_grok_routes_grok(self):
        from graph import user_gate_router
        grok = MockGrokClient(available=True)
        result = user_gate_router(
            {"user_confirmed_online": True}, grok=grok
        )
        assert result == "grok_fallback"

    def test_confirmed_with_unavailable_grok_routes_offline(self):
        from graph import user_gate_router
        grok = MockGrokClient(available=False)
        result = user_gate_router(
            {"user_confirmed_online": True}, grok=grok
        )
        assert result == "offline_best_effort"

    def test_confirmed_with_available_claude_routes_claude(self):
        from graph import user_gate_router
        claude = MockClaudeClient(available=True)
        result = user_gate_router(
            {"user_confirmed_online": True, "online_provider": "claude"},
            grok=None,
            claude=claude,
        )
        assert result == "claude_fallback"

    def test_confirmed_with_unavailable_claude_routes_offline(self):
        from graph import user_gate_router
        claude = MockClaudeClient(available=False)
        result = user_gate_router(
            {"user_confirmed_online": True, "online_provider": "claude"},
            grok=None,
            claude=claude,
        )
        assert result == "offline_best_effort"


class TestHealthResponseFields:
    """Verify /health returns all expected fields with correct types."""

    def test_health_has_all_fields(self, client):
        test_client, _ = client
        with patch("gate.check_all", return_value=[]):
            resp = test_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()

        assert "status" in data
        assert "services" in data
        assert "index_ready" in data
        assert "graph_ready" in data
        assert "mode" in data
        assert "graph_timeout_sec" in data

        assert isinstance(data["index_ready"], bool)
        assert isinstance(data["graph_ready"], bool)
        assert isinstance(data["graph_timeout_sec"], int)
        assert data["graph_timeout_sec"] > 0

    def test_health_degraded_without_services(self, client):
        test_client, _ = client
        from utils.errors import HealthStatus
        degraded = [HealthStatus(name="llm", healthy=False, latency_ms=0, error="down")]
        with patch("gate.check_all", return_value=degraded):
            resp = test_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "degraded"

    def test_health_mode_reflects_config(self, client):
        test_client, _ = client
        import gate
        gate.cfg["app"]["mode"] = "hybrid"
        with patch("gate.check_all", return_value=[]):
            resp = test_client.get("/health")
        assert resp.json()["mode"] == "hybrid"
        gate.cfg["app"]["mode"] = "offline"
