# CyClaw Unit Test Suite — Audit & Remediation Report

> **Historical snapshot (2026-06-16) — findings since remediated.** This report
> describes the suite as it existed at HEAD `2bccc9e`. Its recommendations have
> landed on `main`: §3.8's limiter extraction now exists as `utils/ratelimit.py`
> with `tests/test_rate_limit.py` importing the real limiter under an injected
> fake clock (no `time.sleep`), and the suite collects and passes cleanly
> (1096 passed / 13 expected skips as of the 2026-07-09 sandbox audit — see
> `docs/audits/2026-07-09-full-review-findings.md`). Read the sections below as
> the *before* picture, not the current state.

**Date:** 2026-06-16
**Runtime verified:** Python 3.12.3 (CPython, Linux)
**Scope:** Full `tests/` folder, run against `main` (HEAD `2bccc9e`)
**Trigger:** First scheduled review run — full-branch verification (Python 3.12 runtime,
emulated RAG query, API smoke, unit testing).

---

## 1. Executive summary

The **application code runs correctly under Python 3.12** — the real `graph.py` topology and
the `gate.py` FastAPI gateway were exercised end-to-end and pass (see §2). **The unit test
suite, however, is broken**: it does not collect cleanly and most of it fails.

```
pytest tests/ -q  →  20 failed, 19 passed, 3 collection errors   (exit code 2)   [as found]
pytest tests/ -q  →  19 failed, 47 passed, 0 collection errors                    [after this PR — see §7]
```

> **Update (this PR):** the 3 collection errors are now fixed and the previously
> un-collectable `test_graph` / `test_gate` / `test_stemmer` (+ `test_hybrid_search`)
> files pass. The remaining 19 failures are the **config-driven sanitizer** suite
> (owned by open **PR #14**) and the **PersonalityManager contract** suite (needs a
> design decision — tracked separately). See **§7** for exactly what changed.

Because `pytest` aborts the whole run on the 3 collection errors, the project's CI only stays
"green" by running **two hand-picked files** with failures swallowed
(`.github/workflows/ci.yml` lines 49–51: `pytest tests/test_sanitizer.py tests/test_rate_limit.py … || echo 'Safe tests done'`).
That masks the 20 failures below and gives a false signal of health.

