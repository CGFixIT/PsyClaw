# CyClaw Code & Security Review — 2026-06-20

**Scope:** All code files on `main` branch (commit 611118d) + recent PRs #49–#52  
**Analyst:** Claude (automated session)  
**Runtime verified:** Python 3.12.3 — 98 tests passed  
**References:** `CyClaw_Architecture_v1.3.0.pdf` (security invariants), OWASP Top 10  

**2026-07-27 reconciliation:** re-checked every P0/P1/P2 item below against current
`main`, over a month and hundreds of commits later. The large majority (rate limiter
lock, ReDoS module, config-path anchoring via `_BASE_DIR`/`_anchor`, soul-preamble
sanitization, soul-endpoint rate limiting, SQLite isolation, score clamping, `top_k`
bound, router `state.get()` guards, error-message leak removal) were already fixed by
later work, most traceable to a documented "PR #99" cleanup pass. Two items were
genuinely still open and are now fixed (see inline notes at "No Maximum Request Body
Size" and "Audit Write Failure Crashes Queries" below). One P2 item — "Maintenance on
`__init__` Blocks Startup" (`utils/personality.py`, still runs `self.maintenance()`
synchronously) — remains open: confirmed still true, but it's a startup-latency
polish item, not a bug fix or security hardening recommendation, so it's out of scope
for this pass rather than silently dropped. The rest of this doc is historical
record, not a live task list.

---

## Recent PRs Reviewed

| PR | Title | Status |
|---|---|---|
| #52 | DevSkim suppression conflict resolution | Merged |
| #51 | Fix DevSkim rule ID in PowerShell test | Merged |
| #49 | Dependabot pip updates (22 packages) | Merged |
| #48 | PsyClaw → CyClaw rename | Merged |

### PR #49 — Dependabot bump (22 packages)

**Review finding — MEDIUM:**  
`torch==2.6.0+cpu` is now pinned in `requirements.txt`. The Dependabot PR bumped it from an older minor version. No issues with the update itself, but:
- `torch` is a 500MB+ dependency for a text-only RAG server that only uses `torch.no_grad()` for BM25 tokenization.
- **Recommendation:** Replace torch dependency with `rank_bm25` or `bm25s` package. This would reduce install size by ~500MB and eliminate the PyTorch supply chain risk surface.

**Review finding — LOW:**  
`langchain` and `langgraph` both bumped. These packages have a history of API-breaking changes between minor versions. The test suite covers the integration surface (`test_graph.py`, `test_gate.py`) so regressions would be caught, but dependency pinning should use `==` not `>=` across the board.

### PRs #51, #52 — DevSkim suppressions

**Review finding — LOW:**  
Inline suppression comments (`# DevSkim: ignore DS162092,DS137138`) are used to suppress "localhost URL" and "hardcoded IP" alerts for `127.0.0.1:8787`. This is correct — the server only binds to loopback. However:
- Suppression reason is not documented inline (DevSkim supports `# DevSkim: ignore DS162092 — reason`)
- Consider adding reason text so future reviewers understand the suppression intent:
  ```python
  # DevSkim: ignore DS162092 — loopback-only by design; api.host in config.yaml controls bind address
  ```

---

## Code Review Findings by File

### `gate.py` — Severity: HIGH

#### 1. Rate Limiter Race Condition (lines ~112–121)

```python
recent = [t for t in _rate_limits[client_ip] if now - t < RATE_LIMIT_WINDOW]
if len(recent) >= RATE_LIMIT_REQUESTS:
    _rate_limits[client_ip] = recent  # ← stored WITHOUT current timestamp
    return False
recent.append(now)
_rate_limits[client_ip] = recent
```

**Problem:** When limit is exceeded, the current request's timestamp is NOT stored. On the next request, the sliding window recalculates from the stored list which still only has 60 entries — so it may allow a 61st request under certain timing conditions.

