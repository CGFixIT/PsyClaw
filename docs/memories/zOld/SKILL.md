---
name: cyclaw-swarm-verification
description: >
  CyClaw Swarm Verification -- comprehensive test system for the CyClaw
  offline-first RAG project (github.com/CGFixIT/CyClaw). Verifies LangGraph
  routing (5 queries), triple-gated online API fallback (Grok + Claude) with
  connection-only tests (no API cost), API key redaction, due-diligence
  invariants, and ALL terminal.html REST endpoints: /soul, /ops/sync,
  /ops/agentic, /ops/fsconnect, /ops/sqlconnect. Use when asked to verify,
  smoke-test, validate, or test CyClaw; mentions CyClaw swarm, terminal
  consoles, triple-gate API, Grok/Claude fallback, key redaction, due
  diligence invariants, or running the test suite.
---

# CyClaw Swarm Verification

Comprehensive test harness for CyClaw. Verifies the core LangGraph pipeline,
triple-gated online API fallback (Grok + Claude), all terminal console REST
endpoints, due-diligence invariants, API key redaction, and security
invariants. Supports both sandbox (stub/mock) mode and full-dependency mode
with real package installation.

## Core Workflow

### Phase 1 -- Clone & Inspect

```bash
git clone https://github.com/CGFixIT/CyClaw.git
cd CyClaw && git checkout main && git pull
```

Inspect: `gate.py`, `graph.py`, `config.yaml`, `pyproject.toml`,
`llm/client.py`, `retrieval/hybrid_search.py`, `utils/personality.py`,
`utils/ops_runner.py`, `utils/metrics.py`, `static/terminal.html`,
`agentic/fsconnect/client.py`, `agentic/sqlconnect/client.py`,
`schemas/api.py`, `tests/test_due_diligence_invariants.py`.

Verify config invariants:
- `app.mode`: "offline"
- `api.host`: "127.0.0.1", `api.port`: 8787
- `models.grok.enabled`: false (triple-gate offline default)
- `models.claude.enabled`: false
- `retrieval.min_score`: 0.028
- 33 banned injection patterns in `policy.prompt_filter.banned_patterns`
- `fsconnect` block present (default `enabled: false`)
- `sqlconnect` block present (default `enabled: false`, `read_only: true`, `allow_write: false`)
- `sync` block present (default `enabled: false`)
- `agentic` block present (default `enabled: false`, `mode: read`, `writes_enabled: false`)
- `policy.fallback.require_user_confirm`: present but **unwired** (hardcoded in user_gate_router)
- `policy.fallback.grok_max_prompt_chars`: 8000
- `policy.fallback.claude_max_prompt_chars`: 8000
- `logging.audit.redact_secrets_like`: includes `sk-ant-*` pattern for Anthropic keys

Verify module isolation invariants:
- `agentic/` NEVER imported by `gate.py`, `graph.py`, `mcp_hybrid_server.py`
- `agentic.*.cli` modules run ONLY via subprocess from `utils/ops_runner`

