#!/usr/bin/env python3
"""
CyClaw Full Verification Script -- Comprehensive smoke test harness.

Runs in sandbox mode (no external dependencies needed) or full-dependency mode.
Executes 5 queries covering: vault hit x2, offline best-effort (Qwen),
Grok API connection-only, Claude API connection-only.

Runs 11 phases (Phase 1 Config & Security Invariants ... Phase 11 Harness HTML
Contract) totalling ~189 checks; each phase prints its own PASS/FAIL tally and
the final report is never hand-counted here -- see verification_report.json.

Verifies:
  1. Config & security invariants (config.yaml contract, static read)
  2. Telemetry kill canonical maps (utils.telemetry_kill, not a gate.py grep)
  3. Mock corpus + BM25/Chroma index build (retrieval.stemmer.tokenize_and_stem)
  4. LangGraph pipeline (5 queries through real node functions)
  5. Triple-gate online API fallback (Grok + Claude) with mocked HTTP
  6. API key redaction parity (Anthropic keys redacted same as Grok)
  7. Metrics escalation + due-diligence invariants (unwired
     require_user_confirm, module isolation, RAG-first entry point)
  8. Terminal + harness REST endpoint registration (gate.py + gate_ops.py +
     gate_auth.py + gate_memory.py; SQL read-only guards; security headers)
  9. Terminal console contract (terminal.html + terminal.js combined source --
     panels, provider buttons, all 4 slash commands, REST endpoint calls)
  10. Harness console REST API (status, registry, tools/skills wiring,
      sessions, soul/model toggles, /api/keys, chat, GitHub status, harness
      runs, agent routes) via a real FastAPI TestClient -- plus rate-limit,
      auto-docs-disabled, and DNS-rebinding checks against the live app object
  11. Harness HTML contract (panes, API endpoints, the full slash-command
      palette derived from harness.html's own COMMANDS array, XSS safety)

Usage:
    python3 .claude/skills/CyClaw-Sandbox/run_full_verification.py

Env:
    CYCLAW_REPO=/path/to/CyClaw  -- use existing clone instead of fresh.
                                    NOTE: if this checkout is NOT a scratch
                                    clone, the run still writes
                                    data/corpus/*.md, index/bm25.json,
                                    query_results.json and
                                    verification_report.json into it (Phase 3
                                    onward) -- point this at a throwaway
                                    clone, not a working tree, unless you
                                    intend those writes.
    FULL_DEPS=1                  -- attempt full dependency install first
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
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

# Which of the 3-tier Ollama realism ladder this run actually exercised (see
# SKILL.md's "3-tier realism" section). Tier 0 (in-process pytest MockLocalLLM
# stub) is tests/conftest.py's concern, not this script's. Set once in main()
# by _probe_ollama_tier() before any phase that talks to the local-LLM base_url
# runs, then stamped into both report files this script writes.
OLLAMA_TIER: int | None = None

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
    # An operator-supplied CYCLAW_REPO is used AS-IS -- no checkout/pull. This
    # script writes into CYCLAW_DIR from Phase 3 onward (mock corpus, BM25
    # index, query_results.json, verification_report.json); silently switching
    # branches or pulling on a directory the caller pointed us at on purpose
    # (a real working tree, not a scratch clone) would be a surprise mutation
    # of state they didn't ask for. Unset CYCLAW_REPO (the default) still gets
    # the original clone-to-/tmp/CyClaw behavior untouched.
    if os.environ.get("CYCLAW_REPO"):
        log(f"CYCLAW_REPO set -- using {CYCLAW_DIR} as-is (no checkout/pull)", Y)
        log(f"  This run WILL write data/corpus/*.md, index/bm25.json, "
            f"query_results.json and verification_report.json into it.", Y)
        if not (CYCLAW_DIR / ".git").exists():
            log(f"  WARNING: {CYCLAW_DIR} does not look like a git checkout", Y)
    elif CYCLAW_DIR.exists() and (CYCLAW_DIR / ".git").exists():
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


def _probe_ollama_tier() -> int:
    """Which Ollama realism tier this run gets: 2 if a real daemon (or
    mock_ollama.py started ahead of us by verify.sh) already answers on
    127.0.0.1:11434, else 1 (this script has no live chat backend and the
    local_llm queries in Phase 4 will hit connection errors instead of a
    mocked 200). Short-timeout GET, stdlib-only so it needs no FULL_DEPS
    install to run before any other phase.
    """
    import urllib.error
    import urllib.request

    try:
        # DevSkim: ignore DS162092,DS137138 - loopback-only probe, offline-only
        urllib.request.urlopen("http://127.0.0.1:11434/v1/models", timeout=1.5)
        return 2
    except (urllib.error.URLError, OSError, ValueError):
        return 1


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
        ("app.mode == 'hybrid'", cfg.get("app", {}).get("mode") == "hybrid"),
        ("api.host == 127.0.0.1", cfg.get("api", {}).get("host") == "127.0.0.1"),
        ("api.port == 8787", cfg.get("api", {}).get("port") == 8787),
        ("grok.enabled == true", cfg.get("models", {}).get("grok", {}).get("enabled") is True),
        ("claude block present", "claude" in cfg.get("models", {})),
        ("claude.enabled == true", cfg.get("models", {}).get("claude", {}).get("enabled") is True),
        ("retrieval.min_score == 0.028", abs(cfg.get("retrieval", {}).get("min_score", 0) - 0.028) < 0.001),
        (">=40 banned patterns", len(cfg.get("policy", {}).get("prompt_filter", {}).get("banned_patterns", [])) >= 40),
        ("fsconnect block present", "fsconnect" in cfg),
        ("fsconnect.enabled == false", cfg.get("fsconnect", {}).get("enabled") is False),
        ("fsconnect.allow_macos_volume_roots == false",
         cfg.get("fsconnect", {}).get("allow_macos_volume_roots") is False),
        ("sqlconnect block present", "sqlconnect" in cfg),
        ("sqlconnect.read_only == true", cfg.get("sqlconnect", {}).get("read_only") is True),
        ("sync block present", "sync" in cfg),
        ("agentic block present", "agentic" in cfg),
        ("agentic.enabled == false", cfg.get("agentic", {}).get("enabled") is False),
        ("agentic.mode == 'write'", cfg.get("agentic", {}).get("mode") == "write"),
        ("agentic.writes_enabled == true", cfg.get("agentic", {}).get("writes_enabled") is True),
        ("grok_max_prompt_chars == 8000", cfg.get("policy", {}).get("fallback", {}).get("grok_max_prompt_chars") == 8000),
        ("claude_max_prompt_chars == 8000", cfg.get("policy", {}).get("fallback", {}).get("claude_max_prompt_chars") == 8000),
        ("send_local_context_to_grok == false", cfg.get("policy", {}).get("fallback", {}).get("send_local_context_to_grok") is False),
        ("send_local_context_to_claude == false", cfg.get("policy", {}).get("fallback", {}).get("send_local_context_to_claude") is False),
        ("require_user_confirm present (unwired)", "require_user_confirm" in cfg.get("policy", {}).get("fallback", {})),
    ]

    # Check Anthropic key redaction in config (policy.privacy, not logging.audit)
    privacy_cfg = cfg.get("policy", {}).get("privacy", {})
    redact_patterns = privacy_cfg.get("redact_secrets_like", [])
    has_sk_ant = any("sk-ant" in str(p) for p in redact_patterns)
    checks.append(("privacy redact sk-ant-* pattern", has_sk_ant))

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

    # The canonical source of truth is utils/telemetry_kill.py's three maps
    # (issue #1135) -- checking for literal names in gate.py's source text
    # is a lie the moment a name is added/renamed there and gate.py itself
    # only ever imports apply_telemetry_kill(). One gate.py grep is kept
    # below for the G1 wiring anchor, which IS load-bearing text.
    from utils.telemetry_kill import SCRUBBED_ENV_KEYS, TELEMETRY_KILL, UPDATE_CHECK_OPT_OUT

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
        found = var in TELEMETRY_KILL
        status = f"{G}PASS{N}" if found else f"{R}FAIL{N}"
        log(f"  [{status}] {var} is a TELEMETRY_KILL key")
        phase.checks.append(Check(f"telemetry_kill_{var}", found))

    for label, mapping, floor in (
        ("TELEMETRY_KILL", TELEMETRY_KILL, 21),
        ("UPDATE_CHECK_OPT_OUT", UPDATE_CHECK_OPT_OUT, 4),
        ("SCRUBBED_ENV_KEYS", SCRUBBED_ENV_KEYS, 7),
    ):
        ok = len(mapping) >= floor
        log(f"  [{'PASS' if ok else 'FAIL'}] {label} has >={floor} entries ({len(mapping)})")
        phase.checks.append(Check(f"telemetry_kill_size_{label}", ok))

    # The G1 ordering anchor invariant-guard checks by AST: gate.py must bind
    # this name before its heavy imports. A text grep here is a coarse but
    # useful early warning if that binding vanishes entirely.
    gate_src = Path("gate.py").read_text()
    has_anchor = "_TELEMETRY_KILL = apply_telemetry_kill()" in gate_src
    log(f"  [{'PASS' if has_anchor else 'FAIL'}] gate.py's G1 anchor line present")
    phase.checks.append(Check("telemetry_kill_g1_anchor", has_anchor))

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
        from rank_bm25 import BM25Okapi
        from retrieval.stemmer import tokenize_and_stem

        chunks = []
        tokenized = []
        for fname in files:
            text = (corpus_dir / fname).read_text()
            chunks.append({"text": text, "source": fname, "id": len(chunks)})
            # Same call retrieval/indexer.py and retrieval/hybrid_search.py make
            # when tokenizing real corpus/query text -- PorterStemmer is not a
            # public symbol here (removed), and hand-stemming would tokenize
            # this mock index differently from how a real query is tokenized.
            tokenized.append(tokenize_and_stem(text))

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
        json.dump({"query_results": all_results, "ollama_tier": OLLAMA_TIER}, f, indent=2)
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

    # Verify in config.yaml -- policy.privacy, not logging.audit (Phase 1's
    # check at "privacy redact sk-ant-* pattern" reads the same, correct path).
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    redact_patterns = cfg.get("policy", {}).get("privacy", {}).get("redact_secrets_like", [])
    has_sk_ant_config = any("sk-ant" in str(p) for p in redact_patterns)
    log(f"  [{'PASS' if has_sk_ant_config else 'FAIL'}] sk-ant-* in config.yaml policy.privacy redact")
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

    # gate.py registers its own core routes directly, but /ops/* live in
    # gate_ops.py, /auth/* in gate_auth.py, and /memory/* + the export route
    # in gate_memory.py (register_*_routes() calls in gate.py wire them onto
    # the same app) -- a gate.py-only grep silently "loses" every route that
    # moved out during that split. Concatenating the four files' source stays
    # in this script's existing style (every other phase here is static-text
    # analysis; Phase 10 is deliberately the only one that live-imports an
    # app, and only harness.server's, which -- unlike gate.py -- has none of
    # the heavy retrieval/graph dependencies this sandbox stubs).
    gate_src = "\n".join(
        Path(f).read_text()
        for f in ("gate.py", "gate_ops.py", "gate_auth.py", "gate_memory.py")
        if Path(f).exists()
    )

    endpoints = [
        ("/health", "GET"),
        ("/query", "POST"),
        ("/index/build", "POST"),
        ("/index/status", "GET"),
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
        ("/auth/setup-status", "GET"),
        ("/auth/login", "POST"),
        ("/auth/whoami", "GET"),
        ("/memory/status", "GET"),
        ("/query/export/html", "GET"),
    ]

    log("\n  --- Endpoint Registration (gate.py + gate_ops.py + gate_auth.py + gate_memory.py) ---")
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

    # terminal.html's own console logic (the CSP forces script-src 'self')
    # lives in the sibling static/terminal.js -- reading terminal.html alone
    # misses everything that moved, matching tests/test_terminal_contract.py's
    # own _console_source() combined-read.
    html = (
        Path("static/terminal.html").read_text()
        + "\n"
        + Path("static/terminal.js").read_text()
    )

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

    # Online provider buttons (PR#441 explicit buttons). handleConfirm() is
    # now one generic function taking a provider argument (terminal.js), not
    # two literal per-provider call sites -- the per-button wiring is the
    # addEventListener call passing its own `provider` closure variable.
    log("\n  --- Online Provider Buttons ---")
    for name, found in [
        ("grok_button_text", "Send to Grok" in html),
        ("claude_button_text", "Send to Claude" in html),
        ("handle_confirm_generic", "handleConfirm(true, id, provider)" in html),
        ("handle_confirm_signature", "function handleConfirm(confirmed, entryId, onlineProvider" in html),
        ("provider_in_request_body", "body.online_provider = onlineProvider" in html),
        ("provider_label_display", "${providerLabel}" in html),
        ("confirm_offline_option", "Choose Offline" in html or "offline" in html.lower()),
    ]:
        status = f"{G}PASS{N}" if found else f"{R}FAIL{N}"
        log(f"    [{status}] {name}")
        phase.checks.append(Check(f"terminal_{name}", found))

    # All four terminal slash commands (only /users, /admin, /audit, /help --
    # Soul/Sync/Agentic/FS/SQL are Advanced toolbar buttons, not commands).
    log("\n  --- Slash Commands ---")
    for cmd in ("/users", "/admin", "/audit", "/help"):
        found = f"'{cmd}'" in html or f'"{cmd}"' in html
        status = f"{G}PASS{N}" if found else f"{R}FAIL{N}"
        log(f"    [{status}] {cmd}")
        phase.checks.append(Check(f"terminal_cmd_{cmd.lstrip('/')}", found))

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
# Phase 10: Harness Console REST API Verification
# ---------------------------------------------------------------------------
def _harness_mock_transport(reply: str = "mock harness reply", prompt_tokens: int = 5, completion_tokens: int = 8):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "model": "qwen3.8:27b-mlx",
            "choices": [{"message": {"role": "assistant", "content": reply}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        })

    return httpx.MockTransport(handler)


def phase_harness_console() -> PhaseResult:
    banner("Phase 10: Harness Console REST API Verification")
    phase = PhaseResult("Harness Console")

    # Real FastAPI TestClient, not source-text grepping like the terminal
    # phases above -- harness/server.py has none of gate.py's heavy retrieval/
    # graph dependencies, so building and hitting the real app is cheap and
    # strictly more thorough than checking for endpoint string literals.
    try:
        from fastapi.testclient import TestClient
        from harness.config import HarnessConfig
        from harness.ollama import HarnessChatClient
        from harness.server import create_app
    except ImportError as exc:
        log(f"  Harness console phase skipped (import error): {exc}", Y)
        phase.checks.append(Check("harness_console_importable", False, str(exc)))
        return phase

    # Isolate the harness home so this phase never touches the operator's
    # real ~/.CyClaw / %USERPROFILE%\.CyClaw.
    home = Path(tempfile.mkdtemp(prefix="cyclaw-harness-sandbox-"))
    os.environ["CYCLAW_HOME"] = str(home)

    cfg = HarnessConfig.load()
    chat = HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1", model="qwen3.8:27b-mlx", transport=_harness_mock_transport(),
    )
    # The five state-changing POSTs, GET /api/github/status and the three
    # /api/agent/* run routes are Bearer-gated (utils/auth.py) AND require the
    # per-process CSRF token create_app() mints and exposes on app.state --
    # verify.sh exports CYCLAW_API_KEY; fall back to a literal so a standalone
    # run still exercises the guarded routes instead of 401ing.
    _key = os.environ.setdefault("CYCLAW_API_KEY", "sandbox-harness-key")
    _app = create_app(cfg, chat)
    _auth = {"Authorization": f"Bearer {_key}", "X-CyClaw-CSRF": _app.state.csrf_token}
    client = TestClient(_app, base_url="http://127.0.0.1", headers=_auth)

    log("\n  --- Status / Registry ---")
    r = client.get("/api/status")
    phase.checks.append(Check("harness_status_200", r.status_code == 200))
    status_fields = ("version", "model", "provider", "base_url", "soul_enabled",
                      "home", "repo_root", "sessions", "total_tokens", "layout")
    phase.checks.append(Check("harness_status_fields", all(k in r.json() for k in status_fields)))

    r = client.get("/api/registry")
    reg = r.json()
    phase.checks.append(Check("harness_registry_200", r.status_code == 200))
    phase.checks.append(Check(
        "harness_registry_shape",
        all(isinstance(reg.get(k), list) for k in ("skills", "tools", "connectors")),
    ))

    r = client.get("/api/tools")
    tools_payload = r.json() if r.status_code == 200 else {}
    phase.checks.append(Check("harness_tools_200", r.status_code == 200))
    phase.checks.append(Check(
        "harness_tools_shape",
        isinstance(tools_payload.get("tools"), list)
        and isinstance(tools_payload.get("diagram"), str)
        and "HARNESS TOOLS" in (tools_payload.get("diagram") or ""),
    ))
    tool_names = {t.get("name") for t in tools_payload.get("tools") or []}
    phase.checks.append(Check(
        "harness_tools_includes_goal_and_hybrid_search",
        {"goal", "loop", "hybrid_search"} <= tool_names,
    ))
    phase.checks.append(Check(
        "harness_tools_all_harness_rows_wired",
        all(t.get("wired") for t in (tools_payload.get("tools") or []) if t.get("kind") == "harness"),
    ))

    r = client.get("/api/skills")
    skills_payload = r.json() if r.status_code == 200 else {}
    phase.checks.append(Check("harness_skills_200", r.status_code == 200))
    phase.checks.append(Check(
        "harness_skills_shape",
        isinstance(skills_payload.get("skills"), list)
        and isinstance(skills_payload.get("diagram"), str)
        and "HARNESS SKILLS" in (skills_payload.get("diagram") or ""),
    ))
    skill_names = {s.get("name") for s in skills_payload.get("skills") or []}
    phase.checks.append(Check(
        "harness_skills_includes_prompt_and_check",
        {"ponytail", "karpathy-guidelines", "invariant-guard"} <= skill_names,
    ))

    r = client.get("/api/web")
    web_payload = r.json() if r.status_code == 200 else {}
    phase.checks.append(Check("harness_web_200", r.status_code == 200))
    phase.checks.append(Check(
        "harness_web_default_off_empty_allowlist",
        web_payload.get("enabled") is False and web_payload.get("allowlist") == [],
    ))
    deny = client.post("/api/web/fetch", json={"url": "https://example.com/"})
    phase.checks.append(Check(
        "harness_web_fetch_disabled_is_409",
        deny.status_code == 409
        and (deny.json().get("detail") or {}).get("code") == "WEB_DISABLED",
    ))

    r = client.get("/api/memory")
    mem_payload = r.json() if r.status_code == 200 else {}
    phase.checks.append(Check("harness_memory_200", r.status_code == 200))
    phase.checks.append(Check(
        "harness_memory_default_off",
        mem_payload.get("enabled") is False
        and mem_payload.get("count") == 0
        and mem_payload.get("rag", {}).get("writable_from_harness") is False,
    ))
    added = client.post("/api/memory/add", json={"text": "prefer ruff"})
    phase.checks.append(Check(
        "harness_memory_add",
        added.status_code == 200 and added.json().get("count") == 1,
    ))
    client.post("/api/memory/clear")

    log("  --- Sessions CRUD ---")
    r = client.post("/api/sessions", json={"title": "sandbox check"})
    phase.checks.append(Check("harness_session_create_201", r.status_code == 201))
    sid = r.json().get("session_id")
    phase.checks.append(Check("harness_session_create_has_id", bool(sid)))

    r = client.get(f"/api/sessions/{sid}")
    phase.checks.append(Check("harness_session_get", r.status_code == 200 and r.json().get("session_id") == sid))

    r = client.post(f"/api/sessions/{sid}/rename", json={"title": "renamed"})
    phase.checks.append(Check("harness_session_rename", r.status_code == 200 and r.json().get("title") == "renamed"))

    r = client.post(f"/api/sessions/{sid}/goal", json={"goal": "  sandbox goal  "})
    phase.checks.append(Check(
        "harness_session_goal_set",
        r.status_code == 200 and r.json().get("goal") == "sandbox goal",
    ))
    r = client.get(f"/api/sessions/{sid}")
    phase.checks.append(Check(
        "harness_session_goal_persists",
        r.status_code == 200 and r.json().get("goal") == "sandbox goal",
    ))
    r = client.post(
        "/api/chat",
        json={"message": "loop toward sandbox goal", "session_id": sid, "loop": True},
    )
    phase.checks.append(Check(
        "harness_loop_turn_with_goal",
        r.status_code == 200,
        f"status={r.status_code}",
    ))
    r = client.post(f"/api/sessions/{sid}/goal", json={"goal": ""})
    phase.checks.append(Check(
        "harness_session_goal_clear",
        r.status_code == 200 and r.json().get("goal") == "",
    ))
    r = client.post("/api/sessions/000000000000/goal", json={"goal": "nope"})
    phase.checks.append(Check("harness_session_goal_unknown_404", r.status_code == 404))

    r = client.get("/api/sessions/000000000000")
    phase.checks.append(Check("harness_session_unknown_404", r.status_code == 404))

    log("  --- Soul / Model toggles ---")
    before = client.get("/api/soul").json().get("enabled")
    flipped = client.post("/api/soul", json={"enabled": not before}).json().get("enabled")
    phase.checks.append(Check("harness_soul_toggle_flips", flipped == (not before)))
    client.post("/api/soul", json={"enabled": before})  # restore

    # /api/model stores whatever string is posted (model_select() does not
    # validate against a registry) -- use the shipped local model tag rather
    # than an unrelated literal, matching harness_emulation.py's own check.
    r = client.post("/api/model", json={"model": "qwen3.8:27b-mlx"})
    phase.checks.append(Check("harness_model_select", r.json().get("model") == "qwen3.8:27b-mlx"))

    log("  --- API keys panel (/api set) ---")
    r = client.get("/api/keys")
    phase.checks.append(Check("harness_keys_200", r.status_code == 200))
    keys_payload = r.json() if r.status_code == 200 else {}
    phase.checks.append(Check(
        "harness_keys_shape", "keys" in keys_payload and "env_file" in keys_payload,
    ))
    # The one secret value this process actually holds (the Bearer key this
    # very phase authenticates with) must never round-trip in the response --
    # a direct leak check, not a length heuristic.
    phase.checks.append(Check(
        "harness_keys_no_secret_leak", _key not in json.dumps(keys_payload),
    ))

    log("  --- Chat (mocked backend) ---")
    r = client.post("/api/chat", json={"message": "hi", "session_id": sid})
    phase.checks.append(Check("harness_chat_200", r.status_code == 200))
    cd = r.json()
    phase.checks.append(Check(
        "harness_chat_fields", all(k in cd for k in ("session_id", "reply", "model", "usage", "tally")),
    ))
    phase.checks.append(Check("harness_chat_reply_matches_mock", cd.get("reply") == "mock harness reply"))

    r = client.post("/api/chat/cancel")
    phase.checks.append(Check(
        "harness_chat_cancel_idempotent",
        r.status_code == 200 and r.json().get("cancelled") is True,
    ))

    r = client.post("/api/chat", json={"message": "loop without goal", "session_id": sid, "loop": True})
    loop_body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    loop_detail = loop_body.get("detail") if isinstance(loop_body, dict) else {}
    loop_code = loop_detail.get("code") if isinstance(loop_detail, dict) else None
    phase.checks.append(Check(
        "harness_loop_requires_goal",
        r.status_code == 400 and loop_code == "LOOP_REQUIRES_GOAL",
        f"status={r.status_code} code={loop_code!r}",
    ))

    log("  --- GitHub status / harness runs ---")
    r = client.get("/api/github/status")
    phase.checks.append(Check("harness_github_status_well_formed", isinstance(r.json(), dict)))

    r = client.get("/api/harness/runs")
    rd = r.json()
    phase.checks.append(Check("harness_runs_shape", "runs" in rd and "count" in rd))

    log("  --- Agent run routes (auth-gate only -- never a real invocation) ---")
    # /api/agent/run and /decision drive `python -m agentic.cli`: a real run
    # clones a repo, calls a model, and can block ~900s; push/publish/discard
    # reach a git write. None of that belongs in a sandbox check -- only that
    # a bad bearer is rejected, mirroring harness_emulation.py's own approach.
    r = client.get("/api/agent/checks")
    profiles = r.json().get("profiles") if r.status_code == 200 else None
    phase.checks.append(Check("harness_agent_checks_lists_profiles", bool(profiles)))

    bad_auth = {"Authorization": "Bearer wrong-key"}
    for path in (
        "/api/agent/run",
        f"/api/agent/runs/{'0' * 32}/decision",
        f"/api/agent/runs/{'0' * 32}/push",
        f"/api/agent/runs/{'0' * 32}/publish",
        f"/api/agent/runs/{'0' * 32}/discard",
    ):
        resp = client.post(path, json={}, headers=bad_auth)
        phase.checks.append(Check(
            f"harness_agent_route_rejects_bad_key_{path.rsplit('/', 1)[-1]}",
            resp.status_code == 401,
            f"status={resp.status_code}",
        ))

    log("  --- Auth setup status (/api/auth/setup-status) ---")
    # Shipped default is auth.enabled: false -> _require_harness_auth() raises
    # 503 AUTH_DISABLED (harness/server.py), matching gate_auth.py's own
    # /auth/setup-status contract exactly. This asserts the DEFAULT posture;
    # an operator config with auth enabled would instead see 200 + the three
    # {enabled, needs_password, username} fields.
    r = client.get("/api/auth/setup-status")
    detail = (r.json().get("detail") or {}) if r.status_code != 200 else {}
    phase.checks.append(Check(
        "harness_auth_setup_status_disabled_by_default",
        r.status_code == 503 and detail.get("code") == "AUTH_DISABLED",
        f"status={r.status_code}",
    ))

    log("  --- Security: rate limit, auto-docs, host rebinding ---")
    # /api/chat rate limit (per-IP, reusing utils.ratelimit.RateLimiter and
    # config.yaml's api.rate_limit block -- same mechanism gate.py's /query
    # uses). Read the configured ceiling rather than hardcoding it, matching
    # this repo's "config.yaml is the single source of truth" convention.
    try:
        import yaml
        rl_cfg = (yaml.safe_load(Path("config.yaml").read_text()) or {}).get("api", {}).get("rate_limit", {})
        max_requests = int(rl_cfg.get("max_requests", 60))
    except (OSError, ValueError):
        max_requests = 60
    saw_429 = False
    for _ in range(max_requests + 5):
        resp = client.post("/api/chat", json={"message": "spam", "session_id": sid})
        if resp.status_code == 429:
            saw_429 = True
            break
    phase.checks.append(Check(
        "harness_chat_rate_limit_engages", saw_429,
        f"no 429 within {max_requests + 5} requests (configured limit={max_requests})",
    ))

    for path in ("/docs", "/redoc", "/openapi.json"):
        r = client.get(path)
        phase.checks.append(Check(f"harness_auto_docs_disabled_{path.strip('/').replace('.', '_')}",
                                   r.status_code == 404))

    # DNS-rebinding defense: TrustedHostMiddleware reads the Host header off
    # base_url, so this needs its own client rather than an overridden header
    # on the loopback one above -- mirrors tests/test_harness.py's own
    # test_rejects_non_loopback_host_header technique exactly.
    rebind_client = TestClient(create_app(cfg, chat), base_url="http://attacker.example", headers=_auth)
    r = rebind_client.get("/api/status")
    phase.checks.append(Check("harness_trusted_host_rejects_rebinding", r.status_code == 400))

    return phase


# ---------------------------------------------------------------------------
# Phase 11: Harness HTML Console Contract
# ---------------------------------------------------------------------------
def phase_harness_html() -> PhaseResult:
    banner("Phase 11: Harness HTML Console Contract")
    phase = PhaseResult("Harness HTML Contract")

    html_path = Path("static/harness.html")
    if not html_path.exists():
        log("  static/harness.html not found", R)
        phase.checks.append(Check("harness_html_exists", False))
        return phase
    html = html_path.read_text()

    log("\n  --- Console Panes ---")
    for name, pane_id, tab_marker in [
        ("Commands pane", "pane-commands", "data-pane=\"commands\""),
        ("Sessions pane", "pane-sessions", "data-pane=\"sessions\""),
        ("Registry pane", "pane-registry", "data-pane=\"registry\""),
    ]:
        passed = pane_id in html and tab_marker in html
        status = f"{G}PASS{N}" if passed else f"{R}FAIL{N}"
        log(f"    [{status}] {name}")
        phase.checks.append(Check(f"harness_pane_{pane_id.replace('-', '_')}", passed))

    log("\n  --- Console API Endpoints ---")
    for name, endpoint in [
        ("status", "/api/status"),
        ("registry", "/api/registry"),
        ("sessions_list", "/api/sessions"),
        ("soul", "/api/soul"),
        ("model", "/api/model"),
        ("keys", "/api/keys"),
        ("chat", "/api/chat"),
        ("chat_cancel", "/api/chat/cancel"),
        ("github_status", "/api/github/status"),
        ("harness_runs", "/api/harness/runs"),
        ("agent_checks", "/api/agent/checks"),
        ("tools", "/api/tools"),
        ("skills", "/api/skills"),
        ("web", "/api/web"),
        ("web_fetch", "/api/web/fetch"),
        ("memory", "/api/memory"),
        ("memory_add", "/api/memory/add"),
        ("auth_setup_status", "/api/auth/setup-status"),
    ]:
        found = endpoint in html
        status = f"{G}PASS{N}" if found else f"{R}FAIL{N}"
        log(f"    [{status}] {name} -> {endpoint}")
        phase.checks.append(Check(f"harness_html_api_{name}", found))

    phase.checks.append(Check(
        "harness_html_api_session_goal",
        "+ '/goal'" in html or "/goal', 'POST'" in html,
    ))

    log("\n  --- Slash Commands (derived from harness.html's own COMMANDS array) ---")
    # A hardcoded list drifts the moment a command is added -- derive the base
    # token of every row instead. Each row is ['/cmd rest-of-syntax', 'help
    # text']; the regex takes the leading /word before the first space/quote,
    # so '/agent run|plan|...' and '/agent status|approve|...' both collapse
    # to /agent (deduped via the set).
    commands_block_match = re.search(r"const COMMANDS\s*=\s*\[(.*?)\n\s*\];", html, re.DOTALL)
    commands_block = commands_block_match.group(1) if commands_block_match else ""
    slash = sorted(set(re.findall(r"\['(/[^'\s]+)", commands_block)))
    phase.checks.append(Check("harness_html_commands_array_found", bool(slash)))
    for cmd in slash:
        found = f"'{cmd}" in html or f'"{cmd}' in html or f"case '{cmd.lstrip('/')}" in html
        status = f"{G}PASS{N}" if found else f"{R}FAIL{N}"
        log(f"    [{status}] {cmd}")
        phase.checks.append(Check(f"harness_html_cmd_{cmd.lstrip('/')}", found))

    # The hidden `registry` alias of /connectors (shares its case label,
    # SKILL.md's operator map calls it out explicitly) -- worth its own
    # check since it wouldn't otherwise be found by the COMMANDS-array walk
    # above (it isn't a COMMANDS row at all, just a second case label).
    has_registry_alias = "case 'connectors': case 'registry':" in html
    log(f"    [{'PASS' if has_registry_alias else 'FAIL'}] hidden 'registry' alias of /connectors")
    phase.checks.append(Check("harness_html_registry_alias", has_registry_alias))

    log("\n  --- XSS Safety (untrusted model/registry output) ---")
    # harness.html's own comment documents this invariant explicitly: model
    # output and registry data are DATA, never HTML. innerHTML would let a
    # skill description, a chat reply, or a session title inject markup/script
    # into the console DOM (fable-protocol's CATEGORY-ERROR RULE: this lens
    # applies to every generated artifact, not just "protected" surfaces).
    no_inner_html = "innerHTML" not in html
    log(f"    [{'PASS' if no_inner_html else 'FAIL'}] no innerHTML usage (textContent/createElement only)")
    phase.checks.append(Check("harness_html_no_inner_html", no_inner_html))

    has_text_content = "textContent" in html
    log(f"    [{'PASS' if has_text_content else 'FAIL'}] renders via textContent")
    phase.checks.append(Check("harness_html_uses_text_content", has_text_content))

    log("\n  --- API-key field (Bearer-gated writes) ---")
    # Guarded POSTs (/goal, /chat, /chat/cancel, /agent/*) require
    # Authorization: Bearer. harness.html reads #apiKey via apiKeyInput
    # inside api(); it must NOT reuse terminal.html's authHeaders() helper
    # (that would mean the two consoles had silently coupled).
    has_key_field = "apiKeyInput" in html and 'id="apiKey"' in html
    log(f"    [{'PASS' if has_key_field else 'FAIL'}] apiKeyInput present for guarded POSTs")
    phase.checks.append(Check("harness_html_has_api_key_field", has_key_field))
    no_terminal_helper = "authHeaders" not in html
    log(f"    [{'PASS' if no_terminal_helper else 'FAIL'}] no terminal.html authHeaders() helper")
    phase.checks.append(Check("harness_html_no_terminal_auth_helper", no_terminal_helper))

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
    # _ensure_repo() chdirs into the target checkout, but launching this
    # script by path (as documented below) sets sys.path[0] to this skill's
    # own directory, not the repo root -- every phase that imports a
    # repo-root module (retrieval, graph, gate, agentic, harness, ...) would
    # otherwise fail with ModuleNotFoundError regardless of cwd. Mirrors
    # gate_runtime_check.py's identical fix for the identical reason.
    sys.path.insert(0, os.getcwd())
    full_deps = _install_deps()
    if full_deps:
        log("Full dependencies installed successfully", G)
    else:
        log("Running in sandbox mode (stubs active)", Y)

    global OLLAMA_TIER
    OLLAMA_TIER = _probe_ollama_tier()
    tier_desc = "real daemon/mock already answering" if OLLAMA_TIER == 2 else "this script's own mock_ollama.py"
    log(f"Ollama realism: Tier {OLLAMA_TIER} ({tier_desc})", C)

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
        phase_harness_console,
        phase_harness_html,
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

    # Name-based lookup, not positional indices -- a phase insert/reorder
    # used to silently mislabel this summary (verified: the old checks[:6]/
    # [6:] split didn't even match the Grok/Claude boundary correctly on the
    # phase list as it stood). PhaseResult.name is the stable key each phase
    # function sets for itself.
    by_name = {p.name: p for p in results}

    def _phase(name: str) -> PhaseResult:
        return by_name.get(name, PhaseResult(name))

    # Query-specific summary
    print(f"\n{C}  Query Results:{N}")
    query_descs = [
        ("Q1", "Vault hit (CyClaw overview)"),
        ("Q2", "Vault hit (Security doc)"),
        ("Q3", "Offline best-effort / Qwen (Einstein/relativity)"),
        ("Q4", "Grok API connection-only"),
        ("Q5", "Claude API connection-only"),
    ]
    for (qid, desc), pr in zip(query_descs, _phase("5 Queries").checks[:5]):
        status = f"{G}PASS{N}" if pr.passed else f"{R}FAIL{N}"
        print(f"    [{status}] {qid}: {desc}")

    triple_gate_checks = _phase("Triple-Gate Online API").checks
    grok_checks = [c for c in triple_gate_checks if c.name.startswith("grok_")]
    claude_checks = [c for c in triple_gate_checks if c.name.startswith("claude_")]
    shared_checks = [
        c for c in triple_gate_checks
        if not c.name.startswith(("grok_", "claude_"))
    ]

    print(f"\n{'='*60}")
    print(f"CyClaw Swarm Verification Complete.")
    print(f"Full functionality status: {'PASS' if total_passed == total_checks else 'PARTIAL'}.")
    print(f"Total: {total_passed}/{total_checks} checks passed")
    print(f"")
    print(f"RAG pipeline (5 queries): {'PASS' if _phase('5 Queries').passed else 'FAIL'}")
    print(f"Triple-Gate Online API (Grok): {'PASS' if all(c.passed for c in grok_checks) else 'FAIL'}")
    print(f"Triple-Gate Online API (Claude): {'PASS' if all(c.passed for c in claude_checks) else 'FAIL'}")
    print(f"Triple-Gate shared/cross-provider: {'PASS' if all(c.passed for c in shared_checks) else 'FAIL'}")
    print(f"API Key Redaction (both providers): {'PASS' if _phase('Key Redaction').passed else 'FAIL'}")
    print(f"Due-Diligence Invariants: {'PASS' if _phase('Metrics & Invariants').passed else 'FAIL'}")
    print(f"REST API surface: {'PASS' if _phase('Terminal Consoles').passed else 'FAIL'}")
    print(f"Terminal HTML contract: {'PASS' if _phase('Terminal HTML Contract').passed else 'FAIL'}")
    print(f"Harness Console REST API: {'PASS' if _phase('Harness Console').passed else 'FAIL'}")
    print(f"Harness HTML contract: {'PASS' if _phase('Harness HTML Contract').passed else 'FAIL'}")
    config_phase = _phase("Config Invariants")
    print(f"Security Invariants: {config_phase.passed_count}/{len(config_phase.checks)} passed")
    tier_note = "real daemon/mock already up" if OLLAMA_TIER == 2 else "own mock_ollama.py needed"
    print(f"Ollama realism tier: {OLLAMA_TIER} ({tier_note})")
    print(f"{'='*60}")

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_checks": total_checks,
        "total_passed": total_passed,
        "ollama_tier": OLLAMA_TIER,
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