**Additionally:** `_rate_limits` dict is shared state accessed from multiple concurrent request handlers with no lock. Under load, two requests from the same IP can simultaneously pass the `len(recent) >= 60` check before either appends.

**Fix:**
```python
_rate_limit_lock = threading.Lock()

def check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    with _rate_limit_lock:
        recent = [t for t in _rate_limits.get(client_ip, []) if now - t < RATE_LIMIT_WINDOW]
        recent.append(now)  # Always append BEFORE the check
        _rate_limits[client_ip] = recent
        if len(recent) > RATE_LIMIT_REQUESTS:
            return False
    return True
```

**Claude Code task:** Apply the fix above to `gate.py` (or to the extracted `utils/ratelimit.py` per Test Suite PR recommendations). Add `threading` import if not present.

#### 2. Config Path Fragility (line ~151)

```python
with open("config.yaml") as f:
    cfg = yaml.safe_load(f)
```

**Problem:** Assumes CWD is repo root. Fails if gate.py is imported from a different directory or run as a module from a subdirectory.

**Fix:**
```python
_CONFIG_PATH = Path(__file__).parent / "config.yaml"

with open(_CONFIG_PATH) as f:
    cfg = yaml.safe_load(f)
```

**Claude Code task:** Replace all `open("config.yaml")` occurrences in `gate.py` with `open(Path(__file__).parent / "config.yaml")`. Check `utils/logger.py`, `utils/sanitizer.py` for the same pattern and fix there too.

#### 3. No Maximum Request Body Size — RESOLVED (2026-07-27)

**Fixed differently than sketched below:** `gate.py`'s `_MaxBodySizeMiddleware` uses a
config-driven cap (`config.yaml` `security.max_request_body_bytes`, shipped at 1 MiB)
rather than a hardcoded 64 KB, sized with headroom over the largest real Pydantic field
(`schemas/api.py`'s 65536-char fields, which can exceed 64 KB on the wire under
multi-byte UTF-8). Pinned by `tests/test_gate.py::TestMaxBodySize`. Placed between
`TrustedHostMiddleware` and `_SecurityHeadersMiddleware` in the stack so the latter
(outermost) still stamps its headers on a 413 rejection, same as it already does for
TrustedHost's 400. Enforced via the client-declared `Content-Length` header only — a
client that lies about it or uses chunked transfer-encoding without one isn't covered,
an accepted gap under this project's single-operator, loopback-bound threat model.

**Problem:** FastAPI has no built-in body size limit. A malicious client could send a 100MB JSON body to `/query` or `/soul/propose`. The body is buffered before `check_input()` is called.

**Fix — add ASGI middleware:**
```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int = 65_536):  # 64 KB default
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request, call_next):
        if request.headers.get("content-length"):
            if int(request.headers["content-length"]) > self.max_bytes:
                return Response("Request too large", status_code=413)
        return await call_next(request)

app.add_middleware(MaxBodySizeMiddleware, max_bytes=65_536)
```

**Claude Code task:** Add the middleware above to `gate.py`. Add a test `test_oversized_body_returns_413` in `tests/test_gate.py`.

#### 4. Soul Mutation Endpoints Missing Rate Limit

**Problem:** `/soul/propose`, `/soul/apply`, `/soul/reload` have no rate limiting. An authenticated attacker with the API key could flood these endpoints, causing high CPU (OWASP-proposed DoS).

**Fix:** Apply `check_rate_limit()` to soul endpoints the same way it's applied to `/query`.

**Claude Code task:** In `gate.py`, add `check_rate_limit(client_ip)` check to all three soul mutation endpoints before any processing.

---

### `utils/sanitizer.py` — Severity: HIGH (ReDoS)

#### ReDoS Vulnerability (line ~43)

```python
patterns = tuple(re.compile(p, re.IGNORECASE) for p in pf.get("banned_patterns", []))
```

**Problem:** If config.yaml contains a pathological regex pattern (e.g., `(a+)+b`, `(x*)+y`, `(\w+\s+)+end`), it will cause catastrophic backtracking on long inputs. The server would hang for seconds-to-minutes per request, creating a DoS vector. The config file is admin-controlled, but supply-chain attacks on config or misconfiguration are real risks.

