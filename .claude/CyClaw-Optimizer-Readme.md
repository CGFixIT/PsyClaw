note for any loop task prompt or essentially any time an optimization skill is invoked - start here during 4 minute code scan time block and read each issue then compare to current code branch to verify valid/not already resolved

ALSO: there are several others like this to check first when fixing or optimizing cyclaw - under docs/* , docs/zIdeas, docs/audits, 
## Part II — Code Review 

### Bugs / Optimizations needed Summary Table

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High | 5 |
| Medium | 10 |
| Low | 9 |
| Info | 3 |
| **Total** | **27** |

### 2.2 HIGH Severity Findings

#### H1 — `audit_log` crashes with `IsADirectoryError` when `audit_file` is empty string
**File:** `utils/logger.py` | **Affects:** Tests that use base `TEST_CONFIG` without overriding `audit_file`

`TEST_CONFIG` in `conftest.py` sets `"audit_file": ""`. When `audit_log` is called with this config, `Path("")` resolves to cwd (a directory), and `open(Path(""), 'a')` raises `IsADirectoryError` on Linux. The `test_config` fixture does override this correctly, but any new test that calls `audit_log` with the raw `TEST_CONFIG` dict will hit this crash.

**Fix:** Add a guard at the top of `audit_log`:
```python
if not log_path.name:  # empty string path → skip silently
    return
```
Or in `conftest.py`, set `TEST_CONFIG["logging"]["audit_file"] = "/dev/null"` as the fallback.

---

#### H2 — `check_input` and `utils/health.py` use cwd-relative `config.yaml` defaults
**File:** `utils/sanitizer.py`, `utils/health.py`

`gate.py` introduced `_BASE_DIR = Path(__file__).resolve().parent` to fix cwd-relative crashes, but `check_input(query)` is called without an explicit config path in `gate.py` (line 270). If CyClaw is launched from a non-repo-root directory, `check_input` opens the relative `config.yaml` from the wrong path.

**Fix:**
```python
# In gate.py line ~270:
check_input(req.query, config_path=str(_BASE_DIR / "config.yaml"))
```
Apply the same fix to `health.py`'s default config path.

---

#### H3 — `SoulEvolutionRequest` schema accepts empty `reason` and unbounded `new_soul`
**File:** `schemas/api.py:53`

```python
class SoulEvolutionRequest(BaseModel):
    new_soul: str   # no max_length — arbitrary large payload bypasses check_input
    reason: str     # no min_length — empty string violates Soul Governance invariant
```

**Fix:**
```python
from pydantic import Field

class SoulEvolutionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    new_soul: str = Field(min_length=1, max_length=65536)
    reason: str = Field(min_length=1, description="Human-supplied reason for soul mutation (required)")
```

---

#### H4 — `ci_rag_smoke.py` fails when run directly (missing sys.path setup)
**File:** `tests/ci_rag_smoke.py:1`

When invoked as `python tests/ci_rag_smoke.py`, Python adds `tests/` to sys.path but not the repo root. Absolute imports (`from retrieval.indexer import build_index`) then fail with `ModuleNotFoundError`. CI runs it via `python -m tests.ci_rag_smoke` (adds repo root automatically) so CI passes, but direct developer invocation fails.

**Fix:** Add at line 1 (before all other imports):
```python
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
```

---

#### H5 — `SoulEvolutionRequest.reason` validated at schema level but `apply_evolution` accepts `reason=""` from internal callers
**File:** `utils/personality.py:238`

`apply_evolution(new_soul, reason)` does not validate that `reason` is non-empty before proceeding. While the `/soul/apply` endpoint will be gated by schema validation (once H3 is fixed), internal callers (e.g. tests or future code) can pass `reason=""` directly, violating Soul Governance invariant 5.

**Fix:**
```python
def apply_evolution(self, new_soul: str, reason: str, *, scan: bool = True) -> dict:
    if not reason or not reason.strip():
        raise ValueError("reason must be a non-empty string (Soul Governance invariant)")
    ...
```

---

### 2.3 MEDIUM Severity Findings

#### M1 — `grok_fallback_node` accesses `cfg['policy']['fallback']` without None-safety
**File:** `graph.py:~259`

```python
cfg['policy']['fallback'].get('send_local_context_to_grok', False)  # KeyError if 'policy' absent
```

**Fix:** `(cfg.get('policy') or {}).get('fallback', {}).get('send_local_context_to_grok', False)`

---

#### M2 — Semantic-only fallback mode masks BM25 contribution silence in small corpus
**File:** `retrieval/hybrid_search.py:~193`

When `keyword_hits` is empty (which happens with ≤2 documents due to BM25 IDF degeneration), `hybrid_search()` returns semantic hits with `retrieval_mode="semantic"`. This is correct code for the fallback, but there is no debug log to distinguish "BM25 correctly returned nothing" from "BM25 index is malformed."

**Fix:** Add:
```python
if not keyword_hits:
    logger.debug("keyword leg empty for query %r; semantic-only fallback", query[:50])
    return semantic_hits
```
Also add a multi-document corpus integration test that verifies `retrieval_mode=="hybrid"` when the corpus is large enough.

---

#### M3 — Prompt injection audit event may log raw query text if `include_query_hash=False`
**File:** `gate.py:~288`

`audit_log({"event": "prompt_injection_blocked", "query": req.query})` — if operator sets `include_query_hash: false`, the raw injection payload is written to the audit file verbatim.

**Fix:**
```python
audit_log({
    "event": "prompt_injection_blocked",
    "query_hash": hash_query(req.query),  # always hash, never log raw injection payload
})
```

---

#### M4 — `utils/personality.py` uses PEP 585 generic syntax without `__future__` import
**File:** `utils/personality.py:33,48,54`

`list[str]` at runtime requires Python 3.10+. `from __future__ import annotations` is missing, which would defer evaluation and support Python 3.10.

**Fix:** Add `from __future__ import annotations` to `utils/personality.py`.

---

#### M5 — `test_gate.py` patches `gate.yaml.safe_load` but it runs at module import time
**File:** `tests/test_gate.py:~38`

The patch `patch('gate.yaml.safe_load')` has no effect because `gate.py` loads its config at module level. The test works because `gate.cfg = cfg` is set directly, but the patch is misleading dead code.

**Fix:** Remove `patch('gate.open')` and `patch('gate.yaml.safe_load')` from the test fixture; document that `gate.cfg` direct assignment is the actual mechanism.

---

#### M6 — `os.environ.setdefault('SAFECLAW_CONFIG', ...)` in `test_graph.py` is dead code
**File:** `tests/test_graph.py:57`

No code in the CyClaw codebase reads `SAFECLAW_CONFIG`. This is leftover from a renamed variable.

**Fix:** Remove `os.environ.setdefault('SAFECLAW_CONFIG', str(config_path))`.

---

#### M7 — `retrieval/indexer.py` uses cwd-relative config path in `build_index()`
**File:** `retrieval/indexer.py:~71`

`load_config(config_path='config.yaml')` uses a relative path default. When `cyclaw-index` console script is invoked from a non-repo-root directory, it opens the wrong config.

**Fix:**
```python
_BASE_DIR = Path(__file__).resolve().parent.parent

def load_config(config_path: str = str(_BASE_DIR / "config.yaml")) -> dict:
    ...
```

---

#### M8 — `utils/personality_db.py` return type of `connect()` is untyped
**File:** `utils/personality_db.py:12`

`connect()` returns a 3-tuple `(conn, placeholder_char, backend_name)` with no type annotation. Callers unpack positionally which is fragile.

**Fix:**
```python
from typing import NamedTuple
class DBConnection(NamedTuple):
    conn: Any
    placeholder: str
    backend: str

def connect(...) -> DBConnection:
    ...
```

---

#### M9 — MCP audit events use `'mode'` key; graph uses `'retrieval_mode'` — dual-key smell
**File:** `mcp_hybrid_server.py:~91`

`metrics.py` handles both via `e.get('retrieval_mode') or e.get('mode')`. Normalizing to one key would remove this dual-key special case.

**Fix:** Change `mcp_hybrid_server.py` audit_log call to use `'retrieval_mode'` key.

---

#### M10 — `test_agentic_context.py` mock does not replicate real `run_read` return shape
**File:** `tests/test_agentic_context.py:29`

The mock returns `{'data': {'op': op, ...}}` but real `run_read` returns `{'op': op, 'repo': repo, 'data': <parsed_json>}`. If `context.py` were changed to read top-level `result['repo']`, tests pass but production breaks.

**Fix:** Update `_fake_run_read` to exactly mirror the real `run_read` return contract.

---

### 2.4 LOW Severity Findings

| # | File | Issue |
|---|------|-------|
| L1 | `utils/logger.py:53` | `_config_cache` module-level global can leak between tests if fixture cleanup fails |
| L2 | `gate.py:232` | `TrustedHostMiddleware` import is mid-file (should be with other imports at top) |
| L3 | `gate.py:237` | `IndexNotFoundError` caught narrowly — other exceptions propagate and crash startup |
| L4 | `utils/errors.py:144` | `HealthStatus` dataclass belongs in `utils/health.py`, not `errors.py` |
| L5 | `sync/runner.py:295` | `'--filter-from', cfg.filter_file or ''` passes empty string if `filter_file` is None (dead code guard — always set by `_fill_default_paths`) |
| L6 | `tests/test_config_validation.py` | Missing "required key absent" parametrized test case |
| L7 | `tests/test_rate_limit.py:17` | `import gate` at module level triggers full gate startup — heavy for a rate limiter test |
| L8 | `retrieval/embeddings.py:66` | `get_embeddings_batch` bypasses cache — undocumented intentional design |
| L9 | `tests/test_rag_integration.py:29` | Uses `Path('config.yaml')` relative — fails if pytest is run from non-root directory |

### 2.5 INFO Findings

| # | File | Issue |
|---|------|-------|
| I1 | `CLAUDE.md` | Documents `sanitize_query` (does not exist); actual exports are `check_input` and `sanitize_chunk` |
| I2 | `agentic/config.py:180` | `enabled` stored as dynamic attribute outside dataclass definition — mypy strict flag |
| I3 | `utils/ratelimit.py:110` | Empty-hit IPs vacuously satisfy the eviction predicate — acceptable behavior but undocumented |

---

## Part III — Recent PR Review (Last 3 Days: PRs #218–#225)

### PR #222 — `fix(ratelimit): log + recover from corrupt persisted state`

**Files:** `utils/ratelimit.py` (+13 lines), `tests/test_rate_limit.py` (+31 lines)

**Code Review:** ✅ Well-implemented. The corrupt-state handler logs the affected IP (auditable) and resets to empty list rather than silently swallowing. The `_persist` method correctly writes only the touched IP (O(1) not O(N)). The new test (`test_corrupt_persisted_state_logs_and_recovers`) uses `caplog` correctly.

**Finding:** The `_sweep` eviction check (`all(... for t in hits)`) is vacuously True for the freshly-reset empty list, meaning a corrupt-then-reset IP is evicted on the next sweep. This is acceptable but undocumented.

**Verdict:** Merge-worthy, ship finding L9 as a follow-up comment.

---

### PR #223 — `feat(config): validate retrieval block at startup (fail-fast)`

**Files:** `utils/config_validation.py` (new, 63 lines), `tests/test_config_validation.py` (new, 66 lines), `gate.py` (+6 lines)

**Code Review:** ✅ Clean implementation. `_is_real_number()` correctly excludes `bool` (Python bool is an int subclass). The ConfigError raised at startup is informative. Test coverage is comprehensive (boundary values, wrong types, missing block). The `__future__` import is present.

**Finding (M-low):** Missing a "required key absent" test case (e.g., `retrieval: {min_score: 0.028}` with `top_k_semantic` omitted). Add `@pytest.mark.parametrize("key", ["top_k_semantic", ...]): del cfg["retrieval"][key]; with pytest.raises(ConfigError)`.

**Verdict:** Excellent addition — prevents silent mis-routing from typo'd config values.

---

### PR #225 — `fix: correct agentic registry schema to canonical format`

**Files:** `data/agentic/skills_registry.json`

**Code Review:** ✅ Schema correction from `{"version": 0}` to canonical format. The agentic registry tests now pass this schema validation.

**Verdict:** Correct fix.

---

### PRs #219–#221 — Grok wiring hardening, context tests, registry stub

**Code Review:**

- **PR #219 (Grok wiring):** `grok.is_available()` check before routing to `grok_fallback` prevents the "confirmed but no API key" dead-end. Clean.
- **PR #220 (agentic registry stub):** Repair of `skills_registry.json` — correct.
- **PR #221 (agentic context tests):** 116-line new test file. Mock (`_fake_run_read`) does not accurately replicate the real `run_read` return shape — see finding M10.

---

## Part IV — Security Review

### 4.1 Security Invariant Status

| Invariant | Status | Evidence |
|-----------|--------|---------|
| RAG-First | ✅ PASS | `graph.set_entry_point("retrieve")` — retrieve is the only entry |
| Topology = Policy | ✅ PASS | All routing via `add_conditional_edges` — no LLM decides routing |
| Triple-Gated External | ✅ PASS* | `grok` client only created when `mode=hybrid AND grok.enabled=true`; `is_available()` checks key; user gate checks confirmation |
| Audit Convergence | ✅ PASS | All 6 paths → `audit_logger` → `END`; no shortcut |
| Soul Governance | ⚠️ PARTIAL | `reason` accepted as empty string at schema level (H3 above); no length validation in `apply_evolution()` (H5) |

*Triple-gate is enforced but split between `gate.py` startup (checks 1+2) and graph edge (check 3). The CLAUDE.md invariant says "enforce in graph edges" — in practice, `grok=None` is passed to `build_graph()` when the first two conditions are not met, so the graph can never call Grok. This is correct but architecturally diverges from "graph edges only" — the enforcement is at the injection point (build time), not the call site.

### 4.2 Security Findings

#### SEC-1 (HIGH) — Dockerfile binds to `0.0.0.0:8000`, violating loopback-only invariant
**File:** `Dockerfile:50`

```dockerfile
CMD ["uvicorn", "gate:app", "--host", "0.0.0.0", "--port", "8000", ...]
```

`config.yaml` specifies `api.host: 127.0.0.1` (loopback-only). The Dockerfile CMD overrides this with `0.0.0.0` (all interfaces). Any host that can reach the container's port 8000 can access the API — CORS and TrustedHostMiddleware provide partial protection but were designed for localhost-only access patterns.

**Fix:**
```dockerfile
CMD ["uvicorn", "gate:app", "--host", "127.0.0.1", "--port", "8787", "--log-level", "info"]
```
And update `EXPOSE 8787` (currently `8000`). Or use `--port 8000` and update the config, but align Dockerfile port with config.yaml.

---

#### SEC-2 (HIGH) — `docker-compose.yml` publishes port `0.0.0.0:8000:8000` (all interfaces)
**File:** `docker-compose.yml`

```yaml
ports:
  - "8000:8000"
```

This publishes to all host interfaces. Should be `127.0.0.1:8000:8000` if the service is meant to be loopback-only.

**Fix:**
```yaml
ports:
  - "127.0.0.1:8000:8000"
```

---

#### SEC-3 (HIGH) — `uv:latest` in Dockerfile is an unpinned image — supply chain risk
**File:** `Dockerfile:8`

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
```

`latest` is not pinned to a digest. A compromised `uv:latest` would silently affect all builds.

**Fix:** Pin to a specific digest:
```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.5.22@sha256:<digest> /uv /bin/uv
```

---

#### SEC-4 (MEDIUM) — Rate limiter uses `request.client.host` which does not read `X-Forwarded-For`
**File:** `gate.py:266`

```python
client_ip = request.client.host if request.client else "unknown"
```

On a loopback-only deployment this is fine (all requests come from 127.0.0.1, no proxy). But if a reverse proxy is placed in front of CyClaw in future, all requests would appear to come from the proxy IP, making per-IP rate limiting ineffective.

**Note:** This is NOT a current vulnerability (loopback-only design means no external proxy today), but documenting it prevents a future misconfiguration.

---

#### SEC-5 (MEDIUM) — Corpus indexer directly opens files from `data/corpus/` with no extension filtering at load time
**File:** `retrieval/indexer.py`

The indexer reads all files matching configured extensions, but there is no validation that corpus file contents are safe markdown (no embedded script tags, HTML injection, etc.). Since corpus content is prepended to LLM prompts, a poisoned corpus file could inject instructions into system prompts.

**Mitigation present:** `sanitize_chunk()` is called on each chunk during indexing. However, `sanitize_chunk()` only removes banned patterns that match `policy.prompt_filter.banned_patterns` — it does not sanitize HTML/script content in corpus files.

**Fix:** Add a corpus-level content scan that flags files containing `<script>`, `javascript:`, or other HTML injection vectors before indexing.

---

#### SEC-6 (MEDIUM) — Grok API key logged in error detail if GrokServiceError is raised
**File:** `llm/client.py:176-177`

```python
raise GrokServiceError("GROK_API_KEY not set",
                        details={"required_env": "GROK_API_KEY"})
```

The key itself is not logged, but error detail objects from `RAGError` subclasses are included in audit log events via `audit_logger_node`. If the key were accidentally included in `details`, it would be persisted to `logs/audit.jsonl`. Currently safe, but worth a defensive check.

**Fix:** Audit `utils/errors.py` to ensure `details` dict values are never sanitized (no secret values in error details by convention).

---

#### SEC-7 (MEDIUM) — Soul backup file (`.bak`) created with same permissions as soul.md
**File:** `utils/personality.py:271`

```python
bak_path.write_text(self.soul_core, encoding="utf-8")
```

No explicit permissions are set. The backup is created with the process umask, which on most systems allows group-read. Soul content is identity-defining text that should have minimal permissions.

**Fix:**
```python
import os
bak_path.write_text(self.soul_core, encoding="utf-8")
os.chmod(bak_path, 0o600)  # owner-read/write only
```

---

#### SEC-8 (LOW) — ChromaDB `PersistentClient` settings do not explicitly disable network features beyond telemetry
**File:** `retrieval/hybrid_search.py:~61`

```python
client = chromadb.PersistentClient(
    path=chroma_path,
    settings=Settings(anonymized_telemetry=False)
)
```

CVE-2026-45829 affects ChromaDB's HTTP client mode. The threat model accepts this because `PersistentClient` is used. However, `Settings` does not explicitly disable the HTTP server capability (e.g., `chroma_server_http_port=None`). Future upgrades to chromadb should be reviewed to ensure no HTTP server is inadvertently activated.

---

#### SEC-9 (LOW) — CORS `allow_credentials=False` but `allow_origins` could be overly broad in production
**File:** `gate.py:218-222`

The default `config.yaml` sets `allowed_origins` to `["http://127.0.0.1", "http://localhost", "http://127.0.0.1:8787"]`. This is appropriate for local-only use. If a future deployment adds external origins, the CORS policy would need review (credentials=False provides some protection).

---

#### SEC-10 (LOW) — No timeout on `os.replace()` in `apply_evolution` under concurrent soul applies
**File:** `utils/personality.py:275`

`apply_evolution` acquires `self._lock` (thread-safe) and does `os.replace(tmp_path, soul_path)`. If two concurrent callers both call `apply_evolution` (before/after the lock), the second apply always wins without conflict. The audit DB records both. This is the correct behavior, but a very rapid "propose → apply → apply" sequence from two concurrent HTTP requests could create an audit trail that appears to show two applies where only the second persists.

---

### 4.3 Dependency Security

From `.osv-scanner.toml` and existing `pip-audit.yml` workflow:

| Dependency | Known CVE | Status |
|-----------|-----------|--------|
| `torch==2.6.0+cpu` | CVE-2025-32434 (weights_only bypass) | ✅ Mitigated — 2.6.0 patched |
| `chromadb>=1.5.6` | CVE-2026-45829 (HTTP client pre-auth RCE) | ✅ Accepted — PersistentClient only |
| `pip<26.1.2` | 4 pip CVEs | ✅ CI upgrades pip to >=26.1.2 |

**OSV scanner and pip-audit workflows are present and run on PRs** — good security hygiene.

---

## Part V — Test File Deep-Dive & Recommendations

### 5.1 Test Files with Issues

#### 5.1.1 `tests/ci_rag_smoke.py` — Structural Flaw
**Issue:** Missing `sys.path` setup prevents running as `python tests/ci_rag_smoke.py`.
**Impact:** Developer confusion; CI works but direct invocation fails.
**Fix:** Add at top:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

#### 5.1.2 `tests/test_graph.py` — Dead Code (`SAFECLAW_CONFIG`)
**Issue:** `os.environ.setdefault('SAFECLAW_CONFIG', ...)` references an env var nothing reads.
**Fix:** Remove the setdefault call.

#### 5.1.3 `tests/test_gate.py` — Misleading Patches
**Issue:** `patch('gate.yaml.safe_load')` and `patch('gate.open')` are no-ops because config is loaded at import time.
**Fix:** Remove no-op patches; add comment explaining `gate.cfg = cfg` is the actual mechanism.

#### 5.1.4 `tests/test_agentic_context.py` — Mock Contract Drift
**Issue:** `_fake_run_read` return shape does not match real `run_read` return shape.
**Fix:** Align mock return structure to production contract.

#### 5.1.5 `tests/test_rate_limit.py` — Heavy Import Side Effect
**Issue:** `import gate` at module top fires full gate startup (telemetry kill, config load, ChromaDB init).
**Fix:** Move `import gate` inside the one test that needs it (`test_gate_uses_production_limiter`).

#### 5.1.6 `tests/test_config_validation.py` — Missing Coverage
**Issue:** No test for "individual key absent from retrieval block."
**Fix:** Add parametrized test for missing keys.

#### 5.1.7 `tests/test_rag_integration.py` — Relative Path
**Issue:** `Path('config.yaml')` is relative; fails from non-root pytest invocation.
**Fix:** Use `Path(__file__).resolve().parent.parent / 'config.yaml'`.

#### 5.1.8 `tests/conftest.py` — Empty `audit_file` in base `TEST_CONFIG`
**Issue:** `TEST_CONFIG["logging"]["audit_file"] = ""` causes `IsADirectoryError` when `audit_log` is called without override.
**Fix:** Set to `"/dev/null"` as base default, or add a guard in `audit_log`.

### 5.2 Tests with Good Design Worth Preserving

- **`test_telemetry_kill.py`** — Subprocess-isolated tests for telemetry env vars. This pattern is excellent for gate.py import side effects; preserve it.
- **`test_conftest_fixtures.py`** — Explicitly tests the test infrastructure contracts. Valuable for preventing conftest drift.
- **`test_security.py`** — BM25 pickle RCE test using malicious `__reduce__` payload is a strong regression guard.
- **`test_sanitizer.py::TestShippedConfigContract`** — Tests against the real `config.yaml`; catches pattern regressions on deployment.

### 5.3 Missing Test Coverage Areas (Recommended New Tests)

| Area | Recommended Test | Priority |
|------|-----------------|----------|
| `utils/personality_db.py` Postgres backend | Mock `CYCLAW_DB_URL` env and test `connect()` with `psycopg2` mock | HIGH |
| `hybrid_search` with multi-doc corpus | Build 10-doc index, verify `retrieval_mode=="hybrid"` | HIGH |
| `apply_evolution` with `reason=""` | Assert `ValueError` raised (after H5 fix) | HIGH |
| `SoulEvolutionRequest` with empty reason | Assert HTTP 422 from `/soul` endpoint | MEDIUM |
| `gate.py` — `_BASE_DIR` anchor | Test that `/health` resolves correctly from non-repo-root cwd | MEDIUM |
| Grok triple-gate: mode=offline + grok.enabled=true | Assert `grok=None` is passed to `build_graph()` | MEDIUM |
| `sync/selftest.py` lines 61-143 | Increase from 71% to ≥80% | LOW |

### 5.4 Overall Test Suite Health

The test suite has matured significantly from the June-16 state documented in `tests/TEST_SUITE_AUDIT.md` (which recorded 20 failed, 3 collection errors). As of June-24:

- **418 tests pass** (up from ~66 passing)
- **0 failures**
- **88.27% coverage** (target: 80%)
- The `--cov` flag issue in `pyproject.toml` must be fixed (see §5.5)

### 5.5 Coverage Tool Fix Required

**`pyproject.toml` `addopts = "-ra -q --cov"` generates false 0% coverage.**

When `--cov` is passed without a path argument, pytest-cov collects no data and reports 0%. The `fail_under=80` then triggers a failure. This is why CI uses explicit `--cov=<module>` flags in the workflow.

**Fix:**
```toml
[tool.pytest.ini_options]
addopts = "-ra -q --cov=. --cov-config=pyproject.toml"
```
Or remove `--cov` from addopts entirely and rely on CI's explicit flags. The current state means running `pytest tests/` locally always produces a false coverage failure.

---

## Part VI — Recommendations Summary (Prioritized)

### Immediate Actions (High Priority)

1. **Fix Dockerfile binding** — Change `--host 0.0.0.0` to `--host 127.0.0.1` and align port with `config.yaml`
2. **Fix docker-compose.yml** — Change `"8000:8000"` to `"127.0.0.1:8000:8000"`
3. **Add reason validation** — `SoulEvolutionRequest.reason = Field(min_length=1)` and guard in `apply_evolution()`
4. **Fix `pyproject.toml` coverage** — Change addopts to use `--cov=.` or remove `--cov` from addopts
5. **Fix `ci_rag_smoke.py` sys.path** — Add PYTHONPATH bootstrap at top of file
6. **Fix `check_input()` call in gate.py** — Pass explicit config path using `_BASE_DIR`

### Short-Term Improvements (Medium Priority)

7. Pin `uv:latest` Docker image to a specific digest
8. Add `min_length=1, max_length=65536` to `SoulEvolutionRequest.new_soul`
9. Fix MCP audit event to use `retrieval_mode` key (not `mode`) for metrics consistency
10. Move `TrustedHostMiddleware` import to file top
11. Remove dead `SAFECLAW_CONFIG` env var setdefault from `test_graph.py`
12. Update CLAUDE.md: replace `sanitize_query` with `check_input`/`sanitize_chunk`
13. Add `from __future__ import annotations` to `utils/personality.py`
14. Add debug logging in `hybrid_search` when BM25 leg is empty

### Test Suite (Next Sprint)

15. Add Postgres backend coverage for `utils/personality_db.py` (currently 64%)
16. Add multi-document corpus test to verify `retrieval_mode=="hybrid"`
17. Fix relative paths in `test_rag_integration.py` and `ci_rag_smoke.py`
18. Remove no-op patches from `test_gate.py` fixture
19. Add "missing key" parametrized test to `test_config_validation.py`
20. Move `import gate` inside test function in `test_rate_limit.py`

---

## Part VII — Recent Change Impact Assessment

| Change | Risk | Assessment |
|--------|------|-----------|
| `utils/config_validation.py` (PR #223) | Low | Clean fail-fast validator; good coverage |
| `utils/ratelimit.py` corrupt state fix (PR #222) | Low | Correct recovery with audit trail |
| `gate.py` `_BASE_DIR` anchor | Low | Fixes cwd fragility for Windows |
| `agentic/registry.py` schema fix (PR #225) | Low | Correct stub format |
| `tests/test_agentic_context.py` (PR #221) | Medium | Mock shape doesn't match production contract |
| README.md updates (PRs #226-227) | Info | Documentation only |

---

## Appendix A — Full pytest Output (Summary)

```
418 passed, 1 warning in 36.56s

Warnings:
  starlette/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with
  `starlette.testclient` is deprecated; install `httpx2` instead.
```

## Appendix B — Full RAG Smoke Output

```
=== Real Offline RAG Query Smoke (ChromaDB + BM25 + RRF) ===
Configured min_score gate: 0.028

[1/4] Query: What fusion method does CyClaw use to blend semantic and keyword results?
  Top source: data/corpus/cyclaw_overview.md
  Top score:  0.525083 (gate: 0.028)
  Mode:       semantic
  PASS: vault hit above gate, correct source

[2/4] Query: How does CyClaw combine ChromaDB vector embeddings with BM25 keyword search?
  Top source: data/corpus/cyclaw_overview.md
  Top score:  0.539403 (gate: 0.028)
  Mode:       semantic
  PASS: vault hit above gate, correct source

[3/4] Query: What does CyClaw use for rate limiting to protect against DoS attacks?
  Top source: data/corpus/cyclaw_overview.md
  Top score:  0.354752 (gate: 0.028)
  Mode:       semantic
  PASS: vault hit above gate, correct source

[4/4] Query: How does CyClaw deploy and run local LLM inference offline?
  Top source: data/corpus/cyclaw_overview.md
  Top score:  0.255164 (gate: 0.028)
  Mode:       semantic
  PASS: vault hit above gate, correct source

All 4 real RAG queries passed (vault hits above the 0.028 gate)
```

## Appendix C — metrics.py Output

```
Total events: 48

Event breakdown:
  rag_query: 26
  mcp_rag_query: 12
  user_gate_pause: 6
  soul_drift_detected: 3
  prompt_injection_blocked: 1

RAG scores — avg: 0.650, min: 0.300, max: 0.920

Retrieval modes:
  hybrid: 30
  semantic: 5
  keyword: 3
```

## Appendix D — Security Findings Summary

| ID | Severity | Title |
|----|----------|-------|
| SEC-1 | HIGH | Dockerfile binds to 0.0.0.0 — violates loopback-only invariant |
| SEC-2 | HIGH | docker-compose publishes to all host interfaces |
| SEC-3 | HIGH | `uv:latest` unpinned in Dockerfile — supply chain risk |
| SEC-4 | MEDIUM | Rate limiter ignores X-Forwarded-For (future proxy risk) |
| SEC-5 | MEDIUM | Corpus indexer does not sanitize HTML/script in corpus files |
| SEC-6 | MEDIUM | Grok API key could be included in error detail (defensive concern) |
| SEC-7 | MEDIUM | Soul backup file created with default (group-readable) permissions |
| SEC-8 | LOW | ChromaDB Settings does not explicitly disable HTTP server capability |
| SEC-9 | LOW | CORS `allowed_origins` acceptable today but needs review if externalized |
| SEC-10 | LOW | Concurrent `apply_evolution` race: second caller wins silently |

---

*Report generated by Claude Code automated audit — 2026-06-24*
*Repository: cgfixit/cyclaw | Branch: main | Commit: 6149dcf*
