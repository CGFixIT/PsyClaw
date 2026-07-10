# CyClaw Test Specifications

Detailed test case inventory for the CyClaw Swarm Verification skill. Covers
PR#441 (Claude fallback), post-merge commits (key redaction, shared fallback
node, metrics, due-diligence invariants), and all terminal console endpoints.

## Table of Contents
1. [Query Prompts](#query-prompts)
2. [Triple-Gate Online API Tests](#triple-gate-online-api-tests)
3. [API Key Redaction Tests](#api-key-redaction-tests)
4. [Due-Diligence Invariant Tests](#due-diligence-invariant-tests)
5. [Terminal Console Endpoint Tests](#terminal-console-endpoint-tests)
6. [Config Invariants](#config-invariants)

---

## Query Prompts

### Q1 -- Vault Hit (CyClaw Overview)
- **Prompt**: `"what is CyClaw"`
- **Expected**: high score, `local_llm`, `answer_model="local"`
- **Corpus match**: `cyclaw_about.md`
- **Purpose**: Verifies retrieval works, score routing, answer generation

### Q2 -- Vault Hit (Security Doc)
- **Prompt**: `"explain CyClaw security"`
- **Expected**: high score, `local_llm`, `answer_model="local"`
- **Corpus match**: `cyclaw_security.md`
- **Purpose**: Multi-doc coverage, different vocabulary path

### Q3 -- Offline Best-Effort / Qwen Test
- **Prompt**: `"who wrote the theory of general relativity and when"`
- **Expected**: low score, `user_gate`, `needs_user_confirm=True`
- **On deny**: `offline_best_effort_node`, `answer_model="offline-best-effort"`
- **Corpus**: `general_knowledge.md` has NO relativity content (only speed of light)
- **Purpose**: Forces best-effort path. Qwen answers from parametric knowledge
  (Einstein, 1915) with no vault context. Tests the deny-path through all 3 gates.

### Q4 -- Grok API Connection-Only
- **Prompt**: `"what are the latest features in xAI Grok 4"`
- **Expected**: low score, `user_gate`, `needs_user_confirm=True`
- **On confirm + provider="grok"**: Verify GrokClient.generate() called
- **Mock HTTP**: Return `{choices:[{message:{content:"..."}}]}`
- **Verify request**: `Authorization: Bearer <key>`, `/chat/completions`,
  `model`, `messages`, `max_tokens`, `temperature`
- **Does NOT require**: `GROK_API_KEY` to be set (mock the HTTP layer)
- **Purpose**: Verifies Grok API integration without cost

### Q5 -- Claude API Connection-Only
- **Prompt**: `"explain quantum computing decoherence"`
- **Expected**: low score, `user_gate`, `needs_user_confirm=True`
- **On confirm + provider="claude"**: Verify ClaudeClient.generate() called
- **Mock HTTP**: Return `{content:[{type:"text",text:"..."}]}`
- **Verify request**: `x-api-key` header (NOT `Authorization: Bearer`),
  `anthropic-version: 2023-06-01`, `/messages` endpoint (NOT `/chat/completions`),
  `model`, `max_tokens`, `messages` (NO `temperature`)
- **Does NOT require**: `ANTHROPIC_API_KEY` to be set (mock the HTTP layer)
- **Purpose**: Verifies Claude API integration without cost

---

## Triple-Gate Online API Tests

### Architecture (Post-Refactor)

```
Query -> retrieve_node (N1) -> route_by_score_node (N2)
  score < threshold -> user_gate_node (N3b)
    user_confirmed_online=None -> needs_confirm=True (UI shows buttons)
    user_confirmed_online=True + online_provider="grok" -> grok_fallback_node
      = _external_fallback_node(..., provider="grok", label="Grok")
    user_confirmed_online=True + online_provider="claude" -> claude_fallback_node
      = _external_fallback_node(..., provider="claude", label="Claude")
    user_confirmed_online=False -> offline_best_effort_node
  score >= threshold -> local_llm_node (N3a)
ALL paths -> audit_logger_node (N4) -> END
```

### Gate 1: Score Gate
- `route_by_score_node(state, cfg)`: score >= 0.028 -> local_llm, else user_gate

### Gate 2: User Gate (Hardcoded, NOT Config-Driven)
- `user_gate_router`: `confirmed is None` -> `audit_logger` (pause, show buttons)
- **IMPORTANT**: `policy.fallback.require_user_confirm` is UNWIRED. Setting it
  false has NO effect. The pause is hardcoded in `user_gate_router`.

### Gate 3: Availability Gate
- `user_gate_router(state, grok, claude)`: checks `online_provider` + `client.is_available()`

### _external_fallback_node Shared Implementation

Extracted from the near-identical grok/claude fallback nodes. Parameterized on
`provider` and `label`. Both wrappers are thin 1-line calls.

**Config keys read dynamically**: `send_local_context_to_<provider>`,
`<provider>_max_prompt_chars`

**Audit event**: `<provider>_prompt_truncated` with `original_chars`,
`truncated_chars`, `query`

**Truncation edge case**: When context forwarding is ON, the trailing
"Answer the query..." instruction must survive the truncation. Budget the
variable context, not the framing.

**No fabricated sources**: `answer_sources` is always `[]` for external fallbacks.

### Grok Tests

#### G1: Connection-Only Test (Mock HTTP)
```python
def test_grok_connection_only():
    mock_resp = httpx.Response(200, json={
        "choices": [{"message": {"content": "mocked grok response"}}]
    })
    client = GrokClient(cfg=test_cfg)
    client.api_key = "test-grok-key"
    client._client.post = MagicMock(return_value=mock_resp)
    result = client.generate("test prompt")
    call = client._client.post.call_args
    assert call[0][0].endswith("/chat/completions")
    assert "Bearer test-grok-key" in call[1]["headers"]["Authorization"]
    assert call[1]["json"]["model"] == "grok-4.3"
    assert call[1]["json"]["max_tokens"] == 256
    assert call[1]["json"]["temperature"] == 0.2
```

#### G2: is_available() Contract
- `GROK_API_KEY` set and non-empty -> `True`
- `GROK_API_KEY` unset or empty -> `False`
- Key stripped of whitespace before check

#### G3: Retry Behavior
- 5xx and 429: retried (with backoff)
- 401/403: fail fast (no retry)
- Read timeout: NOT retried (`retry_on_timeout=False`)

#### G4-G6: Triple-Gate Integration, Deny Path, Unavailable
See SKILL.md Phase 5 for full test code.

### Claude Tests

#### C1: Connection-Only Test (Mock HTTP)
```python
def test_claude_connection_only():
    mock_resp = httpx.Response(200, json={
        "content": [{"type": "text", "text": "mocked claude response"}]
    })
    client = ClaudeClient(cfg=test_cfg)
    client.api_key = "test-anthropic-key"
    client._client.post = MagicMock(return_value=mock_resp)
    result = client.generate("test prompt")
    call = client._client.post.call_args
    assert call[0][0].endswith("/messages")  # NOT /chat/completions
    assert call[1]["headers"]["x-api-key"] == "test-anthropic-key"
    assert call[1]["headers"]["anthropic-version"] == "2023-06-01"
    assert "temperature" not in call[1]["json"]  # Claude doesn't send temp
    assert call[1]["json"]["messages"][0]["role"] == "user"
```

#### C2: is_available() Contract
- `ANTHROPIC_API_KEY` set and non-empty -> `True`
- `ANTHROPIC_API_KEY` unset or empty -> `False`

#### C3: Retry Behavior (Parity with Grok)
- 5xx and 429: retried
- 401/403: fail fast
- Malformed response: Claude-specific error mapping

#### C4-C8: Triple-Gate, Cross-Provider, Soul Privacy, Unavailable
See SKILL.md Phase 5.

### Cross-Provider Tests

#### X1: Both Enabled, Provider Selects Correct One
```python
def test_online_provider_selects_correct_client():
    grok = MockGrokClient(response="Grok answer")
    claude = MockClaudeClient(response="Claude answer")
    graph = build_graph(retriever=r, llm=llm, grok=grok, claude=claude, cfg=cfg)
    result_g = graph.invoke({..., "online_provider": "grok"})
    assert result_g["answer_model"] == "grok"
    result_c = graph.invoke({..., "online_provider": "claude"})
    assert result_c["answer_model"] == "claude"
```

#### X2: Default Provider Behavior
When `online_provider` is not set, `user_gate_router` defaults to "grok"
(if available).

---

## API Key Redaction Tests

### Background
Commit `78515b0` added Anthropic key redaction with the same rigor as Grok:
- `ANTHROPIC_API_KEY` added to env-var redaction tuple in `gate.py`
- `sk-ant-[A-Za-z0-9_\-]{20,}` pattern added to `_SECRET_PATTERNS`
- `sk-ant-*` pattern added to `config.yaml` `audit.redact_secrets_like`

### Test Cases

#### R1: Env-Var Redaction
```python
def test_anthropic_key_env_redaction():
    gate_src = Path("gate.py").read_text()
    assert "ANTHROPIC_API_KEY" in gate_src
    assert "GROK_API_KEY" in gate_src
```

#### R2: Pattern Redaction in gate.py
```python
def test_sk_ant_pattern_in_secret_patterns():
    gate_src = Path("gate.py").read_text()
    assert "sk-ant-" in gate_src
```

#### R3: Pattern Redaction in config.yaml
```python
def test_sk_ant_in_audit_config():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    patterns = cfg["logging"]["audit"]["redact_secrets_like"]
    assert any("sk-ant" in str(p) for p in patterns)
```

#### R4: Functional Redaction Test
```python
def test_anthropic_key_redacted_in_errors():
    from gate import _sanitize_error
    raw = "Error: key sk-ant-api03-testkey123456789 failed"
    sanitized = _sanitize_error(raw)
    assert "sk-ant-api03" not in sanitized or "[REDACTED]" in sanitized
```

---

## Due-Diligence Invariant Tests

From `tests/test_due_diligence_invariants.py` (12 test classes):

### TestRagFirstEntry
- `retrieve` is the unconditional graph entry point
- `build_graph` -> `set_entry_point("retrieve")` is present

### TestExternalCallGateRuntimeHalf
- Both grok+claude require: hybrid mode + model enabled + API key set + user confirm
- Runtime gate fires for each provider independently

### TestExternalCallGateConstructionHalf
- `build_graph` only constructs GrokClient/ClaudeClient when mode=hybrid AND model enabled
- Offline mode: no external clients constructed

### TestAuditConvergence
- Every node routes to `audit_logger`
- `audit_logger` -> END (no further nodes)
- Audit log written on every path

### TestSoulReasonGate
- `apply_evolution` refuses empty reason string
- Non-empty reason required for soul mutation

### TestSoulInjectionScanBoundary
- Injection scanner covers all 33 banned patterns
- Scanner runs on proposed soul content before apply

### TestAuditQueryPrivacy
- Audit log contains SHA-256 hashes of queries, NEVER plaintext
- `hash_query()` used for query identification

### TestSanitizerCwdIndependence
- Config loading works regardless of current working directory
- `load_config()` resolves paths correctly

### TestMcpNoLlmPath
- MCP server code path never calls LLM directly
- MCP routes through graph, not direct LLM calls

### TestCoreModuleIsolation
- `agentic/` never imported by `gate.py`, `graph.py`, `mcp_hybrid_server.py`
- All agentic modules run via subprocess only

### TestHealthEmbeddingsSignalIsStatic
- Embeddings health signal does not depend on model being loaded
- Static config check, not dynamic model inference

### TestFallbackRequireUserConfirmIsUnwired
- **CRITICAL**: `policy.fallback.require_user_confirm` is NOT read by any production code
- `gate.py` and `graph.py` both grep-negative for the string
- `user_gate_router` hardcodes `confirmed is None` -> pause
- Config key kept for backward compat but documented as unwired
- Test will FAIL if someone wires it up (deliberate breakage signal)

---

## Terminal Console Endpoint Tests

### Soul Console (`/soul/*`)

All endpoints require `CYCLAW_API_KEY`.

| Test | Method | Endpoint | Body | Expected |
|------|--------|----------|------|----------|
| SC-1 | GET | `/soul` | - | 401 without API key |
| SC-2 | GET | `/soul` | `Authorization: Bearer <key>` | 200, `{soul, version, source}` |
| SC-3 | POST | `/soul/propose` | `{new_soul, reason}` | 200, `{current_sha, proposed_sha, ...}` |
| SC-4 | POST | `/soul/apply` | `{new_soul, reason}` | 200, `{status, version, source}` |
| SC-5 | POST | `/soul/apply` | `{new_soul}` (no reason) | 400 (injection/reason required) |
| SC-6 | POST | `/soul/reload` | - | 200, `{status: "reloaded", version}` |
| SC-7 | POST | `/soul/restore` | - | 200 or 404 (no .bak) |
| SC-8 | POST | `/soul/propose` | - | 429 when rate limited |

### Sync Console (`/ops/sync`)

| Test | Action | Body | Expected |
|------|--------|------|----------|
| SYNC-1 | `status` | `{action: "status"}` | `{config: {enabled, direction, schedule}}` |
| SYNC-2 | `sync` + dry_run | `{action: "sync", dry_run: true}` | `{exit_code, label, ok}` |
| SYNC-3 | `sync` | `{action: "sync", dry_run: false}` | Full sync |
| SYNC-4 | `schedule` | `{action: "schedule"}` | Schedule enabled |
| SYNC-5 | `unschedule` | `{action: "unschedule"}` | Schedule disabled |
| SYNC-6 | unknown | `{action: "destroy"}` | 400, `OPS_BAD_ACTION` |

### Agentic Console (`/ops/agentic`)

| Test | Action | Body | Expected |
|------|--------|------|----------|
| AG-1 | `status` | `{action: "status"}` | `{config: {enabled, mode, writes_enabled}, ...}` |
| AG-2 | `context` + pr | `{action: "context", pr: 123}` | PR context |
| AG-3 | `context` + issue | `{action: "context", issue: 456}` | Issue context |
| AG-4 | `propose-skill` | `{action: "propose-skill", name, desc, body, reason}` | Proposal |
| AG-5 | `apply-skill` (locked) | defaults | 4-gate checklist fails |
| AG-6 | `apply-skill` (open) | `{..., confirm: true}` | Requires all 4 gates |
| AG-7 | unknown | `{action: "hack"}` | 400 |

### Filesystem Console (`/ops/fsconnect`)

| Test | Action | Body | Expected |
|------|--------|------|----------|
| FS-1 | `status` | `{action: "status"}` | `{config: {...}}` |
| FS-2 | `list` | `{action: "list", root, path}` | `{entries: [...]}` |
| FS-3 | `read` | `{action: "read", root, path}` | `{content, size, ...}` |
| FS-4 | `stat` | `{action: "stat", root, path}` | Path info dict |
| FS-5 | `grep` | `{action: "grep", root, path, pattern}` | `{matches, match_count}` |
| FS-6 | `glob` | `{action: "glob", root, pattern}` | `{matches, match_count}` |
| FS-7 | unknown | `{action: "delete"}` | 400 |

### SQL Console (`/ops/sqlconnect`)

| Test | Action | Body | Expected |
|------|--------|------|----------|
| SQL-1 | `status` | `{action: "status"}` | `{config: {enabled, driver, read_only, max_rows}}` |
| SQL-2 | `schema` | `{action: "schema"}` | Error envelope when DSN unset |
| SQL-3 | `query` + table | `{action: "query", table: "users"}` | Table preview |
| SQL-4 | `query` + sql | `{action: "query", sql: "SELECT 1"}` | Query results |
| SQL-5 | `query` + explain | `{action: "query", sql: "...", explain: true}` | Explain plan |
| SQL-6 | `query` + fmt | `{action: "query", sql: "...", fmt: "csv"}` | CSV output |
| SQL-7 | unknown | `{action: "drop"}` | 400 |
| SQL-8 | write SQL | `{action: "query", sql: "INSERT..."}` | Rejected by guard |

---

## Config Invariants

### Offline Mode (Default)
```yaml
app:
  mode: "offline"
models:
  grok:
    enabled: false
  claude:
    enabled: false
policy:
  fallback:
    require_user_confirm: true  # UNWIRED - hardcoded in user_gate_router
    send_local_context_to_grok: false
    send_local_context_to_claude: false
    grok_max_prompt_chars: 8000
    claude_max_prompt_chars: 8000
logging:
  audit:
    redact_secrets_like:
      - "sk-[a-zA-Z0-9]{20,}"     # OpenAI-style
      - "sk-ant-[a-zA-Z0-9_\\-]{20,}"  # Anthropic (Claude)
```

### Hybrid Mode (For Testing)
```yaml
app:
  mode: "hybrid"
models:
  grok:
    enabled: true
  claude:
    enabled: true
```
- Both providers enabled
- User confirmation still required (hardcoded, Gate 2)
- `is_available()` checks API keys (Gate 3)