**Fix option A — use `regex` module with timeout:**
```python
import regex  # pip install regex

def _safe_compile(pattern: str) -> "regex.Pattern":
    try:
        return regex.compile(pattern, regex.IGNORECASE | regex.TIMEOUT)
    except regex.error as e:
        logger.warning("Invalid banned_pattern %r skipped: %s", pattern, e)
        return None
```

**Fix option B — validate patterns at config load time:**
```python
REDOS_DANGEROUS = re.compile(r'(\(.+\)[+*]){2,}|(\[.+\][+*]){2,}')

def _validate_pattern(p: str) -> bool:
    if REDOS_DANGEROUS.search(p):
        logger.warning("Potentially dangerous regex skipped: %r", p)
        return False
    return True
```

**Fix option C — use literal string matching instead of regex:**
For simple banned phrases (most real-world cases), `str.lower().find()` is safer and faster than regex.

**Claude Code task:** Implement Fix option A (install `regex` package, update `requirements.txt`, and use `regex.compile` with timeout in `_load_filter()`). Add a test in `tests/test_sanitizer.py` verifying a known ReDoS pattern does not hang with `@pytest.mark.timeout(2)`.

#### Error Message Pattern Leak (line ~76)

```python
raise PromptInjectionError(..., details={"matched_pattern": pattern.pattern})
```

**Problem:** The matched pattern's full regex string is included in the error response sent to the client. If the pattern encodes business logic (e.g., `(confidential|proprietary|secret)`) this reveals what the filter is looking for.

**Fix:** Remove `matched_pattern` from client-facing details. Log it server-side:
```python
logger.debug("Injection blocked by pattern %r for input: %s", pattern.pattern, query[:100])
raise PromptInjectionError(query, details={"reason": "banned pattern matched"})
```

**Claude Code task:** In `utils/sanitizer.py`, change the `PromptInjectionError` raise to not include the pattern string in `details`. Add server-side `logger.debug()` call instead.

---

### `utils/logger.py` — Severity: HIGH

#### Audit Write Failure Crashes Queries (line ~96) — RESOLVED (2026-07-27)

`utils/logger.py`'s `audit_log()` now wraps the write in `try/except OSError`, logging
`logger.warning("audit_log write failed for %s: %s", ...)` instead of letting the
exception propagate. Pinned by `tests/test_logger.py::TestAuditLogWriteFailure`. This
matters specifically because `audit_logger` is the unconditional terminal node every
graph path converges on (invariant I4), running *after* the answer is already computed
— a disk/permission failure there must never turn an already-good response into a 500.

```python
with open(log_path, "a") as f:
    f.write(json.dumps(event) + "\n")
```

**Problem:** If the audit log directory is removed or permissions change, this raises an unhandled `OSError`, which propagates up through `audit_logger_node` and crashes the query with a 500 error. Audit failure should not break the user-facing query.

**Fix:**
```python
try:
    with open(log_path, "a") as f:
        f.write(json.dumps(event) + "\n")
except OSError as e:
    logger.error("Audit log write failed (non-fatal): %s — event: %s", e, json.dumps(event))
```

**Claude Code task:** Wrap the `open(log_path, "a")` block in `audit_log()` with a try/except OSError. Add a test that patches `builtins.open` to raise OSError and verifies the `/query` endpoint still returns 200 (or appropriate response).

#### Config Cache TOCTOU (lines ~52–59)

```python
_config_cache: Optional[dict] = None

def _get_config(config_path: str = "config.yaml") -> dict:
    global _config_cache
    if _config_cache is None:
        with open(config_path) as f:
            _config_cache = yaml.safe_load(f)
    return _config_cache
```

