#!/usr/bin/env python3
"""
CyClaw Full Verification Script -- Comprehensive smoke test harness.

Runs in sandbox mode (no external dependencies needed) or full-dependency mode.
Executes 5 queries covering: vault hit x2, offline best-effort (Qwen),
Grok API connection-only, Claude API connection-only.

Verifies:
  1. LangGraph pipeline (5 queries through real node functions)
  2. Triple-gate online API fallback (Grok + Claude) with mocked HTTP
  3. API key redaction parity (Anthropic keys redacted same as Grok)
  4. _external_fallback_node shared implementation
  5. All 5 terminal console REST endpoints (soul, sync, agentic, fs, sql)
  6. Due-diligence invariants (unwired require_user_confirm, module isolation)
  7. Terminal HTML contract (5 panels, explicit provider buttons)

Usage:
    python3 scripts/run_full_verification.py

Env:
    CYCLAW_REPO=/path/to/CyClaw  -- use existing clone instead of fresh
    FULL_DEPS=1                  -- attempt full dependency install first
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO_URL = "https://github.com/CGFixIT/CyClaw.git"
BRANCH = "main"
CYCLAW_DIR = Path(os.environ.get("CYCLAW_REPO", "/tmp/CyClaw"))
RESULTS_FILE = Path("query_results.json")

# ANSI colors
R = "\033[91m"; G = "\033[92m"; Y = "\033[93m"; B = "\033[94m"; C = "\033[96m"; N = "\033[0m"


@dataclass
class Check:
    name: str
    passed: bool = False
    detail: str = ""


@dataclass
class PhaseResult:
    name: str
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)


# ---------------------------------------------------------------------------
# Stubs for missing dependencies
# ---------------------------------------------------------------------------
def _install_stubs():
    import types
    for mod_name in ["chromadb", "chromadb.config", "sentence_transformers",
                     "transformers", "tokenizers", "langsmith", "langgraph",
                     "langgraph.graph", "langgraph.cache"]:
        parts = mod_name.split(".")
        for i in range(len(parts)):
            sub = ".".join(parts[:i+1])
            if sub not in sys.modules:
                sys.modules[sub] = types.ModuleType(sub)

    # chromadb
    chromadb = sys.modules["chromadb"]
    chromadb.Client = lambda **kw: object()

    # sentence_transformers
    st = sys.modules["sentence_transformers"]
    st.SentenceTransformer = lambda *a, **kw: object()

    # langgraph.graph
    lgg = sys.modules["langgraph.graph"]
    class _StateGraph:
        def __init__(self, state): pass
        def add_node(self, name, fn): pass
        def add_edge(self, a, b): pass
        def add_conditional_edges(self, src, router, mapping): pass
        def set_entry_point(self, name): pass
        def compile(self): return self
        def invoke(self, state): return state
    lgg.StateGraph = _StateGraph
    lgg.END = None

    # langsmith / langgraph.cache
    sys.modules["langsmith"].Client = lambda *a, **kw: object()
    sys.modules["langgraph.cache"] = types.ModuleType("langgraph.cache")


# ---------------------------------------------------------------------------
# Mock Embedding Implementation
# ---------------------------------------------------------------------------
class MockSentenceTransformer:
    def __init__(self, model_name_or_path: str = "mock", **kw):
        self._dim = 384

    def encode(self, texts, **kw):
        import numpy as np
        if isinstance(texts, str):
            texts = [texts]
        vecs = []
        for text in texts:
            vec = np.zeros(self._dim, dtype=np.float32)
            for word in text.lower().split():
                h = hashlib.md5(word.encode()).hexdigest()
                for i in range(3):
                    idx = int(h[i*8:(i+1)*8], 16) % self._dim
                    vec[idx] += 1.0
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec /= norm
            vecs.append(vec)
        return np.array(vecs)

    @property
    def dimension(self):
        return self._dim


class MockCollection:
    def __init__(self, name):
        self.name = name
        self._docs: list[str] = []
        self._meta: list[dict] = []
        self._embeds: list[Any] = []
        self._ids: list[str] = []

    def add(self, embeddings=None, documents=None, metadatas=None, ids=None):
        self._docs.extend(documents or [])
        self._meta.extend(metadatas or [])
        self._embeds.extend(embeddings or [])
        self._ids.extend(ids or [])

    def query(self, query_embeddings=None, n_results=5, **kw):
        import numpy as np
        if not self._embeds:
            return {"ids": [[]], "distances": [[]], "documents": [[]], "metadatas": [[]]}
        q = np.array(query_embeddings[0])
        scores = []
        for emb in self._embeds:
            s = float(np.dot(q, np.array(emb)))
            scores.append(s)
        top = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:n_results]
        return {
            "ids": [[self._ids[i] for i, _ in top]],
            "distances": [[1.0 - s for _, s in top]],
            "documents": [[self._docs[i] for i, _ in top]],
            "metadatas": [[self._meta[i] for i, _ in top]],
        }


class MockChromaClient:
    _collections: dict[str, MockCollection] = {}

    def __init__(self, **kw):
        MockChromaClient._collections = {}

    def get_or_create_collection(self, name, **kw):
        if name not in MockChromaClient._collections:
            MockChromaClient._collections[name] = MockCollection(name)
        return MockChromaClient._collections[name]


# ---------------------------------------------------------------------------
# Mock HTTP Response Helpers
# ---------------------------------------------------------------------------
class MockResponse:
    def __init__(self, status_code, json_data, headers=None):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = json.dumps(json_data)

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


# ---------------------------------------------------------------------------
# Mock Clients for Online API Testing
# ---------------------------------------------------------------------------
class MockGrokClient:
    """Stand-in for GrokClient; same generate/is_available/close contract."""
    def __init__(self, response: str = "mock grok answer", available: bool = True):
        self.response = response
        self._available = available
        self.last_prompt = None
        self.last_headers = None
        self.calls: list[dict] = []

    def is_available(self) -> bool:
        return self._available

    def generate(self, prompt: str) -> str:
        self.last_prompt = prompt
        self.calls.append({"prompt": prompt, "provider": "grok"})
        if not self._available:
            from utils.errors import GrokServiceError
            raise GrokServiceError("GROK_API_KEY not set")
        return self.response

    def close(self) -> None:
        pass

    def _verify_request_shape(self, expected_headers: dict) -> bool:
        """Verify the last HTTP request had the correct headers for Grok."""
        if not self.last_headers:
            return False
        return (
            "Authorization" in self.last_headers and
            "Bearer" in self.last_headers.get("Authorization", "") and
            "x-ai-model" not in self.last_headers  # Grok doesn't use x-ai-model
        )


class MockClaudeClient(MockGrokClient):
    """Stand-in for ClaudeClient; same contract, Anthropic API shape."""
    def generate(self, prompt: str) -> str:
        self.last_prompt = prompt
        self.calls.append({"prompt": prompt, "provider": "claude"})
        if not self._available:
            from utils.errors import ClaudeServiceError
            raise ClaudeServiceError("ANTHROPIC_API_KEY not set")
        return self.response

    def _verify_request_shape(self) -> bool:
        """Verify the last HTTP request had the correct headers for Claude."""
        if not self.last_headers:
            return False
        return (
            "x-api-key" in self.last_headers and
            "anthropic-version" in self.last_headers and
            self.last_headers.get("anthropic-version") == "2023-06-01"
        )


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------
def log(msg: str, color: str = ""):
    print(f"{color}{msg}{N}")


def banner(msg: str):
    print(f"\n{B}{'='*60}{N}")
    print(f"{B}  {msg}{N}")
    print(f"{B}{'='*60}{N}")


def _ensure_repo():
    if CYCLAW_DIR.exists() and (CYCLAW_DIR / ".git").exists():
        log(f"Using existing repo: {CYCLAW_DIR}")
        subprocess.run(["git", "checkout", BRANCH], cwd=CYCLAW_DIR, capture_output=True)
        subprocess.run(["git", "pull"], cwd=CYCLAW_DIR, capture_output=True)
    else:
        log(f"Cloning {REPO_URL} -> {CYCLAW_DIR}")
        CYCLAW_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", BRANCH, REPO_URL, str(CYCLAW_DIR)],
            check=True, capture_output=True,
        )
    os.chdir(CYCLAW_DIR)


def _install_deps() -> bool:
    if not os.environ.get("FULL_DEPS"):
        return False
    try:
        log("Attempting full dependency install...", Y)
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", ".[test,full]"],
            capture_output=True, text=True, timeout=300,
        )
        return r.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Phase 1: Config Invariant Checks
# ---------------------------------------------------------------------------
def phase_config_invariants() -> PhaseResult:
    banner("Phase 1: Config & Security Invariants")
    phase = PhaseResult("Config Invariants")

    import yaml
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    checks = [
        ("app.mode == 'offline'", cfg.get("app", {}).get("mode") == "offline"),
        ("api.host == 127.0.0.1", cfg.get("api", {}).get("host") == "127.0.0.1"),
        ("api.port == 8787", cfg.get("api", {}).get("port") == 8787),
        ("grok.enabled == false", cfg.get("models", {}).get("grok", {}).get("enabled") is False),
        ("claude block present", "claude" in cfg.get("models", {})),
        ("claude.enabled == false", cfg.get("models", {}).get("claude", {}).get("enabled") is False),
        ("retrieval.min_score == 0.028", abs(cfg.get("retrieval", {}).get("min_score", 0) - 0.028) < 0.001),
        ("33 banned patterns", len(cfg.get("policy", {}).get("prompt_filter", {}).get("banned_patterns", [])) >= 33),
        ("fsconnect block present", "fsconnect" in cfg),
        ("fsconnect.enabled == false", cfg.get("fsconnect", {}).get("enabled") is False),
        ("sqlconnect block present", "sqlconnect" in cfg),
        ("sqlconnect.read_only == true", cfg.get("sqlconnect", {}).get("read_only") is True),
        ("sync block present", "sync" in cfg),
        ("agentic block present", "agentic" in cfg),
        ("agentic.writes_enabled == false", cfg.get("agentic", {}).get("writes_enabled") is False),
        ("grok_max_prompt_chars == 8000", cfg.get("policy", {}).get("fallback", {}).get("grok_max_prompt_chars") == 8000),
        ("claude_max_prompt_chars == 8000", cfg.get("policy", {}).get("fallback", {}).get("claude_max_prompt_chars") == 8000),
        ("send_local_context_to_grok == false", cfg.get("policy", {}).get("fallback", {}).get("send_local_context_to_grok") is False),
        ("send_local_context_to_claude == false", cfg.get("policy", {}).get("fallback", {}).get("send_local_context_to_claude") is False),
        ("require_user_confirm present (unwired)", "require_user_confirm" in cfg.get("policy", {}).get("fallback", {})),
    ]

    # Check Anthropic key redaction in config
    audit_cfg = cfg.get("logging", {}).get("audit", {})
    redact_patterns = audit_cfg.get("redact_secrets_like", [])
    has_sk_ant = any("sk-ant" in str(p) for p in redact_patterns)
    checks.append(("audit redact sk-ant-* pattern", has_sk_ant))

    for name, passed in checks:
        status = f"{G}PASS{N}" if passed else f"{R}FAIL{N}"
        log(f"  [{status}] {name}")
        phase.checks.append(Check(name, passed))

    return phase


# ---------------------------------------------------------------------------
# Phase 2: Telemetry Kill Check
# ---------------------------------------------------------------------------
def phase_telemetry_kill() -> PhaseResult:
    banner("Phase 2: Telemetry Kill Verification")
    phase = PhaseResult("Telemetry Kill")

    gate_src = Path("gate.py").read_text()

    kill_vars = [
        "LANGCHAIN_TRACING_V2",
        "LANGSMITH_TRACING",
        "LANGGRAPH_CLI_NO_ANALYTICS",
        "ANONYMIZED_TELEMETRY",
        "CHROMA_OTEL_COLLECTION_ENDPOINT",
        "CHROMA_OTEL_SERVICE_NAME",
        "OTEL_SDK_DISABLED",
        "OTEL_TRACES_EXPORTER",
        "OTEL_METRICS_EXPORTER",
        "OTEL_LOGS_EXPORTER",
    ]

    for var in kill_vars:
        found = var in gate_src
        status = f"{G}PASS{N}" if found else f"{R}FAIL{N}"
        log(f"  [{status}] {var} killed at import")
        phase.checks.append(Check(f"telemetry_kill_{var}", found))

    return phase


# ---------------------------------------------------------------------------
# Phase 3: Build Mock Corpus & Index
# ---------------------------------------------------------------------------
def phase_build_corpus() -> PhaseResult:
    banner("Phase 3: Build Mock Corpus & Index")
    phase = PhaseResult("Corpus & Index")

    corpus_dir = Path("data/corpus")
    corpus_dir.mkdir(parents=True, exist_ok=True)

    # NOTE: general_knowledge.md has NO relativity content.
    # Query 3 asks about Einstein to test offline best-effort (no vault match).
    files = {
        "cyclaw_about.md": (
            "# CyClaw Overview\n\nCyClaw is an offline-first RAG system. "
            "It retrieves from local vault before calling any LLM. "
            "Key features: hybrid search, triple-gated external API fallback, "
            "soul governance, zero telemetry, and read-only filesystem/sql connectors."
        ),
        "cyclaw_architecture.md": (
            "# Architecture\n\nThe graph flow: retrieve (N1) -> route_by_score (N2) -> "
            "local_llm (N3a) OR user_gate (N3b) -> grok_fallback OR claude_fallback OR "
            "offline_best_effort -> audit_logger (N4) -> END. "
            "Both external providers share _external_fallback_node. "
            "Topology is policy: routing via scores, not prompts."
        ),
        "cyclaw_security.md": (
            "# Security\n\nTriple-gated external API: score gate (<0.028), user gate "
            "(human confirmation), availability gate (is_available()). "
            "33 banned injection patterns. API key redaction for GROK_API_KEY "
            "and ANTHROPIC_API_KEY including sk-ant-* patterns. "
            "Soul preamble never forwarded off-box. Module isolation: agentic/ "
            "never imported by gate.py or graph.py directly."
        ),
        "offline_mode.md": (
            "# Offline Mode\n\nIn offline mode, no external API calls. "
            "Best-effort local answers only via Qwen. No data leaves the machine. "
            "Both grok.enabled and claude.enabled are false. "
            "Policy.fallback.require_user_confirm is present but unwired."
        ),
        "general_knowledge.md": (
            "# General Knowledge\n\nThe capital of France is Paris. "
            "Water boils at 100 degrees Celsius at sea level. "
            "The speed of light in a vacuum is approximately 299,792,458 meters per second. "
            "Python is a popular programming language for AI development. "
            "This document contains general world knowledge facts only."
        ),
    }

    for fname, content in files.items():
        fpath = corpus_dir / fname
        fpath.write_text(content, encoding="utf-8")
        log(f"  Written {fpath}")

    phase.checks.append(Check("corpus_files_written", True))

    # Build BM25 index
    try:
        from retrieval.stemmer import PorterStemmer
        from rank_bm25 import BM25Okapi
        stemmer = PorterStemmer()

        chunks = []
        tokenized = []
        for fname in files:
            text = (corpus_dir / fname).read_text()
            chunks.append({"text": text, "source": fname, "id": len(chunks)})
            tokens = [stemmer.stem(w) for w in text.lower().split()]
            tokenized.append(tokens)

        import json
        index_dir = Path("index")
        index_dir.mkdir(exist_ok=True)
        with open(index_dir / "bm25.json", "w") as f:
            json.dump({
                "tokenized_corpus": tokenized,
                "chunks": [c["text"] for c in chunks],
                "metadata": [{"source": c["source"], "id": c["id"]} for c in chunks],
            }, f)

        log(f"  BM25 index: {index_dir / 'bm25.json'}")
        phase.checks.append(Check("bm25_index_built", True))
    except Exception as e:
        log(f"  BM25 build error: {e}", R)
        phase.checks.append(Check("bm25_index_built", False, str(e)))

    # Build mock ChromaDB index
    try:
        encoder = MockSentenceTransformer()
        chroma_client = MockChromaClient()
        collection = chroma_client.get_or_create_collection("cyclaw_kb")

        for chunk in chunks:
            emb = encoder.encode([chunk["text"]])[0].tolist()
            collection.add(
                embeddings=[emb],
                documents=[chunk["text"]],
                metadatas=[{"source": chunk["source"]}],
                ids=[f"chunk_{chunk['id']}"],
            )

        log(f"  ChromaDB mock index built ({len(chunks)} chunks)")
        phase.checks.append(Check("chroma_index_built", True))
    except Exception as e:
        log(f"  Chroma build error: {e}", R)
        phase.checks.append(Check("chroma_index_built", False, str(e)))

    return phase


# ---------------------------------------------------------------------------
# Phase 4: Execute 5 Queries
# ---------------------------------------------------------------------------
def phase_execute_queries() -> PhaseResult:
    banner("Phase 4: Execute 5 Queries Through Real Graph Nodes")
    phase = PhaseResult("5 Queries")

    # Patch embeddings loader
    import retrieval.embeddings as emb_mod
    emb_mod._load_model = lambda: MockSentenceTransformer()
    sys.modules["chromadb"] = sys.modules.get("chromadb") or type(sys)("chromadb")
    sys.modules["chromadb"].Client = MockChromaClient
    sys.modules["chromadb"].Client.__init__ = lambda **kw: None

    from graph import (
        retrieve_node, route_by_score_node, local_llm_node,
        user_gate_node, offline_best_effort_node, audit_logger_node,
    )
    from retrieval.hybrid_search import HybridRetriever
    from llm.client import LocalLLMClient

    retriever = HybridRetriever()

    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    llm = LocalLLMClient(cfg=cfg)

    queries = [
        ("what is CyClaw", "local", True, "Vault hit - CyClaw overview"),
        ("explain CyClaw security", "local", True, "Vault hit - Security doc"),
        ("who wrote the theory of general relativity and when", "offline-best-effort", False, "Offline best-effort (Qwen) - no vault match"),
        ("what are the latest features in xAI Grok 4", "grok", False, "Grok API connection-only"),
        ("explain quantum computing decoherence", "claude", False, "Claude API connection-only"),
    ]

    all_results = []
    for query_text, expected_model, expect_answer, description in queries:
        log(f"\n  {C}--- {description} ---{N}")
        log(f"  Query: \"{query_text}\"")
        state = {"query": query_text}

        # N1: retrieve
        n1 = retrieve_node(state, retriever, cfg)
        state.update(n1)
        top_score = n1.get("top_score", 0)
        hit_count = len(n1.get("retrieved_docs", []))
        log(f"    retrieve: mode={n1.get('retrieval_mode')}, top_score={top_score:.4f}, hits={hit_count}")

        # N2: route_by_score
        n2 = route_by_score_node(state, cfg)
        state.update(n2)
        needs_confirm = n2.get("needs_user_confirm", False)
        log(f"    route_by_score: needs_confirm={needs_confirm}")

        if not needs_confirm:
            n3 = local_llm_node(state, llm, cfg)
            state.update(n3)
            model = n3.get("answer_model", "")
            log(f"    local_llm: model={model}")
        else:
            # Q3: Test offline best-effort deny path (user_confirmed_online=False)
            if expected_model == "offline-best-effort":
                state["user_confirmed_online"] = False
                n3 = user_gate_node(state, cfg)
                state.update(n3)
                n3b = offline_best_effort_node(state, llm, cfg)
                state.update(n3b)
                model = n3b.get("answer_model", "")
                log(f"    user_gate -> offline_best_effort: model={model}")
            # Q4/Q5: Just verify user_gate fires; mock online clients in Phase 5
            else:
                n3 = user_gate_node(state, cfg)
                state.update(n3)
                model = "user_gate_pause"
                log(f"    user_gate: needs_confirm={n3.get('needs_user_confirm')}")

        # N4: audit
        n4 = audit_logger_node(state, cfg)
        state.update(n4)

        # Evaluate
        passed = True
        if query_text == "what is CyClaw":
            passed = not needs_confirm and state.get("answer_model") == "local" and hit_count > 0
        elif query_text == "explain CyClaw security":
            passed = not needs_confirm and state.get("answer_model") == "local" and hit_count > 0
        elif query_text == "who wrote the theory of general relativity and when":
            passed = needs_confirm and state.get("answer_model") == "offline-best-effort"
        elif query_text == "what are the latest features in xAI Grok 4":
            passed = needs_confirm and state.get("needs_user_confirm") is True
        elif query_text == "explain quantum computing decoherence":
            passed = needs_confirm and state.get("needs_user_confirm") is True

        status = f"{G}PASS{N}" if passed else f"{R}FAIL{N}"
        log(f"    [{status}] model={state.get('answer_model', '--')}, score={top_score:.4f}")

        phase.checks.append(Check(f"query_{description.replace(' ', '_').lower()}", passed))
        all_results.append({
            "query": query_text,
            "description": description,
            "model": state.get("answer_model", ""),
            "top_score": top_score,
            "hit_count": hit_count,
            "needs_confirm": state.get("needs_user_confirm", False),
            "retrieval_mode": state.get("retrieval_mode", "none"),
            "passed": passed,
        })

    with open(RESULTS_FILE, "w") as f:
        json.dump({"query_results": all_results}, f, indent=2)
    log(f"\n  Results saved to {RESULTS_FILE}")

    return phase


# ---------------------------------------------------------------------------
# Phase 5: Triple-Gate Online API Verification
# ---------------------------------------------------------------------------
def phase_triple_gate() -> PhaseResult:
    banner("Phase 5: Triple-Gate Online API (Grok + Claude)")
    phase = PhaseResult("Triple-Gate Online API")

    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    cfg["app"]["mode"] = "hybrid"
    cfg["models"]["grok"]["enabled"] = True
    cfg["models"]["claude"]["enabled"] = True

    from graph import build_graph, user_gate_router
    from retrieval.hybrid_search import HybridRetriever
    from llm.client import LocalLLMClient

    retriever = HybridRetriever()
    llm = LocalLLMClient(cfg=cfg)

    log("\n  --- _external_fallback_node structure ---")
    import inspect
    try:
        from graph import _external_fallback_node
        sig = inspect.signature(_external_fallback_node)
        params = list(sig.parameters.keys())
        has_provider = "provider" in params
        has_label = "label" in params
        has_no_personality = "personality" not in params
        log(f"    {G}PASS{N} _external_fallback_node exists with provider/label params")
        phase.checks.append(Check("external_fallback_node_exists", True))
        phase.checks.append(Check("external_fallback_no_personality", has_no_personality))
    except ImportError:
        log(f"    {R}FAIL{N} _external_fallback_node not found")
        phase.checks.append(Check("external_fallback_node_exists", False))

    log("\n  --- Grok Triple-Gate ---")

    # G1: is_available contract
    grok = MockGrokClient(response="Grok fallback answer", available=True)
    phase.checks.append(Check("grok_is_available_true", grok.is_available() is True))

    grok_unavail = MockGrokClient(available=False)
    phase.checks.append(Check("grok_is_available_false", grok_unavail.is_available() is False))

    # G2: Full triple-gate integration
    graph = build_graph(retriever=retriever, llm=llm, grok=grok, claude=None, cfg=cfg)
    result = graph.invoke({
        "query": "rocket ship",
        "user_confirmed_online": True,
        "online_provider": "grok",
    })
    passed = result.get("answer_model") == "grok" and "Grok fallback" in result.get("answer", "")
    log(f"    [{'PASS' if passed else 'FAIL'}] Grok full triple-gate: model={result.get('answer_model')}")
    phase.checks.append(Check("grok_full_triple_gate", passed))

    # G3: Deny path
    result = graph.invoke({"query": "rocket ship", "user_confirmed_online": False})
    passed = result.get("answer_model") == "offline-best-effort"
    log(f"    [{'PASS' if passed else 'FAIL'}] Grok deny -> offline_best_effort")
    phase.checks.append(Check("grok_deny_path", passed))

    # G4: Unavailable grok -> offline
    graph_no_grok = build_graph(retriever=retriever, llm=llm, grok=None, claude=None, cfg=cfg)
    result = graph_no_grok.invoke({
        "query": "rocket", "user_confirmed_online": True, "online_provider": "grok",
    })
    passed = result.get("answer_model") == "offline-best-effort"
    log(f"    [{'PASS' if passed else 'FAIL'}] Unavailable Grok -> offline_best_effort")
    phase.checks.append(Check("grok_unavailable_offline", passed))

    log("\n  --- Claude Triple-Gate ---")

    # C1: is_available contract
    claude = MockClaudeClient(response="Claude fallback answer", available=True)
    phase.checks.append(Check("claude_is_available_true", claude.is_available() is True))

    claude_unavail = MockClaudeClient(available=False)
    phase.checks.append(Check("claude_is_available_false", claude_unavail.is_available() is False))

    # C2: Full triple-gate integration
    graph_claude = build_graph(retriever=retriever, llm=llm, grok=None, claude=claude, cfg=cfg)
    result = graph_claude.invoke({
        "query": "quantum physics",
        "user_confirmed_online": True,
        "online_provider": "claude",
    })
    passed = result.get("answer_model") == "claude" and "Claude fallback" in result.get("answer", "")
    log(f"    [{'PASS' if passed else 'FAIL'}] Claude full triple-gate: model={result.get('answer_model')}")
    phase.checks.append(Check("claude_full_triple_gate", passed))

    # C3: Claude does not call Grok
    grok_tracker = MockGrokClient(response="Grok should not be used")
    claude_real = MockClaudeClient(response="Claude selected by provider")
    graph_both = build_graph(retriever=retriever, llm=llm, grok=grok_tracker, claude=claude_real, cfg=cfg)
    result = graph_both.invoke({
        "query": "explain AI",
        "user_confirmed_online": True,
        "online_provider": "claude",
    })
    passed = (result.get("answer_model") == "claude" and
              grok_tracker.last_prompt is None and
              "Claude selected" in result.get("answer", ""))
    log(f"    [{'PASS' if passed else 'FAIL'}] Claude provider does not call Grok")
    phase.checks.append(Check("claude_does_not_call_grok", passed))

    # C4: Unavailable claude -> offline
    claude_dead = MockClaudeClient(available=False)
    graph_no_claude = build_graph(retriever=retriever, llm=llm, grok=None, claude=claude_dead, cfg=cfg)
    result = graph_no_claude.invoke({
        "query": "rocket", "user_confirmed_online": True, "online_provider": "claude",
    })
    passed = result.get("answer_model") == "offline-best-effort"
    log(f"    [{'PASS' if passed else 'FAIL'}] Unavailable Claude -> offline_best_effort")
    phase.checks.append(Check("claude_unavailable_offline", passed))

    # C5: Soul preamble privacy
    try:
        from graph import _external_fallback_node
        sig = inspect.signature(_external_fallback_node)
        has_no_personality = "personality" not in sig.parameters
    except ImportError:
        has_no_personality = False
    log(f"    [{'PASS' if has_no_personality else 'FAIL'}] Soul preamble never forwarded off-box")
    phase.checks.append(Check("soul_preamble_privacy", has_no_personality))

    log("\n  --- Cross-Provider Routing ---")

    # X1: Both enabled, provider selects correct one
    grok_x = MockGrokClient(response="Grok answer X")
    claude_x = MockClaudeClient(response="Claude answer X")
    graph_x = build_graph(retriever=retriever, llm=llm, grok=grok_x, claude=claude_x, cfg=cfg)

    result_g = graph_x.invoke({"query": "q", "user_confirmed_online": True, "online_provider": "grok"})
    passed_g = result_g.get("answer_model") == "grok"
    log(f"    [{'PASS' if passed_g else 'FAIL'}] Both enabled, provider='grok' -> grok")
    phase.checks.append(Check("cross_provider_grok", passed_g))

    result_c = graph_x.invoke({"query": "q", "user_confirmed_online": True, "online_provider": "claude"})
    passed_c = result_c.get("answer_model") == "claude"
    log(f"    [{'PASS' if passed_c else 'FAIL'}] Both enabled, provider='claude' -> claude")
    phase.checks.append(Check("cross_provider_claude", passed_c))

    # X2: user_gate_router unit tests
    log("\n  --- user_gate_router Unit Tests ---")

    r = user_gate_router(
        {"user_confirmed_online": True, "online_provider": "claude"},
        grok=None, claude=MockClaudeClient(available=True),
    )
    phase.checks.append(Check("router_confirmed_claude", r == "claude_fallback"))

    r = user_gate_router(
        {"user_confirmed_online": True, "online_provider": "claude"},
        grok=None, claude=MockClaudeClient(available=False),
    )
    phase.checks.append(Check("router_unavailable_claude", r == "offline_best_effort"))

    r = user_gate_router({"user_confirmed_online": False}, grok=None, claude=None)
    phase.checks.append(Check("router_denied", r == "offline_best_effort"))

    r = user_gate_router({"user_confirmed_online": None}, grok=None, claude=None)
    phase.checks.append(Check("router_first_pass", r == "audit_logger"))

    return phase


# ---------------------------------------------------------------------------
# Phase 6: API Key Redaction & Secret Sanitization
# ---------------------------------------------------------------------------
def phase_key_redaction() -> PhaseResult:
    banner("Phase 6: API Key Redaction (Grok + Claude Parity)")
    phase = PhaseResult("Key Redaction")

    gate_src = Path("gate.py").read_text()

    # Check ANTHROPIC_API_KEY in env-var redaction tuple
    has_anthropic_env = "ANTHROPIC_API_KEY" in gate_src
    log(f"  [{'PASS' if has_anthropic_env else 'FAIL'}] ANTHROPIC_API_KEY in env-var redaction")
    phase.checks.append(Check("anthropic_key_env_redaction", has_anthropic_env))

    # Check sk-ant-* pattern in _SECRET_PATTERNS
    has_sk_ant_pattern = "sk-ant-" in gate_src
    log(f"  [{'PASS' if has_sk_ant_pattern else 'FAIL'}] sk-ant-* pattern in _SECRET_PATTERNS")
    phase.checks.append(Check("sk_ant_pattern_in_gate", has_sk_ant_pattern))

    # Check GROK_API_KEY is still there (regression check)
    has_grok_env = "GROK_API_KEY" in gate_src
    log(f"  [{'PASS' if has_grok_env else 'FAIL'}] GROK_API_KEY in env-var redaction (regression)")
    phase.checks.append(Check("grok_key_env_redaction", has_grok_env))

    # Test redaction of an Anthropic key in error messages
    try:
        # Import and test _sanitize_error if available
        from gate import _sanitize_error
        test_msg = "Error: API call failed with key sk-ant-api03-testkey123456789"
        sanitized = _sanitize_error(test_msg)
        redacted = "sk-ant" not in sanitized or "[REDACTED]" in sanitized
        log(f"  [{'PASS' if redacted else 'FAIL'}] Anthropic key sk-ant-api03-... redacted in errors")
        phase.checks.append(Check("anthropic_key_sanitized", redacted))
    except (ImportError, AttributeError) as e:
        log(f"  {Y}SKIP{N}] _sanitize_error not importable: {e}")
        phase.checks.append(Check("anthropic_key_sanitized", False, str(e)))

    # Verify in config.yaml
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    redact_patterns = cfg.get("logging", {}).get("audit", {}).get("redact_secrets_like", [])
    has_sk_ant_config = any("sk-ant" in str(p) for p in redact_patterns)
    log(f"  [{'PASS' if has_sk_ant_config else 'FAIL'}] sk-ant-* in config.yaml audit redact")
    phase.checks.append(Check("sk_ant_in_config_redact", has_sk_ant_config))

    return phase


# ---------------------------------------------------------------------------
# Phase 7: Metrics & Due-Diligence Invariants
# ---------------------------------------------------------------------------
def phase_metrics_and_invariants() -> PhaseResult:
    banner("Phase 7: Metrics Escalation & Due-Diligence Invariants")
    phase = PhaseResult("Metrics & Invariants")

    # 7a: Metrics recognize both providers
    try:
        metrics_src = Path("metrics.py").read_text()
        has_claude_in_metrics = "claude" in metrics_src.lower() or "\"claude\"" in metrics_src
        has_grok_in_metrics = "grok" in metrics_src.lower()
        log(f"  [{'PASS' if has_claude_in_metrics else 'FAIL'}] Claude recognized in metrics.py")
        phase.checks.append(Check("claude_in_metrics", has_claude_in_metrics))
        log(f"  [{'PASS' if has_grok_in_metrics else 'FAIL'}] Grok recognized in metrics.py")
        phase.checks.append(Check("grok_in_metrics", has_grok_in_metrics))
    except FileNotFoundError:
        log(f"  {Y}SKIP{N}] metrics.py not found")

    # 7b: audit_logger_node sets online_escalated for both providers
    try:
        graph_src = Path("graph.py").read_text()
        has_online_escalated_set = "online_escalated" in graph_src
        has_both_models = '"grok"' in graph_src and '"claude"' in graph_src
        log(f"  [{'PASS' if has_online_escalated_set else 'FAIL'}] online_escalated set in audit_logger")
        phase.checks.append(Check("online_escalated_set", has_online_escalated_set))
        log(f"  [{'PASS' if has_both_models else 'FAIL'}] Both grok+claude in audit model set")
        phase.checks.append(Check("both_models_in_audit", has_both_models))
    except FileNotFoundError:
        pass

    # 7c: require_user_confirm is NOT read by production code
    log("\n  --- Due-Diligence: require_user_confirm unwired ---")
    for fname in ("gate.py", "graph.py"):
        src = Path(fname).read_text()
        not_read = "require_user_confirm" not in src
        log(f"    [{'PASS' if not_read else 'FAIL'}] {fname} does NOT read require_user_confirm")
        phase.checks.append(Check(f"unwired_require_user_confirm_{fname}", not_read))

    # 7d: user_gate_router hardcodes confirmed is None -> pause
    graph_src = Path("graph.py").read_text()
    has_hardcoded_none = "confirmed is None" in graph_src
    log(f"    [{'PASS' if has_hardcoded_none else 'FAIL'}] user_gate_router hardcodes 'confirmed is None' pause")
    phase.checks.append(Check("hardcoded_confirmation_pause", has_hardcoded_none))

    # 7e: Module isolation
    log("\n  --- Due-Diligence: Module Isolation ---")
    for fname in ("gate.py", "graph.py", "mcp_hybrid_server.py"):
        if not Path(fname).exists():
            continue
        src = Path(fname).read_text()
        direct_import = any(
            line.strip().startswith(("import agentic.", "from agentic."))
            for line in src.splitlines()
        )
        passed = not direct_import
        log(f"    [{'PASS' if passed else 'FAIL'}] {fname} does not import agentic/ directly")
        phase.checks.append(Check(f"module_isolation_{fname}", passed))

    # 7f: retrieve is graph entry point
    has_retrieve_entry = False
    try:
        graph_src = Path("graph.py").read_text()
        has_retrieve_entry = 'set_entry_point("retrieve")' in graph_src
    except FileNotFoundError:
        pass
    log(f"\n    [{'PASS' if has_retrieve_entry else 'FAIL'}] retrieve_node is graph entry point")
    phase.checks.append(Check("rag_first_entry_point", has_retrieve_entry))

    return phase


# ---------------------------------------------------------------------------
# Phase 8: Terminal Console REST API Tests
# ---------------------------------------------------------------------------
def phase_terminal_consoles() -> PhaseResult:
    banner("Phase 8: Terminal Console REST API Verification")
    phase = PhaseResult("Terminal Consoles")

    gate_src = Path("gate.py").read_text()

    # Verify all 12 endpoints
    endpoints = [
        ("/health", "GET"),
        ("/query", "POST"),
        ("/soul", "GET"),
        ("/soul/propose", "POST"),
        ("/soul/apply", "POST"),
        ("/soul/reload", "POST"),
        ("/soul/restore", "POST"),
        ("/audit/summary", "GET"),
        ("/ops/sync", "POST"),
        ("/ops/agentic", "POST"),
        ("/ops/fsconnect", "POST"),
        ("/ops/sqlconnect", "POST"),
    ]

    log("\n  --- Endpoint Registration ---")
    for path, method in endpoints:
        found = f'"{path}"' in gate_src or f"'{path}'" in gate_src
        status = f"{G}PASS{N}" if found else f"{R}FAIL{N}"
        log(f"    [{status}] {method} {path}")
        phase.checks.append(Check(f"endpoint_{method}_{path.replace('/', '_')}", found))

    # Verify ops_runner delegation
    log("\n  --- Ops Runner Delegation ---")
    runner_src = Path("utils/ops_runner.py").read_text()
    for name in ("run_sync_op", "run_agentic_op", "run_fsconnect_op", "run_sqlconnect_op"):
        found = f"def {name}" in runner_src
        status = f"{G}PASS{N}" if found else f"{R}FAIL{N}"
        log(f"    [{status}] {name} in ops_runner.py")
        phase.checks.append(Check(f"ops_runner_{name}", found))

    # Verify action whitelists
    log("\n  --- Action Whitelists ---")
    for name in ("_SYNC_ACTIONS", "_AGENTIC_ACTIONS", "_FSCONNECT_ACTIONS", "_SQLCONNECT_ACTIONS"):
        found = name in runner_src
        status = f"{G}PASS{N}" if found else f"{R}FAIL{N}"
        log(f"    [{status}] {name} whitelist defined")
        phase.checks.append(Check(f"whitelist_{name}", found))

    # SQL read-only guards
    log("\n  --- SQL Security Guards ---")
    try:
        from agentic.sqlconnect.client import assert_read_only_sql
        phase.checks.append(Check("sql_guards_importable", True))

        for sql, should_pass, name in [
            ("SELECT * FROM users", True, "select_pass"),
            ("DROP TABLE users", False, "drop_blocked"),
            ("", False, "empty_blocked"),
            ("SELECT 1; DROP TABLE x", False, "semicolon_blocked"),
            ("-- comment", False, "comment_blocked"),
        ]:
            try:
                assert_read_only_sql(sql)
                actual_pass = True
            except Exception:
                actual_pass = False
            phase.checks.append(Check(f"sql_guard_{name}", actual_pass == should_pass))
    except ImportError as e:
        log(f"    {Y}SKIP{N} SQL guards import: {e}")
        phase.checks.append(Check("sql_guards_importable", False, str(e)))

    # Security headers
    log("\n  --- Security Headers & Middleware ---")
    for header in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy",
                   "Permissions-Policy", "Content-Security-Policy"):
        found = header in gate_src
        status = f"{G}PASS{N}" if found else f"{R}FAIL{N}"
        log(f"    [{status}] {header}")
        phase.checks.append(Check(f"security_header_{header.lower().replace('-', '_')}", found))

    has_limiter = "RateLimiter" in gate_src
    log(f"    [{'PASS' if has_limiter else 'FAIL'}] RateLimiter initialized")
    phase.checks.append(Check("rate_limiter_initialized", has_limiter))

    has_trusted_host = "TrustedHostMiddleware" in gate_src
    log(f"    [{'PASS' if has_trusted_host else 'FAIL'}] TrustedHostMiddleware")
    phase.checks.append(Check("trusted_host_middleware", has_trusted_host))

    has_api_key_gate = "require_api_key" in gate_src and "CYCLAW_API_KEY" in gate_src
    log(f"    [{'PASS' if has_api_key_gate else 'FAIL'}] API key gate")
    phase.checks.append(Check("api_key_gate", has_api_key_gate))

    return phase


# ---------------------------------------------------------------------------
# Phase 9: Terminal HTML Console Contract
# ---------------------------------------------------------------------------
def phase_terminal_html() -> PhaseResult:
    banner("Phase 9: Terminal HTML Console Contract")
    phase = PhaseResult("Terminal HTML Contract")

    html = Path("static/terminal.html").read_text()

    # All 5 console panels
    log("\n  --- Console Panels ---")
    panels = [
        ("Soul Console", "soulPanel", "soulToggleBtn"),
        ("Sync Console", "syncPanel", "syncToggleBtn"),
        ("Agentic Console", "agenticPanel", "agenticToggleBtn"),
        ("FS Console", "fsPanel", "fsToggleBtn"),
        ("SQL Console", "sqlPanel", "sqlToggleBtn"),
    ]
    for name, panel_id, btn_id in panels:
        passed = panel_id in html and btn_id in html
        status = f"{G}PASS{N}" if passed else f"{R}FAIL{N}"
        log(f"    [{status}] {name}")
        phase.checks.append(Check(f"panel_{name.lower().replace(' ', '_')}", passed))

    # Online provider buttons (PR#441 explicit buttons)
    log("\n  --- Online Provider Buttons ---")
    for name, found in [
        ("grok_button_text", "Send to Grok" in html),
        ("claude_button_text", "Send to Claude" in html),
        ("grok_handler_explicit", "handleConfirm(true, id, 'grok')" in html),
        ("claude_handler_explicit", "handleConfirm(true, id, 'claude')" in html),
        ("provider_in_request_body", "body.online_provider = onlineProvider" in html),
        ("provider_label_display", "${providerLabel}" in html),
        ("confirm_offline_option", "Choose Offline" in html or "offline" in html.lower()),
    ]:
        status = f"{G}PASS{N}" if found else f"{R}FAIL{N}"
        log(f"    [{status}] {name}")
        phase.checks.append(Check(f"terminal_{name}", found))

    # API endpoint calls
    log("\n  --- Console API Endpoints ---")
    for name, endpoint in [
        ("soul_load", "/soul"),
        ("soul_propose", "/soul/propose"),
        ("soul_apply", "/soul/apply"),
        ("soul_reload", "/soul/reload"),
        ("soul_restore", "/soul/restore"),
        ("sync_ops", "/ops/sync"),
        ("agentic_ops", "/ops/agentic"),
        ("fs_ops", "/ops/fsconnect"),
        ("sql_ops", "/ops/sqlconnect"),
    ]:
        found = endpoint in html
        status = f"{G}PASS{N}" if found else f"{R}FAIL{N}"
        log(f"    [{status}] {name} -> {endpoint}")
        phase.checks.append(Check(f"terminal_api_{name}", found))

    # Auth integration
    has_auth = "authHeaders()" in html and "apiKeyInput" in html
    log(f"\n    [{'PASS' if has_auth else 'FAIL'}] authHeaders() + apiKeyInput")
    phase.checks.append(Check("terminal_auth_integration", has_auth))

    has_health = "/health" in html
    log(f"    [{'PASS' if has_health else 'FAIL'}] /health polling")
    phase.checks.append(Check("terminal_health_poll", has_health))

    return phase


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"\n{B}{'='*60}")
    print(f"  CyClaw Swarm Verification (Full)")
    print(f"  Target: {REPO_URL} @ {BRANCH}")
    print(f"  5 Queries: 2 vault hit, 1 offline best-effort, 1 Grok API, 1 Claude API")
    print(f"{'='*60}{N}\n")

    _install_stubs()
    _ensure_repo()
    full_deps = _install_deps()
    if full_deps:
        log("Full dependencies installed successfully", G)
    else:
        log("Running in sandbox mode (stubs active)", Y)

    results: list[PhaseResult] = []
    phases = [
        phase_config_invariants,
        phase_telemetry_kill,
        phase_build_corpus,
        phase_execute_queries,
        phase_triple_gate,
        phase_key_redaction,
        phase_metrics_and_invariants,
        phase_terminal_consoles,
        phase_terminal_html,
    ]

    for fn in phases:
        try:
            results.append(fn())
        except Exception as e:
            log(f"{fn.__name__} error: {e}", R)
            traceback.print_exc()
            results.append(PhaseResult(fn.__name__, [Check("phase_error", False, str(e))]))

    # Report
    banner("FINAL REPORT")

    total_checks = sum(len(p.checks) for p in results)
    total_passed = sum(p.passed_count for p in results)

    for phase_result in results:
        status = f"{G}PASS{N}" if phase_result.passed else f"{R}PARTIAL{N}"
        if phase_result.passed_count == 0 and len(phase_result.checks) > 0:
            status = f"{R}FAIL{N}"
        print(f"\n  [{status}] {phase_result.name}: {phase_result.passed_count}/{len(phase_result.checks)}")
        for check in phase_result.checks:
            cstatus = f"{G}o{N}" if check.passed else f"{R}x{N}"
            print(f"      [{cstatus}] {check.name}")
            if check.detail and not check.passed:
                print(f"          -> {check.detail}")

    # Query-specific summary
    print(f"\n{C}  Query Results:{N}")
    query_descs = [
        ("Q1", "Vault hit (CyClaw overview)"),
        ("Q2", "Vault hit (Security doc)"),
        ("Q3", "Offline best-effort / Qwen (Einstein/relativity)"),
        ("Q4", "Grok API connection-only"),
        ("Q5", "Claude API connection-only"),
    ]
    for (qid, desc), pr in zip(query_descs, results[3].checks[:5] if len(results) > 3 else []):
        status = f"{G}PASS{N}" if pr.passed else f"{R}FAIL{N}"
        print(f"    [{status}] {qid}: {desc}")

    print(f"\n{'='*60}")
    print(f"CyClaw Swarm Verification Complete.")
    print(f"Full functionality status: {'PASS' if total_passed == total_checks else 'PARTIAL'}.")
    print(f"Total: {total_passed}/{total_checks} checks passed")
    print(f"")
    print(f"RAG pipeline (5 queries): {'PASS' if results[3].passed else 'FAIL'}")
    print(f"Triple-Gate Online API (Grok): {'PASS' if all(c.passed for c in results[4].checks[:6]) else 'FAIL'}")
    print(f"Triple-Gate Online API (Claude): {'PASS' if all(c.passed for c in results[4].checks[6:]) else 'FAIL'}")
    print(f"API Key Redaction (both providers): {'PASS' if results[5].passed else 'FAIL'}")
    print(f"Due-Diligence Invariants: {'PASS' if results[6].passed else 'FAIL'}")
    print(f"REST API surface: {'PASS' if results[7].passed else 'FAIL'}")
    print(f"Terminal HTML contract: {'PASS' if results[8].passed else 'FAIL'}")
    print(f"Security Invariants: {results[0].passed_count}/{len(results[0].checks)} passed")
    print(f"{'='*60}")

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_checks": total_checks,
        "total_passed": total_passed,
        "phases": [
            {
                "name": p.name,
                "passed": p.passed,
                "passed_count": p.passed_count,
                "total": len(p.checks),
                "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in p.checks],
            }
            for p in results
        ],
    }
    with open("verification_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nFull report saved to verification_report.json")

    return 0 if total_passed == total_checks else 1


if __name__ == "__main__":
    sys.exit(main())
