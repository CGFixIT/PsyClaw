"""Shared pytest fixtures for CyClaw test suite.

Mocks: LLM services, embedding model, retriever, test config.
No live services required — all external deps are mocked.
"""

import contextlib
import copy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from retrieval.hybrid_search import SearchResult


# DevSkim: ignore DS162092,DS137138 - test fixtures; loopback addresses are intentional
TEST_CONFIG = {
    "app": {"name": "cyclaw-test", "env": "test", "mode": "offline", "debug": True},
    "models": {
        "local_llm": {"provider": "ollama", "base_url": "http://127.0.0.1:11434/v1",
                      "model": "test-model", "max_tokens": 256, "temperature": 0.1, "timeout_sec": 10},
        "embeddings": {"provider": "sentence-transformers", "model": "all-MiniLM-L6-v2",
                       "dim": 384, "cache_dir": None},
        "grok": {"enabled": False, "base_url": "https://api.x.ai/v1", "model": "grok-4.5",
                 "timeout_sec": 10, "max_tokens": 256, "temperature": 0.2},
        "claude": {"enabled": False, "base_url": "https://api.anthropic.com/v1",
                   "model": "claude-sonnet-5", "anthropic_version": "2023-06-01",
                   "timeout_sec": 10, "max_tokens": 256}
    },
    "corpus": {"path": "data/corpus", "extensions": [".md", ".txt"]},
    "indexing": {"chroma_path": "", "bm25_path": "", "collection_name": "test_kb",
                 "chunk_size": 512, "chunk_overlap": 50, "batch_size": 10},
    "retrieval": {"top_k_semantic": 3, "top_k_keyword": 3, "rrf_k": 60,
                   "max_context_tokens": 1000, "min_score": 0.75,
                   "hybrid": {"enabled": True}},
    "policy": {
        "fallback": {"enabled": True, "require_user_confirm": True,
                     "send_local_context_to_grok": False,
                     "send_local_context_to_claude": False},
        "prompt_filter": {"enabled": True,
                          "banned_patterns": ["ignore previous instructions", "system prompt:"],
                          "max_input_chars": 4000},
        "privacy": {"redact_emails": True, "redact_ips": True,
                    "redact_secrets_like": ["AKIA[0-9A-Z]{16}"]}
    },
    "api": {"host": "127.0.0.1", "port": 8787},  # DevSkim: ignore DS162092
    # audit_file is a deliberately-nonexistent placeholder, never a real path.
    # Every consumer overrides it per-test (the test_config fixture below and the
    # autouse audit-routing fixtures in test_graph.py / test_due_diligence_
    # invariants.py all point it at tmp_path). The old value called
    # tempfile.mkdtemp() at module import — leaking one orphan /tmp dir per
    # pytest collection for a path nothing ever wrote to. If a future test uses
    # TEST_CONFIG raw and writes audit lines, this placeholder fails loudly
    # (missing directory) instead of scattering files under /tmp.
    "logging": {"level": "DEBUG", "log_file": "", "audit_file": "OVERRIDDEN-PER-TEST/audit.jsonl",
                "spend_file": "OVERRIDDEN-PER-TEST/spend.jsonl",
                "audit_fields": {"include_query_hash": True}},
    "security": {"require_env": ["GROK_API_KEY"],
                 "allowed_origins": ["http://127.0.0.1", "http://localhost"]},  # DevSkim: ignore DS162092,DS137138
    "personality": {"enabled": False, "soul_path": "", "db_path": "", "interaction_ttl_days": 90}
}


@pytest.fixture(autouse=True)
def _disarm_agentic_write_execution(request, monkeypatch):
    # Structural backstop against the unit lane opening a real pull request.
    #
    # agentic/writer.py's EXECUTION_ENABLED ships True (operator enablement,
    # 2026-08-07), and the real-repo CLI fixtures copy the shipped config.yaml
    # -- which now carries mode: "write" + writes_enabled: true -- and then set
    # enabled = True to exercise the pipeline. The only remaining thing between
    # a mis-stubbed test and a live `gh pr create --repo cgfixit/CyClaw` is
    # whether that individual test remembered to stub or monkeypatch. `gh` is
    # absent from most dev containers but IS preinstalled on GitHub Actions
    # runners, so "it didn't fire locally" is not evidence.
    #
    # Before the flag was armed this role was played by a source constant that
    # no test could accidentally satisfy. This fixture restores that property
    # at the suite level rather than leaving it to per-test discipline.
    #
    # Tests that are ABOUT the armed posture opt out with:
    #     @pytest.mark.uses_shipped_execution_flag
    # and are then responsible for their own stubbing.
    if request.node.get_closest_marker("uses_shipped_execution_flag"):
        return
    try:
        import agentic.writer as _writer
    except ModuleNotFoundError as exc:
        # Only the absent agentic package itself means "nothing to disarm".
        # A ModuleNotFoundError naming anything else is a broken transitive
        # import INSIDE agentic/writer.py -- swallowing it would leave
        # EXECUTION_ENABLED armed for the whole session, so re-raise.
        if exc.name == "agentic" or (exc.name or "").startswith("agentic."):
            return
        raise
    monkeypatch.setattr(_writer, "EXECUTION_ENABLED", False)