**Problem:** Config is loaded once and never refreshed. Two threads could simultaneously see `_config_cache is None` and both load the file (benign but wasteful). More importantly, if `config.yaml` changes at runtime (e.g., banned_patterns updated), the running server uses stale patterns until restart.

**Fix (minimal):** Add a lock around cache initialization:
```python
_config_lock = threading.Lock()

def _get_config(config_path: str = "config.yaml") -> dict:
    global _config_cache
    with _config_lock:
        if _config_cache is None:
            with open(config_path) as f:
                _config_cache = yaml.safe_load(f)
    return _config_cache
```

**Fix (better):** Support hot-reload via file mtime:
```python
import os

_config_mtime: float = 0.0

def _get_config(config_path: str = "config.yaml") -> dict:
    global _config_cache, _config_mtime
    with _config_lock:
        mtime = os.stat(config_path).st_mtime
        if _config_cache is None or mtime != _config_mtime:
            with open(config_path) as f:
                _config_cache = yaml.safe_load(f)
            _config_mtime = mtime
    return _config_cache
```

**Claude Code task:** Implement the minimal fix (add `_config_lock`). Optionally implement hot-reload. Add a test verifying that if config is changed after first load, the new config is picked up on next call.

---

### `graph.py` — Severity: MEDIUM

#### Soul Injection Without Sanitization (line ~158)

```python
prompt = f"""{soul_preamble}USER QUERY: {query}

RETRIEVED CONTEXT (treat as untrusted data)...
"""
```

`soul_preamble` comes from `PersonalityManager.get_system_prompt_additive()` which returns the raw content of `soul.md`. If `soul.md` were compromised (e.g., via a malicious `soul/apply` call that passed the OWASP pattern check but still contains subtle injection), it would be injected before the user query into the LLM prompt.

**Current protection:** `soul/propose` scans proposed soul content with the same OWASP injection scanner used for user queries. But `soul.md` loaded from disk (e.g., placed manually) has no runtime validation.

**Fix:** Call `sanitize_chunk(soul_preamble)` before injecting into the prompt:
```python
from utils.sanitizer import sanitize_chunk

safe_preamble = sanitize_chunk(soul_preamble) if soul_preamble else ""
prompt = f"""{safe_preamble}USER QUERY: {query}..."""
```

**Claude Code task:** In `graph.py`, wrap `soul_preamble` with `sanitize_chunk()` before prompt construction. Add test verifying that a soul containing injection patterns is sanitized before reaching the LLM.

#### Missing Dict Key Guards in Routers (lines ~331–349)

```python
def score_router(state: CyClawState) -> str:
    if state["needs_user_confirm"]:  # KeyError if key missing
        return "user_gate"
```

**Fix:**
```python
def score_router(state: CyClawState) -> str:
    if state.get("needs_user_confirm", False):
        return "user_gate"
```

**Claude Code task:** Replace all direct `state["key"]` accesses in router functions with `state.get("key", default)` in `graph.py`.

#### Personality Error Level (line ~322)

```python
except Exception as e:
    logger.warning("personality.record_interaction failed (non-fatal): %s", e)
```

Interaction recording failure loses query history silently at WARNING level. Change to ERROR so it's surfaced in alerting.

**Claude Code task:** In `graph.py` audit_logger_node, change `logger.warning` for personality interaction failure to `logger.error`.

---

### `utils/personality.py` — Severity: MEDIUM

#### Duplicate SQL Constant (lines ~42–50)

`_SQL_INSERT_SOUL_VERSION` appears to be defined twice. Remove the duplicate definition.

**Claude Code task:** Search for duplicate SQL constants in `utils/personality.py` and remove the duplicate.

#### No SQLite Transaction Isolation (line ~68)

```python
self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
```

Default SQLite isolation level is `DEFERRED`, which can cause `SQLITE_BUSY` errors under concurrent writes (two requests both try to write interactions at the same time).

**Fix:**
```python
self.conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level="IMMEDIATE")
```

**Claude Code task:** Add `isolation_level="IMMEDIATE"` to the sqlite3 connect call.