Verify code structure invariants (post-PR#441 refactor):
- `graph.py` has `_external_fallback_node(state, client, cfg, *, provider, label)`
- `grok_fallback_node` is a thin wrapper calling `_external_fallback_node(..., provider="grok", label="Grok")`
- `claude_fallback_node` is a thin wrapper calling `_external_fallback_node(..., provider="claude", label="Claude")`
- Both nodes share: prompt assembly, cost-guard truncation, audit-log truncation events

### Phase 2 -- Environment Setup

Requirements: Python 3.12+, `rank-bm25`, `nltk`, PyYAML, numpy, httpx.

**Full dependency install (preferred -- when network available):**
```bash
pip install -e ".[test,full]"
```
This installs: chromadb, sentence-transformers, langgraph, fastapi, uvicorn,
httpx, pytest, and all console-specific deps (psycopg, pyodbc for SQL).

**Sandbox fallback (missing chromadb / sentence-transformers / langgraph):**
Create in-memory stubs before importing CyClaw modules. See
`scripts/run_full_verification.py` for the complete stub implementation.

### Phase 3 -- Build Mock Corpus & Index

Create corpus files under `data/corpus/`:
- `cyclaw_about.md` -- CyClaw overview and key features
- `cyclaw_architecture.md` -- Graph flow and security invariants
- `cyclaw_security.md` -- Injection patterns and rate limiting
- `offline_mode.md` -- Offline mode behavior
- `general_knowledge.md` -- General world knowledge for best-effort testing

Build BM25 index (`index/bm25.json`) and mock ChromaDB index
(`index/chroma_db/`). See `scripts/run_full_verification.py` for the
implementation.

### Phase 4 -- Execute 5 Queries

Use the real node functions from `graph.py`:
```python
from graph import (retrieve_node, route_by_score_node, local_llm_node,
                   user_gate_node, offline_best_effort_node, audit_logger_node,
                   grok_fallback_node, claude_fallback_node)
```

**Query 1 -- Local/RAG vault hit:** `"what is CyClaw"`
- Expect: high score -> `local_llm` -> `answer_model="local"`
- Verifies: retrieval works, score routing to local_llm, answer generation

**Query 2 -- Local/RAG vault hit (different doc):** `"explain CyClaw security"`
- Expect: high score -> `local_llm` -> `answer_model="local"`
- Verifies: retrieval from security.md doc, multi-doc corpus coverage

**Query 3 -- Vault miss / Offline best-effort (Qwen test):** `"who wrote the theory of general relativity and when"`
- Expect: low score -> `user_gate` -> `needs_user_confirm=True`
- On deny (`user_confirmed_online=False`): `offline_best_effort_node` -> `answer_model="offline-best-effort"`
- Purpose: Tests Qwen's general knowledge reasoning with no vault context. Qwen
  should answer from its parametric knowledge (Einstein, 1915). The corpus has
  NO relativity content, forcing the best-effort path.

**Query 4 -- Vault miss / Grok online API (connection-only):** `"what are the latest features in xAI Grok 4"`
- Expect: low score -> `user_gate` -> `needs_user_confirm=True`
- On confirm with `online_provider="grok"`: verify GrokClient is called with
  correct request shape (mocked, no real API cost)
- Purpose: Verifies Grok API setup -- request headers (`Authorization: Bearer`),
  endpoint (`/chat/completions`), JSON payload shape. Does NOT require
  `GROK_API_KEY` to be set; mock the HTTP layer.

**Query 5 -- Vault miss / Claude online API (connection-only):** `"explain quantum computing decoherence"`
- Expect: low score -> `user_gate` -> `needs_user_confirm=True`
- On confirm with `online_provider="claude"`: verify ClaudeClient is called
  with correct request shape (mocked, no real API cost)
- Purpose: Verifies Claude API setup -- request headers (`x-api-key`,
  `anthropic-version`), endpoint (`/messages`), JSON payload shape. Does NOT
  require `ANTHROPIC_API_KEY` to be set; mock the HTTP layer.

### Phase 5 -- Triple-Gated Online API Verification (Grok + Claude)

This phase tests the vault-miss -> user confirmation -> online API fallback
path. The "triple gate" is:
1. **Score Gate**: `route_by_score_node` (score < min_score triggers user_gate)
2. **User Gate**: `user_gate_node` (human must confirm online escalation)
3. **Availability Gate**: `user_gate_router` checks `client.is_available()`

Since PR#441 + follow-up commits, both providers share `_external_fallback_node`:
- `grok_fallback_node(state, grok, cfg)` = `_external_fallback_node(state, grok, cfg, provider="grok", label="Grok")`
- `claude_fallback_node(state, claude, cfg)` = `_external_fallback_node(state, claude, cfg, provider="claude", label="Claude")`

The shared function reads: `send_local_context_to_<provider>`,
`<provider>_max_prompt_chars`, emits `<provider>_prompt_truncated` audit events,
and sets `answer_model = provider`.

#### 5a -- Grok Triple-Gate Test

Verify `GrokClient` in `llm/client.py`:
- `is_available()` returns `True` when `GROK_API_KEY` env var is set and
  non-empty; `False` otherwise
- `generate()` raises `GrokServiceError` when API key is missing
- `generate()` calls the configured `base_url` with correct OpenAI-compatible
  `/chat/completions` payload
- Retry: 5xx and 429 are retried; 401/403 fail fast; read timeout NOT retried
  (`retry_on_timeout=False` for local)

**Connection-only test** (no query cost): Mock `httpx.Client.post` to return a
200 with a valid `choices[0].message.content` shape. Verify the request
headers include `Authorization: Bearer <key>` and the JSON body has the
correct `model`, `messages`, `max_tokens`, `temperature`.

**Full triple-gate integration test** (using `build_graph`):
```python
mock_grok = GrokClient(cfg=cfg)
mock_grok.api_key = "test-key"
graph = build_graph(retriever=retriever, llm=llm, grok=mock_grok, claude=None, cfg=cfg)

state = {"query": "rocket ship", "user_confirmed_online": True, "online_provider": "grok"}
result = graph.invoke(state)
assert result["answer_model"] == "grok"
assert result["needs_user_confirm"] is False
```

**Deny-path test**: `user_confirmed_online=False` -> `offline_best_effort_node`.

**Unavailable-Grok test**: `grok=None` or `grok.is_available()=False` ->
`offline_best_effort_node` even when `user_confirmed_online=True`.

#### 5b -- Claude Triple-Gate Test

Verify `ClaudeClient` in `llm/client.py`:
- `is_available()` returns `True` when `ANTHROPIC_API_KEY` env var is set and
  non-empty; `False` otherwise
- `generate()` raises `ClaudeServiceError` when API key is missing
- `generate()` calls `base_url/messages` with Anthropic headers:
  `x-api-key`, `anthropic-version`, `content-type: application/json`
- Retry: 5xx and 429 are retried; 401/403 fail fast

**Connection-only test** (no query cost): Mock `httpx.Client.post` to return
200 with `{"content": [{"type": "text", "text": "..."}]}`. Verify:
- URL ends with `/messages` (NOT `/chat/completions`)
- Headers: `x-api-key` (NOT `Authorization: Bearer`), `anthropic-version`
- JSON body: `model`, `max_tokens`, `messages` (NO `temperature`)

**Full triple-gate integration test**:
```python
mock_claude = ClaudeClient(cfg=cfg)
mock_claude.api_key = "test-key"
graph = build_graph(retriever=retriever, llm=llm, grok=None, claude=mock_claude, cfg=cfg)

state = {"query": "quantum computing", "user_confirmed_online": True, "online_provider": "claude"}
result = graph.invoke(state)
assert result["answer_model"] == "claude"
```

**Claude-does-not-call-Grok test**: When `online_provider="claude"`,
`grok.generate()` must never be called even if Grok is available.

**Unavailable-Claude test**: `claude=None` or `claude.is_available()=False` ->
`offline_best_effort_node`.

#### 5c -- Soul Preamble Privacy Test

Verify that `_external_fallback_node` does NOT accept a `personality`
parameter and does NOT include the soul/identity preamble. Inspect the
signature: only `state`, `client`, `cfg`, `provider`, `label`. The soul is
NEVER forwarded off-box (invariant 14). `local_llm_node` and
`offline_best_effort_node` DO include soul when personality is enabled.

#### 5d -- Prompt Assembly & Cost-Guard Truncation Test

Verify both providers share the same truncation logic via
`_external_fallback_node`. When the prompt exceeds
`<provider>_max_prompt_chars`:
1. With context forwarding ON: budget the variable context, preserve framing
2. With context forwarding OFF: simple tail slice
3. Audit log event: `<provider>_prompt_truncated` with `original_chars`,
   `truncated_chars`, `query`
4. Warning log emitted with provider-specific label

Verify no fabricated sources: `answer_sources` is always `[]` for external
fallbacks (Grok and Claude answer from their own knowledge, not local docs).

### Phase 6 -- API Key Redaction & Secret Sanitization

Since PR#441 follow-up (`78515b0`), Anthropic API keys are redacted with the
same rigor as Grok keys.

Verify in `gate.py::_sanitize_error`:
1. `ANTHROPIC_API_KEY` is in the env-var redaction tuple alongside
   `GROK_API_KEY`, `CYCLAW_API_KEY`, etc.
2. Anthropic key pattern `sk-ant-[A-Za-z0-9_\-]{20,}` is in `_SECRET_PATTERNS`
   (real Anthropic keys like `sk-ant-api03-...` contain hyphens)

Verify in `config.yaml`:
- `logging.audit.redact_secrets_like` includes `sk-ant-*` pattern

Test: Simulate an exception message containing `sk-ant-api03-testkey123` and
verify it is redacted to `[REDACTED]` before the HTTP response body.

### Phase 7 -- Metrics & Audit Integrity

Verify in `metrics.py`:
- `online_escalated` heuristic checks `answer_model in {"grok", "claude"}`
- Legacy fallback: `model_used.startswith("grok")` OR `model_used.startswith("claude")`
- Both providers counted in `/audit/summary` escalation tally

Verify `audit_logger_node` sets:
- `"online_escalated": state.get("answer_model") in {"grok", "claude"}`
- `"model_used": "grok"` or `"claude"` or `"local"` or `"offline-best-effort"`

### Phase 8 -- Due-Diligence Invariants

Verify the real invariants pinned by `tests/test_due_diligence_invariants.py`:

| Test Class | What It Checks |
|------------|---------------|
| `TestRagFirstEntry` | `retrieve` is the unconditional graph entry point |
| `TestExternalCallGateRuntimeHalf` | Both grok+claude require: hybrid mode + enabled + key + user confirm |
| `TestExternalCallGateConstructionHalf` | `build_graph` only constructs clients when mode=hybrid + enabled |
| `TestAuditConvergence` | Every node reaches `audit_logger`; `audit_logger` -> END |
| `TestSoulReasonGate` | `apply_evolution` refuses empty reason |
| `TestSoulInjectionScanBoundary` | Injection scanner covers the documented patterns |
| `TestAuditQueryPrivacy` | Audit log contains SHA-256 hashes, never plaintext queries |
| `TestSanitizerCwdIndependence` | Config loading works regardless of CWD |
| `TestMcpNoLlmPath` | MCP server path never calls LLM directly |
| `TestCoreModuleIsolation` | `agentic/` never imported by gate/graph/mcp |
| `TestHealthEmbeddingsSignalIsStatic` | Embeddings health signal does not depend on model load |
| `TestFallbackRequireUserConfirmIsUnwired` | `policy.fallback.require_user_confirm` is NOT read by gate.py or graph.py |

The `require_user_confirm` key is **documented as unwired** in config.yaml.
The actual confirmation pause is hardcoded in `user_gate_router`:
`confirmed is None -> audit_logger` (pause). Setting this config key has
no effect. A future wiring change must update BOTH the config comment AND
this test.

### Phase 9 -- Terminal REST API Full Verification

Verify that `gate.py` exposes ALL routes required by `static/terminal.html`.

#### 9a -- Core Endpoints

| Endpoint | Method | Verify |
|----------|--------|--------|
| `/health` | GET | Returns `status`, `mode`, `graph_timeout_sec`, `index_ready`, `graph_ready`, `version` |
| `/query` | POST | RAG-first query/response cycle with sources, scores, model_used |
| `/soul` | GET | Returns `soul`, `version`, `source` (API-key gated, personality enabled) |
| `/soul/propose` | POST | Accepts `new_soul` + `reason`, returns proposal envelope with SHA hashes |
| `/soul/apply` | POST | Applies evolution with human reason gate + injection scan |
| `/soul/reload` | POST | Reloads soul from disk |
| `/soul/restore` | POST | Restores from `.bak` |
| `/audit/summary` | GET | Returns aggregate audit stats (API-key gated) |

#### 9b -- Soul Console (`/soul/*`)

Test the full CRUD lifecycle:
1. `GET /soul` -- load current soul content
2. `POST /soul/propose` -- propose evolution with `new_soul` + `reason`
3. `POST /soul/apply` -- apply with matching proposal
4. Verify `POST /soul/apply` rejects without `reason` (injection scan)
5. `POST /soul/reload` -- revert to disk
6. `POST /soul/restore` -- restore from `.bak`
7. Verify all `/soul/*` mutations return `401` without `CYCLAW_API_KEY`
8. Verify all `/soul/*` mutations return `429` under rate-limit exhaustion

#### 9c -- Sync Console (`/ops/sync`)

| Action | Verify |
|--------|--------|
| `status` | Returns config block with `enabled`, `direction`, `schedule` |
| `sync` + `dry_run=true` | Dry-run flag passed correctly to CLI |
| `sync` + `dry_run=false` | Full sync executed |
| `schedule` | Enable scheduled sync |
| `unschedule` | Disable scheduled sync |
| unknown action | Rejected with `OpsError` -> HTTP 400 |

#### 9d -- Agentic Console (`/ops/agentic`)

| Action | Verify |
|--------|--------|
| `status` | Returns config with `enabled`, `mode`, `writes_enabled`, `registry_version`, `skills` |
| `context` + `pr=N` | Fetches PR context via `--pr` flag |
| `context` + `issue=N` | Fetches issue context via `--issue` flag |
| `propose-skill` | Proposes skill with `name`, `desc`, `body`, `reason` |
| `apply-skill` | 4-gate checklist: `mode=write` + `writes_enabled=true` + non-empty `reason` + `confirm=true` |
| unknown action | Rejected with `OpsError` -> HTTP 400 |

Verify 4-gate checklist in UI + backend:
- Gate 1: `agentic.mode == "write"`
- Gate 2: `agentic.writes_enabled == true`
- Gate 3: non-empty `reason` string
- Gate 4: `confirm == true`
- Apply button disabled until ALL gates pass (with defaults: always disabled)

#### 9e -- Filesystem Console (`/ops/fsconnect`) -- Read-Only

| Action | Verify |
|--------|--------|
| `status` | Returns config with `enabled`, `allowed_roots`, `writes_enabled`, `max_file_bytes` |
| `list` | Directory listing scoped to `root`/`path`; returns `entries` |
| `read` | File read returns `content`, `size`, `is_binary`, `encoding`, `injection_flags` |
| `stat` | File metadata returns path info dict |
| `grep` | Text search with `pattern`/`regex`; returns `matches` (cap 200) |
| `glob` | Pattern search; returns `matches` (cap 1000) |
| unknown action | `_FSCONNECT_ACTIONS` whitelist rejects -> HTTP 400 |

#### 9f -- SQL Console (`/ops/sqlconnect`) -- Read-Only

| Action | Verify |
|--------|--------|
| `status` | Returns config with `enabled`, `driver`, `read_only`, `max_rows` |
| `schema` | Returns schema list (verify error envelope when DSN unset) |
| `query` + `table` | Table preview with optional `count`, `explain` |
| `query` + `sql` | Raw SELECT/WITH query with optional `explain`, `fmt` (json/csv) |
| unknown action | `_SQLCONNECT_ACTIONS` whitelist rejects -> HTTP 400 |

#### 9g -- Rate Limiting & Security Headers (All Endpoints)

Verify for ALL `/ops/*`, `/soul/*`, `/query`, `/audit/summary`:
- Returns `429` when per-IP rate limit exceeded
- API-key-gated endpoints return `401` when `CYCLAW_API_KEY` missing/invalid
- Responses include security headers: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`,
  `Permissions-Policy`, `Content-Security-Policy`
- TrustedHostMiddleware rejects non-allowed Host headers

### Phase 10 -- Terminal HTML Console Contract

Verify `static/terminal.html` contracts:
- All 5 console panels exist (soul, sync, agentic, fs, sql)
- Confirm dialog has: **"Send to Grok"** button with `handleConfirm(true, id, 'grok')`
- Confirm dialog has: **"Send to Claude"** button with `handleConfirm(true, id, 'claude')`
- `submitQuery` passes `body.online_provider = onlineProvider`
- Dynamic provider label: `` `Escalating to ${providerLabel}...` ``
- Confirm message: `"Choose Offline Best Effort, Send to Grok, or Send to Claude."`
- `authHeaders()` + `apiKeyInput` for API key gating
- `/health` polling for status

### Phase 11 -- Unit & Integration Test Suite

**Run order:**
```bash
# 1. Built-in pytest suite (if dependencies available)
python -m pytest tests/ -v --tb=short 2>/dev/null || echo "pytest deps missing"

# 2. Smoke test (always works)
python scripts/run_full_verification.py

# 3. Full integration (requires gate.py running)
# CYCLAW_API_KEY=test-key python gate.py &
# python scripts/test_terminal_consoles.py
```

**Unit test patterns to verify:**

| Test File | Coverage |
|-----------|----------|
| `test_graph.py` | All nodes: retrieve, route_by_score, local_llm, user_gate, grok_fallback, claude_fallback, offline_best_effort, audit_logger. Includes `TestClaudeFallbackPrompt` (10 tests mirroring Grok) |
| `test_gate.py` | All HTTP endpoints, query provider passthrough, Anthropic key redaction |
| `test_client.py` | LocalLLMClient + GrokClient + **ClaudeClient** (error/retry/timeout/401-fail-fast parity) |
| `test_ops_runner.py` | Subprocess delegation for all 4 ops runners |
| `test_fsconnect_*.py` | FsConnect CLI, client, config, pathsafe, writer |
| `test_sqlconnect_*.py` | SQLConnect CLI, client, config, read-only guards |
| `test_sync_*.py` | Sync CLI, config, runner, scheduler, filters |
| `test_agentic_*.py` | Agentic CLI, config, context, writer, registry |
| `test_personality.py` | Soul CRUD, evolution, injection blocking |
| `test_security.py` | Prompt injection, rate limiting, sanitization |
| `test_health.py` | Health check with grok + **claude** probes |
| `test_metrics.py` | Audit integrity, **claude escalation heuristic** |
| `test_telemetry_kill.py` | 10 env vars set at import time |
| `test_due_diligence_invariants.py` | **12 invariant classes**: RAG-first, external call gates, audit convergence, soul governance, soul injection, audit privacy, sanitizer CWD, MCP no-LLM, module isolation, health embeddings, **unwired require_user_confirm** |
| `test_terminal_contract.py` | Console endpoint existence, **explicit provider buttons** |

### Phase 12 -- Report

End with:
```
CyClaw Swarm Verification Complete.
Full functionality status: [PASS/FAIL].
RAG pipeline (5 queries): [PASS/FAIL]
  - Query 1 (vault hit): [PASS/FAIL]
  - Query 2 (vault hit): [PASS/FAIL]
  - Query 3 (offline best-effort / Qwen): [PASS/FAIL]
  - Query 4 (Grok API connection-only): [PASS/FAIL]
  - Query 5 (Claude API connection-only): [PASS/FAIL]
REST API surface: [PASS/FAIL]
Terminal Consoles (all 5): [PASS/FAIL]
Triple-Gate Online API (Grok): [PASS/FAIL]
Triple-Gate Online API (Claude): [PASS/FAIL]
API Key Redaction (Grok + Claude): [PASS/FAIL]
Due-Diligence Invariants: [X/12 passed]
Security Invariants: [X/17 passed]
Recommendations: ...
```

## Bundled Resources

### `scripts/run_full_verification.py`

Self-contained comprehensive test script with **5 queries**. Run directly:
```bash
python3 scripts/run_full_verification.py
```

What it does:
1. Creates all in-memory stubs (chromadb, sentence_transformers, etc.)
2. Writes 5 corpus files to `data/corpus/` (replaces fight_club with
   `general_knowledge.md` for best-effort testing)
3. Builds BM25 and ChromaDB indexes
4. Patches `retrieval.embeddings._load_model`
5. Imports and runs `graph.py` node functions for **5 queries**:
   - Q1/Q2: Vault hit tests (CyClaw overview + security)
   - Q3: Offline best-effort with Einstein/relativity question (no vault match)
   - Q4: Grok API connection-only test (mocked HTTP, verifies request shape)
   - Q5: Claude API connection-only test (mocked HTTP, verifies Anthropic headers)
6. **Tests triple-gate Grok path** with mocked GrokClient
7. **Tests triple-gate Claude path** with mocked ClaudeClient
8. **Tests API key redaction** for both `GROK_API_KEY` and `ANTHROPIC_API_KEY`
9. **Tests soul preamble privacy** (external nodes never get soul)
10. **Tests _external_fallback_node** shared truncation logic
11. **Tests due-diligence invariants** (unwired require_user_confirm, module isolation)
12. **Tests metrics escalation** for both providers
13. Verifies `utils/ops_runner.py` has all 4 runners
14. Verifies all action whitelists are non-empty
15. Verifies all config blocks exist in `config.yaml`
16. Verifies SQL read-only guards are importable and functional
17. Verifies FsClient and SqlClient method signatures
18. Verifies security headers middleware + TrustedHostMiddleware
19. Verifies rate limiter is initialized with config values
20. Verifies terminal.html contract (5 panels, 2 provider buttons)

### `scripts/test_terminal_consoles.py`

Integration test for terminal console REST endpoints. Requires `gate.py`
running with `CYCLAW_API_KEY` set.

```bash
CYCLAW_API_KEY=test-key python gate.py &
sleep 3
python scripts/test_terminal_consoles.py
```

### `references/test-specifications.md`

Detailed test case inventory with expected inputs, outputs, and assertion
criteria. Read when implementing new tests or debugging failures.

## Mock Embedding Implementation

`MockSentenceTransformer` creates sparse keyword-based 384-dim vectors:
- Each word hashes to 3 dimension slots via MD5
- Slot values accumulate per word occurrence
- Final vector L2-normalized

`MockChromaClient` / `MockCollection` implement:
- `add(embeddings, documents, metadatas, ids)` -- append documents
- `query(query_embeddings, n_results)` -- cosine similarity search
- `get_or_create_collection(name)` -- singleton collection registry

## Security Invariants Checklist

| # | Invariant | Check |
|---|-----------|-------|
| 1 | RAG-First | `retrieve_node` is always N1 |
| 2 | Topology = Policy | Routing via `route_by_score_node`, not prompts |
| 3 | Triple-Gated External | `grok.enabled=false` + `claude.enabled=false` (default) |
| 4 | Audit Convergence | `audit_logger_node` is always last |
| 5 | Soul Governance | Evolution requires human reason string |
| 6 | Zero Telemetry | 10 env vars killed at import time |
| 7 | Loopback Only | `api.host="127.0.0.1"` |
| 8 | FsConnect Read-Only Default | `fsconnect.writes_enabled=false`, `follow_symlinks=false` |
| 9 | FsConnect Pathsafe | All paths through `ScopedRoots` with `O_NOFOLLOW` |
| 10 | FsConnect Op Whitelist | Only `fs_list`, `fs_stat`, `fs_read`, `fs_grep`, `fs_glob` |
| 11 | SQL Read-Only Default | `sqlconnect.read_only=true`, `allow_write=false` |
| 12 | SQL Query Guard | Only SELECT/WITH; comments and `;` rejected |
| 13 | Module Isolation | `agentic/` never imported by `gate.py` / `graph.py` / `mcp_hybrid_server.py` |
| 14 | Soul Privacy | Soul preamble never forwarded to Grok/Claude (off-box) |
| 15 | API Key Gate | All mutations require `CYCLAW_API_KEY` (fail-closed) |
| 16 | Rate Limit | All endpoints share per-IP rate limiter |
| 17 | Key Redaction Parity | `ANTHROPIC_API_KEY` redacted same as `GROK_API_KEY`; `sk-ant-*` pattern in both gate.py and config.yaml |