@pytest.fixture
def test_config(tmp_path):
    # Deep-copy so each test gets a fully independent config tree. The old
    # ``TEST_CONFIG.copy()`` was a SHALLOW copy that only hand-cloned the
    # ``indexing`` and ``logging`` sub-dicts; every other nested dict (models,
    # policy, retrieval, security, ...) was a shared reference to the module-level
    # TEST_CONFIG. A test mutating e.g. ``cfg["models"]["grok"]["enabled"]`` or
    # appending to ``cfg["policy"]["prompt_filter"]["banned_patterns"]`` would
    # poison the global and leak into every later test (order-dependent flakes,
    # amplified under pytest-xdist). deepcopy removes that whole class of bug.
    cfg = copy.deepcopy(TEST_CONFIG)
    cfg["indexing"]["chroma_path"] = str(tmp_path / "chroma_db")
    cfg["indexing"]["bm25_path"] = str(tmp_path / "bm25.json")
    cfg["logging"]["log_file"] = str(tmp_path / "cyclaw.log")
    cfg["logging"]["audit_file"] = str(tmp_path / "audit.jsonl")
    cfg["logging"]["spend_file"] = str(tmp_path / "spend.jsonl")
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(cfg, f)
    return cfg, str(config_file)


# =============================================================================
# Class-style mocks + result constants used by test_graph.py / test_gate.py.
#
# These mirror the dependency-injection contract of build_graph(retriever, llm,
# grok, cfg, personality): each mock exposes the same call surface the graph
# nodes touch (retriever.hybrid_search / llm.generate / grok.generate) and the
# LLM/Grok mocks record their last prompt so tests can assert on prompt content.
# =============================================================================

MOCK_HIGH_SCORE_RESULTS = [
    SearchResult(text="Veeam uses chattr +i to make backups immutable.", score=0.92,
                 source="veeam-immutability.md", chunk_id=0, stem_tags=["veeam", "immut"],
                 retrieval_mode="hybrid", rrf_score=0.92, semantic_score=0.92, semantic_rank=0),
    SearchResult(text="Immutable backups cannot be modified or deleted.", score=0.81,
                 source="veeam-immutability.md", chunk_id=1, stem_tags=["immut", "backup"],
                 retrieval_mode="hybrid", rrf_score=0.81, semantic_score=0.81, semantic_rank=1),
]

MOCK_LOW_SCORE_RESULTS = [
    SearchResult(text="A weakly related passage about unrelated topics.", score=0.30,
                 source="misc.md", chunk_id=0, stem_tags=["misc"],
                 retrieval_mode="hybrid", rrf_score=0.30, semantic_score=0.30, semantic_rank=0),
]

MOCK_EMPTY_RESULTS: list[SearchResult] = []


class MockRetriever:
    """Stand-in for HybridRetriever that returns a fixed result list."""
    def __init__(self, results):
        self.results = results

    def hybrid_search(self, query):
        return self.results

    def semantic_search(self, query, k=None):
        return self.results

    def keyword_search(self, query, k=None):
        return self.results


class MockLocalLLM:
    """Stand-in for LocalLLMClient; records the last prompt it was given."""
    def __init__(self, response="This is a test answer from the local LLM."):
        self.response = response
        self.last_prompt = None

    def generate(self, prompt, **kwargs):
        self.last_prompt = prompt
        self.last_spend_context = kwargs.get("spend_context")
        return self.response


class MockGrokClient:
    """Stand-in for GrokClient; records the last prompt it was given.

    ``available`` mirrors the real ``GrokClient.is_available()`` (True when a
    ``GROK_API_KEY`` is present). It defaults to True so existing routing tests
    still reach grok_fallback; set ``available=False`` to simulate Grok enabled
    in config but with no API key.
    """
    def __init__(self, response="This is a test answer from Grok.", available=True):
        self.response = response
        self.last_prompt = None
        self._available = available

    def is_available(self):
        return self._available

    def generate(self, prompt, **kwargs):
        self.last_prompt = prompt
        self.last_spend_context = kwargs.get("spend_context")
        return self.response


class MockClaudeClient(MockGrokClient):
    """Stand-in for ClaudeClient; same generate/is_available contract."""