#### Maintenance on `__init__` Blocks Startup

`maintenance(ttl_days)` runs a DELETE SQL query on every PersonalityManager instantiation. For large DBs with many interactions, this adds startup latency.

**Fix:** Run maintenance in a background thread:
```python
threading.Thread(target=self.maintenance, kwargs={"ttl_days": ttl_days}, daemon=True).start()
```

**Claude Code task:** In `utils/personality.py` `__init__`, move the `self.maintenance()` call to a daemon thread.

---

### `retrieval/hybrid_search.py` — Severity: MEDIUM

#### Distance Score Not Clamped to [0, 1] (line ~96)

```python
score = 1 - results["distances"][0][i]
```

ChromaDB's default distance metric (L2 squared) is unbounded — distances can be > 1, making scores negative. The `min_score` gate in config.yaml (typically `0.3`) would never trigger for negative scores, but the behavior is confusing.

**Fix:**
```python
score = max(0.0, min(1.0, 1 - results["distances"][0][i]))
```

**Claude Code task:** Apply score clamping in `retrieval/hybrid_search.py`. Add a test verifying that a very high distance value produces score=0.0 rather than a negative value.

#### No `top_k` Upper Bound

If MCP client passes `top_k=100000`, the retriever attempts to return 100,000 results from ChromaDB, which would allocate significant memory and slow the system.

**Fix:**
```python
MAX_TOP_K = 50

def hybrid_search(self, query: str, top_k: int = 5) -> list[SearchResult]:
    top_k = min(top_k, MAX_TOP_K)
    ...
```

**Claude Code task:** Add a `MAX_TOP_K = 50` constant and clamp `top_k` in `hybrid_search()`. Add test verifying `top_k=1000` is clamped to `MAX_TOP_K`.

---

### `mcp_hybrid_server.py` — Severity: LOW

#### Tool Name Not Validated as String

```python
tool_name = msg.get("params", {}).get("name")
if tool_name == "hybrid_search":
```

If `name` is missing or not a string, the comparison silently falls through to the `-32601` error. Low risk but should validate:
```python
tool_name = msg.get("params", {}).get("name", "")
if not isinstance(tool_name, str):
    return _error(msg_id, -32602, "params.name must be a string")
```

**Claude Code task:** Add string type check on `tool_name` in `mcp_hybrid_server.py`.

---

## Security Invariants Check (per Architecture PDF)

The CyClaw architecture document (v1.3.0) defines these security invariants. Status against current main:

| Invariant | Status | Notes |
|---|---|---|
| No LLM from MCP layer | ✅ PASS | `sampling: None` enforced, test_mcp_server covers it |
| Telemetry kill before LangChain import | ✅ PASS | Verified by gate_runtime_check.py and test_telemetry_kill.py |
| User query sanitized before graph entry | ✅ PASS | check_input() called in gate.py before graph.invoke() |
| Retrieved context marked as untrusted | ✅ PASS | Prompt template includes "RETRIEVED CONTEXT (treat as untrusted)" |
| Soul mutations require auth | ✅ PASS | Bearer token on /soul/propose, /soul/apply, /soul/reload |
| BM25 loaded from JSON, not pickle | ✅ PASS | RCE protection verified by test_security.py |
| Audit log on every query | ✅ PASS | audit_logger_node is terminal node in all graph paths |
| Rate limiting on /query | ✅ PASS | check_rate_limit() called, but has race condition (see above) |
| Soul evolution with drift detection | ✅ PASS | SHA-256 checksums on load and after apply |
| Personality interaction TTL pruning | ✅ PASS | maintenance() called on PersonalityManager init |

**Invariant VIOLATIONS found:**

