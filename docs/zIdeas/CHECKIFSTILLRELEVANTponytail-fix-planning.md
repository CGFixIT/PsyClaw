# Ponytail Review — Planning & Findings

**Date:** 2026-06-29
**Branch reviewed:** `origin/main` @ `edc1d5b`
**Verdict:** FAIL — 8 violations across 6 production files

---

## Summary

A full ponytail review (YAGNI / stdlib-first / minimal-abstraction / no-dead-code / no-speculative-generality / correctness-over-cleverness / no-half-measures) was applied to all 17 production Python files on the CyClaw main branch. No Rule 2 (stdlib-first) violations were found — all third-party deps are justified. Eight violations were identified across six other rules.

---

## Violations

### 1. `llm/client.py` — Unused context-manager protocol

**Rules broken:** YAGNI (Rule 1)

**Location:** Lines ~208–212 (`LocalLLMClient.__enter__`/`__exit__`) and ~269–272 (`GrokClient.__enter__`/`__exit__`)

**Finding:** Both LLM clients implement `__enter__`/`__exit__`, but no production code uses them as context managers. `gate.py` instantiates clients directly and calls `.close()` in the lifespan hook. Tests also don't use the `with` form.

**Suggested fix:** Remove `__enter__` and `__exit__` from both classes. If context-manager support is ever needed, add it when a caller exists.

**Risk:** Low — removing unused methods; no behavioral change.

---

### 2. `utils/health.py`:64–71 — `require_healthy()` is a half-measure with no caller

**Rules broken:** YAGNI (Rule 1), No half-measures (Rule 7)

**Location:** Lines 64–71

**Finding:** `require_healthy()` has no production caller — only test files call it. Additionally, it only raises for `lm_studio` being unhealthy, silently ignoring `grok_api` and `embeddings_local`. A function named "require healthy" that only checks one of three services does not fulfill its contract.

**Suggested fix (option A — preferred):** Delete `require_healthy()` entirely. Tests that call it should use `check_all()` directly.

**Suggested fix (option B):** If retained, make it raise for any unhealthy service — not just `lm_studio` — and add a production caller. Otherwise it is dead code with a misleading name.

**Risk:** Low — function has no production callers; tests need a one-line update.

---

### 3. `retrieval/hybrid_search.py`:40 — `SearchResult.provenance` field never read

**Rules broken:** YAGNI (Rule 1), No speculative generality (Rule 5)

**Location:** Line 40 (field definition), lines 91, 123, 230 (population sites)

**Finding:** The `provenance: dict` field on `SearchResult` is populated on every retrieval hit with metadata about which search leg (semantic/keyword) found it and the raw score. However, no production path in `gate.py`, `graph.py`, or `mcp_hybrid_server.py` reads `.provenance`. Only `tests/test_hybrid_search.py` accesses it.

**Suggested fix:** Remove the `provenance` field and the four lines that populate it. If debugging metadata is needed later, add it when a consumer exists.

**Risk:** Low — only test assertions reference it; no production behavior change.

---

### 4. `utils/personality.py`:199–248 — Duplicate pattern-builder functions

**Rules broken:** Minimal abstraction (Rule 3)

**Location:** Lines 199–224 (`_build_enforced_patterns`) and lines 226–248 (`_build_advisory_patterns`)

**Finding:** These two functions are structurally identical: both iterate `sources`, call `re.compile`, skip on `re.error`, and return `list[tuple]`. The only difference is the starting list (`ENFORCED_SOUL_PATTERNS` vs `OWASP_INJECTION_PATTERNS`). Each has one call site.

**Suggested fix:** Collapse into a single `_build_patterns(base: list[str]) -> list[tuple[re.Pattern, str]]` function, called twice with the appropriate base list.

**Risk:** Low — pure refactor; behavior unchanged.

---

### 5. `gate.py`:56 — Unused `_EXPECTED` variable

**Rules broken:** No dead code (Rule 4)

**Location:** Line 56

**Finding:** `_EXPECTED = list(_TELEMETRY_KILL.keys())` is assigned but never referenced after the immediately following dict iteration on line 57.

**Suggested fix:** Delete line 56. The `for` loop on line 57 iterates the dict directly.

**Risk:** None — unused intermediate variable.

---

### 6. `utils/personality.py`:347 — Dead `reload_soul` alias

**Rules broken:** No dead code (Rule 4)

**Location:** Line 347