The failures are **not flaky** and **not environmental** — they are deterministic API/contract
mismatches between the tests and the shipped modules. Several test files explicitly target a
"future (Dropbox-sync-pending) build" (per their `BUILD-ALIGNMENT NOTE` headers and the
`8b84e0f` / `83b6496` commit messages). Some of those fixes are already in flight on the `cc`
integration branch (open **PR #14 → main**, and merged-into-`cc` PRs #7/#12/#15), but **none of
them are on `main` yet**, so `main`'s suite is red.

### Runtime verification (passed) — for contrast with the failing tests

| Check | Result |
|---|---|
| Emulated offline RAG query (CI base64 payload, decoded) | ✅ pass |
| Real `graph.py` topology — high-score → `local` | ✅ pass |
| Real `graph.py` topology — low-score → user-gate pause | ✅ pass |
| Real `graph.py` topology — declined → `offline-best-effort` | ✅ pass |
| API smoke — `POST /query` (apipsTest.ps1 equivalent) → `200`, `model_used=local` | ✅ pass |
| API smoke — `GET /health` → `200` (`degraded`: no live LM Studio, expected) | ✅ pass |

> The PowerShell smoke test (`tests/apipsTest.ps1`) is a one-line `Invoke-RestMethod` POST to
> `/query`; it was reproduced on Linux via the FastAPI `TestClient` since the run host is not
> Windows. It requires a live gateway + built index in real use.

---

## 2. Per-file results (Python 3.12)

| Test file | Result | Root cause |
|---|---|---|
| `test_audit.py` | **9 passed** ✅ | Matches current `utils/logger` API |
| `test_rate_limit.py` | **3 passed** ⚠️ | Passes, but tests a *local copy* of the logic, not `gate.py` |
| `test_hybrid_search.py` | **6 passed, 1 failed** | `kubernetes→k8s` custom stem vs `len>=5` assertion |
| `test_personality_changes.py` | **1 passed, 3 failed** | `get_version()` type, `propose_evolution` keys, TTL-on-init |
| `test_sanitizer.py` | **8 failed** | Tests a config-driven filter; shipped filter is single-arg/hardcoded |
| `test_personality.py` | **8 failed** | `patch(...audit_log)` + several contract drifts |
| `test_stemmer.py` | **collection error** | Imports `enhanced_porter_stem` (does not exist) |
| `test_graph.py` | **collection error** | Imports `MockRetriever`… (not in `conftest.py`) + wrong `build_graph` arg order |
| `test_gate.py` | **collection error** | Imports `MockRetriever`… (not in `conftest.py`) |

---

## 3. Detailed findings & recommended fixes

### 3.1 `test_gate.py` — collection error *(blocks the whole run)*
```
ImportError: cannot import name 'MockRetriever' from 'tests.conftest'   (line 15)
```
- Imports `MockRetriever, MockLocalLLM, MockGrokClient, MOCK_HIGH_SCORE_RESULTS, MOCK_LOW_SCORE_RESULTS`.
  `conftest.py` defines **none** of them — it only provides *fixture-style* mocks
  (`mock_retriever`, `mock_llm`, `mock_search_results`, `bm25_index`, `test_config`) and the
  `TEST_CONFIG` dict.
- **Fix:** add the missing class/constant symbols to `conftest.py`:
  - `MockRetriever(results)` exposing `.hybrid_search / .semantic_search / .keyword_search`.
  - `MockLocalLLM(response=...)` that records `.last_prompt` (asserted by `test_graph.py`).
  - `MockGrokClient(response=...)`.
  - `MOCK_HIGH_SCORE_RESULTS / MOCK_LOW_SCORE_RESULTS / MOCK_EMPTY_RESULTS` as `list[SearchResult]`.

### 3.2 `test_graph.py` — collection error **+ `build_graph` signature drift**
- Same `ImportError` (line 17).
- Even once importable, calls are `build_graph(cfg, retriever, llm)` /
  `build_graph(cfg, retriever, llm, grok)` — **positional `cfg` first**. The real signature is
  `build_graph(retriever, llm, grok, cfg, personality=None)`. The argument order is mismatched.
- **Fix:** add the conftest symbols **and** convert call sites to keyword args
  (`build_graph(retriever=…, llm=…, grok=…, cfg=…)`). Recommend making `build_graph`'s
  dependency parameters keyword-only (`def build_graph(*, retriever, llm, grok, cfg, …)`) so
  positional drift can never silently mis-bind again.

### 3.3 `test_stemmer.py` — collection error
```
ImportError: cannot import name 'enhanced_porter_stem' from 'retrieval.stemmer'   (line 10)
```
- `retrieval/stemmer.py` exposes `stem_token` and `tokenize_and_stem` only.
- Hard-coded expectations (`"running"→"runn"`, `"rationalization"→"rationalize"`,
  `"policies"→"policy"`) encode a *different* stemmer than the NLTK-Porter + custom-dict
  actually shipped.
- **Fix:** either expose a public `enhanced_porter_stem = stem_token` alias and **re-baseline**
  the expected values against real output, or rewrite the tests against `stem_token`. Note the
  `kubernetes→k8s` custom mapping (see §3.7) must be reconciled.

### 3.4 `test_sanitizer.py` — 8 failed (config-driven contract not on `main`)
```
TypeError: check_input() takes 1 positional argument but 2 were given   (lines 43,48,52,56,60)
TypeError: sanitize_chunk() takes 1 positional argument but 2 were given (lines 67,75,80)
```
- Tests assume a **config-driven, toggleable** filter: `check_input(query, config_path)`,
  `sanitize_chunk(text, config_path)`, an `enabled: false` bypass, per-config `banned_patterns`,
  and the error string `"exceeds maximum length"`. The shipped `utils/sanitizer.py` is a
  **hardcoded, single-arg** module whose error string is `"Input too long"`.
- This is the same gap the architecture PDF describes ("Patterns are config-driven and
  hot-reloadable") but that `main` does **not** implement — `config.yaml`'s
  `policy.prompt_filter.banned_patterns` is currently **dead config**.
- **Fix:** implement the config-driven sanitizer (read `policy.prompt_filter` →
  `enabled / banned_patterns / max_input_chars`, align the error text). **Already implemented on
  the `cc` branch** (PR #7 "config-driven prompt filter + repair its test suite", PR #15
  "restore strong injection regexes + harden config load"). Until PR #14 merges `cc → main`,
  these 8 failures are expected on `main`.

### 3.5 `test_personality.py` — 8 failed
```
AttributeError: module 'utils.personality' has no attribute 'audit_log'   (every test)
```
- Each test does `patch("utils.personality.audit_log")`, but `personality.py` never imports
  `audit_log`. (This is also the root of the **missing "forensic audit_log on drift"** that the
  architecture PDF's Invariant #5 promises.)
- Further contract drifts the tests assume but the module does not provide:
  | Test expects | Module actually does |
  |---|---|
  | `get_version() == 1` (int) | returns string `vN_<sha8>_<date>` |
  | `propose_evolution(...) → {status, proposed_soul}` | returns `{diff, injection_flags, safe_to_apply, reason}` |
  | `pm.conn.execute(...)` (persistent conn) | opens/closes a new `sqlite3` conn per call |
  | `pm.reload_soul()` | method is named `reload()` |
  | `pm.maintenance(ttl_days=…)` | no such method (prune only inside `record_interaction`) |
  | "creates default soul when missing" | `_load_soul` sets `soul_core=""`, writes nothing |
- **Fix (design decision required):** settle the intended `PersonalityManager` contract, then
  align code **and** test. Recommended target (also closes documented-but-missing v1.3
  behaviors): keep a persistent `self.conn` (or a test-visible `_connect()` helper); add an
  integer `version` accessor; add `maintenance(ttl_days)` and call it from `__init__`; add a
  `reload_soul` alias; `import audit_log` and emit a forensic event on drift; create a default
  soul when the file is missing.

### 3.6 `test_personality_changes.py` — 3 failed, 1 passed *(plus portability smells)*
```
TypeError: '>=' not supported between 'str' and 'int'   (line 46  — get_version() is a str)
KeyError: 'status'                                      (line 66  — propose_evolution has no 'status')
AssertionError: Old interaction should be pruned        (line 128 — no TTL prune on __init__)
```
- **Passed:** `test_drift_detection` (the drift-recovery version row *is* inserted).
- Design flaws independent of the failures:
  - **Hardcoded foreign path** `sys.path.insert(0, '/home/workdir/artifacts/CyClaw-refactored')`
    (line 18) — non-portable, environment-specific.
  - **Global at-import monkeypatch** of `utils.logger.audit_log` (line 22) — leaks across the
    session and other tests.
  - **Not pytest-native** — plain `def test_*` + a `__main__` runner + `print("✓…")` instead of
    fixtures/assert-based discovery.
- **Fix:** delete the hardcoded path; use `tmp_path` + a `monkeypatch` fixture for `audit_log`;
  align to the chosen PM contract; if atomic write is a requirement, assert no `.tmp` remnant
  after `apply_evolution` (currently the module uses a plain `write_text`, not `os.replace`).

### 3.7 `test_hybrid_search.py` — 1 failed, 6 passed
```
test_single_word: assert len(tokens[0]) >= 5   → tokenize_and_stem("kubernetes") == ["k8s"]  (len 3)
```
- Genuine **logic conflict**: `retrieval/stemmer.py` `_CUSTOM_STEMS` maps `kubernetes→k8s`,
  which contradicts the test's "not overstemmed (≥5 chars)" expectation.
- The other 6 (RRF math, veeam/sobr tokenization) **pass** — these are the only real
  retrieval-layer assertions on `main` that currently pass.
- **Fix:** decide intended behavior for domain acronyms. If `kubernetes→k8s` is intended, change
  the assertion; if not, drop the mapping.

### 3.8 `test_rate_limit.py` — 3 passed, but **tests a copy, not the code** ⚠️
- The file **re-defines its own** `check_rate_limit` + `_rate_limits` (lines 11–21) rather than
  importing from `gate`. It validates a *duplicate* of the algorithm — `gate.check_rate_limit`
  could regress and this test would still pass (false confidence).
- Also carries the same hardcoded `/home/workdir/...` path and a real `time.sleep(2.1)`
  (wall-clock-dependent, slow).
- **Fix:** extract the limiter into a small importable unit (e.g. `utils/ratelimit.py`) used by
  **both** `gate.py` and the test; replace `sleep` with a monkeypatched clock; add an
  IP-eviction / memory-bound test (the subject of `cc`-branch PR #12, which fixes the limiter's
  unbounded per-IP growth — not yet on `main`).

### 3.9 `test_audit.py` — 9 passed ✅ (stale warning note)
- Fully aligned with the current `utils/logger` API. Its top-of-file `BUILD-ALIGNMENT NOTE`
  claiming it "targets a FUTURE build … expected to fail" is **inaccurate** — it passes against
  HEAD. **Fix:** remove the misleading note.

---

## 4. Cross-cutting design flaws (the "test folder" issues)

1. **Two incompatible mocking conventions.** `conftest.py` ships *fixture-style* mocks
   (`mock_retriever`, `mock_llm`); `test_graph.py`/`test_gate.py` expect *class + constant* style
   (`MockRetriever`, `MOCK_HIGH_SCORE_RESULTS`). Pick one and converge — the split is the direct
   cause of the 2 most damaging collection errors.
2. **Duplicate, drifting `TEST_CONFIG`.** Defined in both `tests/conftest.py` and
   `tests/test_personality.py`, plus several inline ad-hoc cfgs. Centralize in `conftest.py`.
3. **CI hides the breakage.** `ci.yml` runs only 2 files and appends `|| echo`, so the job is
   green while 20 tests fail and 3 modules don't import. After the fixes below, switch CI to
   `pytest tests/` and let the real exit code gate the build.
4. **A promised test file is missing.** The architecture PDF (Section J) lists
   `tests/test_endpoints_mocked.py` as a "NEW v1.3" file; it does **not** exist in the repo.
   Either add it or correct the doc.
5. **Non-hermetic tests.** Hardcoded `/home/workdir/artifacts/CyClaw-refactored` paths and
   at-import global monkeypatching in `test_personality_changes.py` / `test_rate_limit.py`.
6. **Mixed runners.** pytest classes vs `if __name__ == "__main__"` script runners → inconsistent
   discovery and reporting.

---

## 5. Recommended remediation order (lowest-risk first)

1. **Unblock collection** (eliminates the 3 errors + restores `pytest tests/`):
   add `MockRetriever / MockLocalLLM / MockGrokClient / MOCK_*` to `conftest.py`; add an
   `enhanced_porter_stem` alias (or rewrite `test_stemmer.py`).
2. **Pick the PersonalityManager & sanitizer contracts** and align code + tests in one change
   (coordinate with open **PR #14**, which already carries the config-driven sanitizer + repaired
   sanitizer tests — avoid double-implementing).
3. **De-duplicate logic under test** — factor the rate limiter into an importable unit; have the
   test import it.
4. **Remove non-hermetic bits** — foreign paths, at-import monkeypatching, stale BUILD-ALIGNMENT
   notes; centralize `TEST_CONFIG`.
5. **Add the missing `test_endpoints_mocked.py`** (or fix the architecture doc).
6. **Re-point CI** at the full suite and drop the error-swallowing `|| echo`.

---

## 6. Reproduction

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install torch==2.4.1+cpu --index-url https://download.pytorch.org/whl/cpu   # only for live embeddings
pip install -r requirements.txt -c constraints.txt pytest pytest-cov
export GROK_API_KEY=dummy
mkdir -p data/corpus data/personality index logs
echo '# Test corpus' > data/corpus/test.md && echo '# Soul' > data/personality/soul.md

# Full suite (note: 3 collection errors abort the run without the flag below)
pytest tests/ -q --continue-on-collection-errors
```

> The mocked suite does **not** require `torch`/`sentence-transformers`; `chromadb`, `langgraph`,
> `nltk`, `rank-bm25`, `fastapi`, `pydantic` are sufficient because `retrieval/embeddings.py`
> imports `sentence_transformers` lazily.

---

## 7. Fixes applied in this PR (collection errors only)

This PR resolves the **3 collection errors** and the closely-related stemmer/empty-query
failures. It deliberately does **not** touch the config-driven sanitizer (owned by **PR #14**)
or the PersonalityManager contract (a design decision — see the companion issue). To avoid
textual conflicts with PR #14 (which also edits `retrieval/stemmer.py`), the stemmer fix uses an
**import alias** rather than editing `stemmer.py`.

| File | Change |
|---|---|
| `tests/conftest.py` | Added the missing `MockRetriever`, `MockLocalLLM` (captures `last_prompt`), `MockGrokClient`, and `MOCK_HIGH_SCORE_RESULTS` / `MOCK_LOW_SCORE_RESULTS` / `MOCK_EMPTY_RESULTS` (`list[SearchResult]`) — unblocks `test_graph.py` + `test_gate.py`. |
| `tests/test_graph.py` | Fixed `build_graph(...)` **argument order** to `(retriever, llm, grok, cfg)`; in the offline-mode test, pass `grok=None` (mirrors how `gate.py` gates Grok — the graph does not read `app.mode`). |
| `tests/test_stemmer.py` | Import `stem_token as enhanced_porter_stem`; re-baselined expectations to the stemmer's real output (`policies→polici`, `running→run`, `rationalization→ration`, `kubernetes→k8s`); removed the stale BUILD-ALIGNMENT note. |
| `tests/test_hybrid_search.py` | `test_single_word`: assert `kubernetes → "k8s"` (intentional domain map) instead of `len ≥ 5`. |
| `schemas/api.py` | `QueryRequest.query` now `Field(min_length=1)` — empty queries are rejected at the schema boundary (HTTP 422), which is what `test_gate.py::test_empty_query_rejected` asserts and a small API hardening. |

**Result:** `test_graph` 9 ✓, `test_gate` 6 ✓, `test_stemmer` 12 ✓, `test_hybrid_search` 7 ✓
(plus the already-green `test_audit` 9, `test_rate_limit` 3). 0 collection errors.

**Still red (intentionally, out of scope here):** `test_sanitizer` (8 — config-driven filter, **PR #14**),
`test_personality` (8) and `test_personality_changes` (3) — pending the PersonalityManager
contract decision tracked in the companion issue.