| Violation | Severity | File | Fix |
|---|---|---|---|
| Rate limiter race condition | HIGH | gate.py | Add threading.Lock |
| ReDoS in sanitizer | HIGH | utils/sanitizer.py | Use regex module with timeout |
| Audit write unprotected | HIGH | utils/logger.py | Add try/except OSError |
| Soul preamble not sanitized before LLM | MEDIUM | graph.py | Call sanitize_chunk() on soul_preamble |
| Config TOCTOU | MEDIUM | utils/logger.py | Add threading.Lock to cache |
| No request body size limit | MEDIUM | gate.py | Add MaxBodySizeMiddleware |
| Soul endpoints not rate limited | MEDIUM | gate.py | Add check_rate_limit() to soul endpoints |
| SQLite no IMMEDIATE isolation | MEDIUM | utils/personality.py | Set isolation_level="IMMEDIATE" |

---

## Dependency Security Notes

From PR #49 Dependabot bumps:

| Package | Concern | Action |
|---|---|---|
| `torch==2.6.0+cpu` | 500MB dependency for text-only RAG | Consider replacing with `bm25s` |
| `langchain` | Frequent breaking changes | Pin to exact version |
| `chromadb` | Active development, API can change | Pin to exact version |
| `pydantic` | Used for request validation — security-critical | Already pinned, keep pinned |
| `fastapi` | API framework — security-critical | Already pinned, keep pinned |

---

## Summary: All Findings by Severity

### P0 — Fix Immediately (Security/Correctness)

1. **Rate limiter race condition** — `gate.py` — add `threading.Lock`, fix append-before-check logic
2. **ReDoS in sanitizer** — `utils/sanitizer.py` — use `regex` module with timeout or validate patterns
3. **Audit write unprotected** — `utils/logger.py` — wrap in try/except OSError
4. **Config cache TOCTOU** — `utils/logger.py` — add `threading.Lock`
5. **Soul preamble not sanitized** — `graph.py` — call `sanitize_chunk()` before prompt construction

### P1 — Fix Soon (Code Quality/Robustness)

6. **No request body size limit** — `gate.py` — add MaxBodySizeMiddleware
7. **Soul endpoints not rate limited** — `gate.py` — add check_rate_limit() calls
8. **SQLite isolation level** — `utils/personality.py` — add `isolation_level="IMMEDIATE"`
9. **Score not clamped** — `retrieval/hybrid_search.py` — clamp to [0, 1]
10. **top_k not bounded** — `retrieval/hybrid_search.py` — add MAX_TOP_K cap
11. **State key guards missing** — `graph.py` — use `state.get()` in routers
12. **Config path fragile** — `gate.py`, `utils/logger.py`, `utils/sanitizer.py` — use `Path(__file__).parent`
13. **Error message leaks pattern** — `utils/sanitizer.py` — remove pattern from client-facing error
14. **Personality log level** — `graph.py` — upgrade `warning` to `error` for interaction failure

### P2 — Polish (Low Risk)

15. **Duplicate SQL constant** — `utils/personality.py` — remove duplicate
16. **Maintenance blocks startup** — `utils/personality.py` — move to daemon thread
17. **Custom stems hardcoded** — `retrieval/stemmer.py` — consider config-driven stems
18. **DevSkim suppression reason** — `apipsTest.ps1`, `verify.sh` — add inline reason text
19. **tool_name type check** — `mcp_hybrid_server.py` — validate string before comparison
20. **metrics.py score validation** — `metrics.py` — validate top_score is numeric

---

## How Claude Code Should Implement

**Session entry point:**
1. Read this file top to bottom.
2. Address P0 items first. After each fix, run `GROK_API_KEY=dummy pytest tests/ -q --tb=short`.
3. For each P0 fix, verify that a test (new or existing) covers the fixed behavior.
4. Address P1 items after P0 is clean.
5. For each new test added, confirm it fails without the fix and passes with it.
6. Push to feature branch and update this PR.

**Reference for Claude Code:**
- Architecture PDF: `docs/CyClaw_Architecture_v1.3.0.pdf`
- Config schema: `config.yaml`
- Test infrastructure: `tests/conftest.py`
- Run tests: `GROK_API_KEY=dummy pytest tests/ -q --tb=short`