**Finding:** `reload_soul = reload` is a method alias preserved from an older API. `gate.py` already calls `.reload()` directly. Only one test uses the alias.

**Suggested fix:** Delete the alias. Update the single test to call `.reload()` instead.

**Risk:** None — one test update required.

---

### 7. `utils/ratelimit.py` — Speculative Postgres backend

**Rules broken:** No speculative generality (Rule 5)

**Location:** Lines ~56–63, 73–104, 107–137, 140–146, 148–170, 183–189

**Finding:** The rate-limiter has a full Postgres persistence backend (~60 lines of code including backend-switching plumbing). The module docstring itself states that Redis is the recommended target for multi-instance rate limiting and warns that "a Postgres round-trip per persisted request is heavier than a local sqlite write." The sqlite path is the real default for the loopback-only, single-process deployment CyClaw currently targets.

**Suggested fix (option A — conservative):** Keep the code but add a comment block documenting why the Postgres backend exists despite the doc discouraging it (e.g., "available for single-Postgres deployments that don't want Redis; not recommended for high-throughput").

**Suggested fix (option B — ponytail-strict):** Remove the Postgres rate-limit backend entirely. Re-add it when a production deployment actually needs it.

**Risk:** Medium — Option B removes functional (if unused) code. Option A is the safer path.

---

### 8. `gate.py`:149–155 — Silent `CYCLAW_DB_URL` fallback for rate-limiter

**Rules broken:** Correctness over cleverness (Rule 6)

**Location:** Lines 149–155

**Finding:** The three-tier resolution chain for `RATE_LIMIT_DB_URL` (`config → CYCLAW_RATELIMIT_DB_URL → CYCLAW_DB_URL`) silently inherits the personality DB URL when no explicit rate-limit URL is set. An operator who sets `CYCLAW_DB_URL` for the soul database unknowingly opts into Postgres rate-limit persistence as a side effect. This coupling is undocumented in `config.yaml`.

**Suggested fix (option A — preferred):** Remove the `CYCLAW_DB_URL` fallback from the rate-limiter resolution chain. The rate-limiter should require its own explicit URL (`CYCLAW_RATELIMIT_DB_URL` or a `config.yaml` key) to use Postgres.

**Suggested fix (option B):** Keep the fallback but document it prominently in `config.yaml` under the rate-limit section and log a warning at startup when the fallback activates.

**Risk:** Low–Medium — Option A may break an undocumented workflow if anyone relies on the silent coupling (unlikely given the docstring discouraging Postgres rate limiting).

---

### Supplementary: `mcp_hybrid_server.py`:111–131 — Inconsistent notification handling

**Rules broken:** No half-measures (Rule 7) (minor)

**Location:** Lines 129–131

**Finding:** `notifications/initialized` is silently swallowed (returns `None`), while all other unknown notification types hit the "Unknown method" error path. No comment explains why this one notification is special.

**Suggested fix:** Add a one-line comment explaining that `notifications/initialized` is a lifecycle handshake that requires no response per the MCP spec. Alternatively, handle all `notifications/*` methods uniformly (either all silently drop or all log and drop).

**Risk:** None — comment-only change, or minor logic consolidation.

---

## Priority Order

| Priority | Violation | Effort | Impact |
|---|---|---|---|
| 1 | `gate.py`:56 — dead `_EXPECTED` variable | 1 min | Noise removal |
| 2 | `personality.py`:347 — dead `reload_soul` alias | 2 min | Noise removal |
| 3 | `health.py` — delete or complete `require_healthy()` | 5 min | Removes misleading API |
| 4 | `hybrid_search.py` — remove `provenance` field | 10 min | Removes per-query memory overhead |
| 5 | `personality.py` — collapse pattern builders | 10 min | Cleaner abstraction |
| 6 | `llm/client.py` — remove unused context-manager | 5 min | YAGNI cleanup |
| 7 | `gate.py`:149–155 — document or remove DB URL fallback | 10 min | Eliminate surprise coupling |
| 8 | `ratelimit.py` — Postgres backend decision | 15 min | Speculative code removal or documentation |

**Estimated total effort:** ~60 minutes for all 8 fixes.

---

## Next Steps

- [ ] Decide on option A vs B for items 3, 7, and 8
- [ ] Implement fixes in a single feature branch
- [ ] Run full test suite: `GROK_API_KEY=dummy pytest tests/ -q --tb=short`
- [ ] Open PR for review