@contextlib.contextmanager
def _mocked_gateway(tmp_path, *, peer=("127.0.0.1", 51234)):  # DevSkim: ignore DS162092,DS137138 - test loopback peer
    """Mocked-gateway TestClient shared by test_gate.py / test_edge_cases.py.

    Yields ``(test_client, mock_graph)``; per-test behavior differences are
    expressed by overriding ``mock_graph.invoke`` (return_value/side_effect).

    gate.py binds its config at module import time, so patching gate.open /
    gate.yaml.safe_load here would be dead code -- the real mechanism is the
    direct module-global assignment below, wrapped in save/restore so no mock
    leaks into the next test.

    ``peer`` is a parameter, not a constant, because gate's rate limiter is a
    process-global keyed per client IP with a 60 req/60 s budget: every file
    sharing one peer shares one budget, and a full-suite run can starve a
    later test (429 where 200/409 was asserted). Each consuming file picks its
    own loopback IP (127.0.0.0/8 is all loopback -- see
    gate._is_loopback_host).
    """
    # Lazy: conftest must import cleanly in minimal-dep CI jobs (e.g.
    # ollama-mock-smoke) that never install fastapi -- a top-level import
    # broke collection there (exit 4) on the first push of this fixture.
    from fastapi.testclient import TestClient

    from utils.logger import reset_config_cache
    reset_config_cache()

    cfg = copy.deepcopy(TEST_CONFIG)
    cfg["logging"]["audit_file"] = str(tmp_path / "audit.jsonl")
    cfg["logging"]["log_file"] = str(tmp_path / "gateway.log")

    with patch("gate.cfg", cfg), \
         patch("gate.HybridRetriever"), \
         patch("gate.LocalLLMClient"), \
         patch("gate.ClaudeClient"), \
         patch("gate.build_graph") as mock_build, \
         patch("gate.check_input", side_effect=lambda q: q), \
         patch("gate.check_all", return_value=[]):

        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "query": "test query",
            "answer": "Test answer from local LLM.",
            "answer_model": "local",
            "answer_sources": [
                {"source": "test.md", "score": 0.9, "chunk_id": 0, "stem_tags": ["test"], "text": "...", "mode": "hybrid"}
            ],
            "retrieved_docs": [{"text": "...", "score": 0.9, "source": "test.md", "chunk_id": 0, "stem_tags": [], "mode": "hybrid"}],
            "top_score": 0.9,
            "retrieval_mode": "hybrid",
            "needs_user_confirm": False,
            "audit_event": {}
        }
        mock_build.return_value = mock_graph

        import gate
        gate.cfg = cfg
        _globals = ("retriever", "local_llm", "grok", "claude", "compiled_graph")
        _saved = {k: getattr(gate, k, None) for k in _globals}
        try:
            gate.retriever = MockRetriever(MOCK_HIGH_SCORE_RESULTS)
            gate.local_llm = MockLocalLLM()
            gate.grok = None
            gate.claude = None
            gate.compiled_graph = mock_graph

            # base_url uses an allowed Host (localhost) so TrustedHostMiddleware
            # (added at import from the real config.yaml allowed_hosts) admits the
            # request; the default "testserver" host would otherwise 400.
            test_client = TestClient(
                gate.app,
                base_url="http://localhost",  # DevSkim: ignore DS162092,DS137138 - test loopback host
                # Starlette defaults the peer to ("testclient", 50000), which is
                # deliberately NOT loopback under _is_loopback_peer. A real
                # loopback peer exercises the ordinary local-operator case; the
                # non-loopback case is asserted explicitly in test_gate.py's
                # TestApiKeyOptionalPeer.
                client=peer,
            )
            yield test_client, mock_graph
        finally:
            for k, v in _saved.items():
                setattr(gate, k, v)

    reset_config_cache()


@pytest.fixture
def client(request, tmp_path):
    """Default mocked-gateway client: the (127.0.0.1, 51234) rate-limit bucket
    is the one test_gate.py has always used -- do not point a second file's
    fixture at it; give each file its own loopback IP via _mocked_gateway.

    Supports indirect parametrization: @pytest.mark.parametrize("client",
    [peer], indirect=True) overrides the peer, e.g. to give one test class its
    own rate-limit bucket. Direct use keeps the historical default peer."""
    peer = getattr(request, "param", ("127.0.0.1", 51234))
    with _mocked_gateway(tmp_path, peer=peer) as pair:
        yield pair


@pytest.fixture(autouse=True)
def _inject_argv_list_sandbox_except_hard_sandbox(request, monkeypatch):
    """Keep Linux/macOS CI green without a production software fallback.

    ``test_agentic_hard_sandbox.py`` is the only file allowed to call the real
    ``production_sandbox()`` factory so fail-closed stays covered.
    """
    path = getattr(request.node, "fspath", None)
    if path is not None and "test_agentic_hard_sandbox" in str(path):
        return
    from tests.executor_sandbox_double import inject_argv_list_sandbox

    inject_argv_list_sandbox(monkeypatch)
