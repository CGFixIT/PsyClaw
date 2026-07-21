---
title: "CyClaw Test-Suite Defects & Fix Spec — 2026-07-21"
date: 2026-07-21
tags: [tests, audit, fix-spec, coverage]
related:
  - CLAUDE.md
  - docs/audits/Main_Branch_Audit_And_Review_2026-07-21.md
---

# CyClaw Test-Suite Defects & Fix Spec — 2026-07-21

**Read this first if you are a Claude Code session picking up this PR.** This
document is written as an execution spec, not a narrative report. Every finding
gives you: the exact file/line, the concrete fix, and (where possible) drop-in test
code or a close paraphrase of what to write. Work top-to-bottom by priority tier.
After each fix, run the single-file command shown so you don't have to wait for the
full suite each time; run the full suite once at the end.

**Baseline state (verified 2026-07-21, main @ `be57a4d`, Python 3.12.3):** the test
suite is in **good health**. `GROK_API_KEY=dummy pytest tests/ -q --tb=short`
passes 100% (zero failures/errors), with 89.50% coverage against an 80% gate. This
document is a coverage/robustness improvement backlog, **not** a "tests are broken"
report — treat every item below as "add a missing test" or "tighten a weak
assertion," not "something is failing today."

Do not weaken any existing passing test, the 80% coverage gate, or any of the six
security invariants while working through this list (see `CLAUDE.md` §3, §6). If a
fix here would require touching `graph.py` edges, `banned_patterns`, or `soul.md`,
stop and treat it as CLAUDE.md §7 High-tier — ask first.

---

## Priority tier 1 — security/reliability-relevant zero-coverage branches

These are the two highest-value gaps: real production code paths with **zero**
direct test coverage, both touching reliability or security-relevant logic.

### 1.1 `retrieval/hybrid_search.py:210-220` — fail-soft dual-leg degrade path untested

The hybrid retriever is designed to degrade gracefully if either the semantic or
keyword leg throws (`EmbeddingServiceError` on the semantic side;
`json.JSONDecodeError`/`KeyError`/`AttributeError`/`IndexError`/`TypeError`/
`ValueError` on the keyword side), logging an `retrieval_degraded` audit event and
falling back to the surviving leg. **No test exercises either exception branch.** A
regression that turned this fail-soft path into a hard crash (taking down `/query`
entirely) would not be caught today.

**File to edit:** `tests/test_hybrid_search.py`

**Add:**
```python
def test_semantic_leg_failure_degrades_to_keyword_only(self, monkeypatch):
    retriever = HybridRetriever(cfg=TEST_CONFIG)  # match existing fixture pattern in this file
    monkeypatch.setattr(
        retriever, "semantic_search",
        lambda query: (_ for _ in ()).throw(EmbeddingServiceError("embedding backend down")),
    )
    # keyword_search left real/working
    results = retriever.hybrid_search("some query")
    assert results  # keyword leg alone still returns hits
    # confirm the degrade event was audited — patch utils.logger.audit_log or inspect via monkeypatch capture

def test_keyword_leg_failure_degrades_to_semantic_only(self, monkeypatch):
    retriever = HybridRetriever(cfg=TEST_CONFIG)
    monkeypatch.setattr(
        retriever, "keyword_search",
        lambda query: (_ for _ in ()).throw(KeyError("bm25 index corrupt")),
    )
    results = retriever.hybrid_search("some query")
    assert results  # semantic leg alone still returns hits

def test_both_legs_failing_returns_empty_not_crash(self, monkeypatch):
    retriever = HybridRetriever(cfg=TEST_CONFIG)
    monkeypatch.setattr(retriever, "semantic_search", lambda q: (_ for _ in ()).throw(EmbeddingServiceError("x")))
    monkeypatch.setattr(retriever, "keyword_search", lambda q: (_ for _ in ()).throw(ValueError("y")))
    results = retriever.hybrid_search("some query")
    assert results == []
```
Adapt fixture/constructor details to match this file's actual existing setup (check
how `TestFusionReturnsFullUnion` builds its retriever instance and mirror it — do not
invent a new construction pattern). Assert on the `retrieval_degraded` audit event if
`audit_log` is easily monkeypatched/spied in this file already; otherwise a follow-up
is acceptable, but the "doesn't crash, degrades to surviving leg" behavior is the
must-have assertion.

**Verify:** `GROK_API_KEY=dummy pytest tests/test_hybrid_search.py -q --tb=short`

### 1.2 `retrieval/indexer.py:71-76` — symlink path-traversal guard untested

```python
if not file_path.resolve().is_relative_to(corpus_resolved):
    logger.warning("Skipping %s: resolves outside corpus directory", file_path)
    continue
```
This is the guard that stops a symlink inside `data/corpus/` from letting the indexer
read (and then serve via RAG) a file outside the corpus root — directly
security-relevant (corpus/memory-poisoning threat class in `docs/THREAT_MODEL.md`
§2). **No test creates a symlink to verify this guard actually skips the escaping
file.**

**File to edit:** `tests/test_indexer.py`

**Add** (POSIX-only; skip on Windows where symlink creation needs elevated privilege):
```python
import os
import sys
import pytest

@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs elevated privileges on Windows")
def test_load_corpus_skips_symlink_escaping_corpus_dir(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("# secret\nshould never be indexed")
    (corpus / "legit.md").write_text("# legit\nshould be indexed")
    (corpus / "escape.md").symlink_to(outside / "secret.md")

    docs = load_corpus(str(corpus), extensions=[".md"])  # match this module's real function name/signature

    sources = {d["source"] for d in docs}  # adapt to the real doc dict/dataclass shape used in this file
    assert any("legit.md" in s for s in sources)
    assert not any("secret.md" in s for s in sources)
```
Check the real function name/signature/return shape in `retrieval/indexer.py` and the
existing tests in `TestLoadCorpusCaseInsensitive` before writing this — match the
established pattern in that class rather than guessing.

**Verify:** `GROK_API_KEY=dummy pytest tests/test_indexer.py -q --tb=short`

Also in the same file/area (lower priority, same PR is a natural place to add these):
- `CorpusEmptyError` for a missing corpus directory (`indexer.py:61-62`) — no test.
- `CorpusEmptyError` for a corpus directory with no matching-extension files
  (`indexer.py:82-83`) — no test.
- Per-file `UnicodeDecodeError`/`OSError` skip-and-continue (`indexer.py:77-81`) — no
  test writes a non-UTF-8 file to confirm indexing continues past it.

---

## Priority tier 2 — invariant-guarding test that doesn't prove a negative

### 2.1 `tests/test_agentic_isolation.py` — the I6 isolation test never proves it can catch a violation

`test_agentic_does_not_import_request_path` and its sibling only run the real
`_imports()` AST-walk helper against the current, already-compliant tree and assert
the forbidden module name is absent. **There is no test proving `_imports()` would
actually flag a violation if one existed** — a bug in the AST walk (e.g. missing
`ast.ImportFrom` handling, or matching only `import x` and not `from x import y`)
would leave this invariant-guarding test green even while silently not testing
anything.

**File to edit:** `tests/test_agentic_isolation.py`

**Add:**
```python
def test_imports_helper_detects_a_synthetic_violation():
    assert "agentic" in _imports("import agentic\n")
    assert "agentic" in _imports("from agentic import cli\n")
    assert "gate" in _imports("from gate import app\n")
    assert "agentic" not in _imports("from . import agentic\n")  # relative import should not false-positive on package name
```

**Also fix (same file):** `test_agentic_does_not_import_request_path` currently
forbids only `{"gate", "graph", "mcp_hybrid_server"}` — it omits `gate_ops`, even
though `REQUEST_PATH_MODULES` (line 18 of this file) already includes `gate_ops.py`
as a protected module. Add `"gate_ops"` to the `forbidden` set so an `agentic/` file
importing `gate_ops` would actually be caught.

**Verify:** `GROK_API_KEY=dummy pytest tests/test_agentic_isolation.py -q --tb=short`,
then re-run `python3 .claude/skills/invariant-guard/check_invariants.py` to confirm
I6 still passes (it will, since the tree is compliant — this fix only strengthens the
test's ability to detect a *future* regression).

---

## Priority tier 3 — I5 soul-governance gate has no direct negative test

### 3.1 `tests/test_personality.py` — empty/whitespace `reason` rejection never directly tested

`utils/personality.py:289-290` is the literal enforcement of invariant I5 ("soul
mutation requires an explicit human `reason` string"):
```python
if not reason or not reason.strip():
    raise ValueError("reason must not be empty")
```
Every existing test in this file always passes a non-empty `reason` — **the actual
gate has zero direct test coverage**, despite `test_due_diligence_invariants.py`
confirming (via source inspection, not a call) that the code exists.

**File to edit:** `tests/test_personality.py`

**Add** (place near `TestApplyEvolutionInjectionGate` or as a new small class):
```python
def test_apply_evolution_rejects_empty_reason(self, personality_manager):
    with pytest.raises(ValueError, match="reason must not be empty"):
        personality_manager.apply_evolution("new soul text", "")

def test_apply_evolution_rejects_whitespace_only_reason(self, personality_manager):
    with pytest.raises(ValueError, match="reason must not be empty"):
        personality_manager.apply_evolution("new soul text", "   ")
    # confirm nothing was written — mirror the disk/DB non-mutation assertion pattern
    # already used by the injection-block test in this same class
```
Match the real fixture name for constructing a `PersonalityManager` in this file
(search for how `TestApplyEvolutionInjectionGate` builds one) rather than inventing
`personality_manager` if that fixture doesn't exist under that name.

### 3.2 Same file — `restore_from_backup()` with no `.bak` present is untested

`utils/personality.py:329-330` raises `FileNotFoundError` when no backup exists; every
existing restore test plants a `.bak` first.

**Add:**
```python
def test_restore_from_backup_raises_when_no_backup_exists(self, personality_manager):
    with pytest.raises(FileNotFoundError):
        personality_manager.restore_from_backup()
```

**Verify:** `GROK_API_KEY=dummy pytest tests/test_personality.py -q --tb=short`

---

## Priority tier 4 — flaky-test risk (real sleep racing a timeout, banned pattern)

### 4.1 `tests/test_sync_lock.py:132-150` — race test uses a real sleep, not a coordinated signal

`test_stale_reclaim_race_single_winner` spawns 8 real processes and relies on
`time.sleep(0.25)` to stop a late "loser" from double-winning after the real winner
releases. This is exactly the "real sleep racing a timeout" pattern CLAUDE.md §4 bans
elsewhere in this repo, and on a loaded CI runner (this test spawns 8 processes on
the `windows-latest` matrix leg) a stall over 250ms produces a **false test failure**,
not a caught bug.

**File to edit:** `tests/test_sync_lock.py`

**Fix approach:** replace the `time.sleep(0.25)` with an `multiprocessing.Event` the
winner waits on before releasing, set by the parent process only after collecting all
7 "lost" results — this makes the test deterministic (every loser is guaranteed to
have already attempted its acquire against a still-held lock) instead of
timing-dependent. Sketch:
```python
def _worker(lock_path, cfg_dict, barrier, results, release_event):
    barrier.wait(timeout=30)
    try:
        with _acquire_sync_lock(lock_path, cfg_dict):
            results.put(("won", os.getpid()))
            release_event.wait(timeout=30)  # hold until parent says everyone else has tried
    except SyncRuntimeError:
        results.put(("lost", os.getpid()))

# in the test body: spawn workers with a shared release_event,
# collect results.get(timeout=30) for n_procs - 1 (the losers),
# THEN release_event.set() so the winner releases,
# THEN collect the winner's own result.
```
Also fix the companion issue in the same test: `results.get()` calls have **no
timeout**, so if a worker dies on an unhandled exception (e.g. the Windows
`PermissionError` noted in the companion audit report's PR #597 section) without
calling `results.put(...)`, the test hangs the whole suite instead of failing
cleanly. Change every `results.get()` to `results.get(timeout=30)`, and have the
worker's exception handler catch bare `Exception` (not just `SyncRuntimeError`) and
put `("error", repr(exc))` so an unexpected failure surfaces as a clear assertion
failure rather than a hang. Also assert `p.exitcode == 0` for every joined process.

**Verify:** `pytest tests/test_sync_lock.py -q --tb=short -v` (run a few times locally
if possible to sanity-check it isn't newly flaky in the other direction).

---

## Priority tier 5 — everything else (grouped by file, ordered by value)

Work through these opportunistically; each is small and independent. File : finding :
fix, one line each unless more detail is needed.

**tests/test_gate.py** (vs `gate.py`)
- `/soul/apply`'s `PromptInjectionError → 400` branch (gate.py:610-617) never
  triggered — patch `personality.apply_evolution` to raise it, assert 400 +
  `soul_apply_injection_blocked` audit event.
- The generic `except Exception → 500 GRAPH_ERROR` wiring (gate.py:499-502) is unit
  tested for `_sanitize_error` in isolation but never end-to-end through `POST
  /query` — set `mock_graph.invoke.side_effect = RuntimeError("boom")` and assert the
  full response + audit event.
- The non-dict `answer_sources` skip-and-warn branch (gate.py:566-572) never
  triggered — feed one dict + one non-dict source, assert the non-dict is dropped and
  a `skipped_sources` audit event with the right count is emitted.

**tests/test_gate_ops.py** (vs `gate_ops.py`)
- `_log_safe`'s CRLF-stripping never tested with a real `\r`/`\n` input.
- fsconnect `grep` action's positive (valid, non-rejected pattern) path never tested,
  only the read path and the regex-rejection path.

**tests/test_client.py** (vs `llm/client.py`)
- `LocalLLMClient`'s conditional `Authorization` header (sent only when `api_key` is
  configured) has no test, unlike the Grok/Claude clients which do — add a symmetric
  pair of tests.

**tests/test_hybrid_search.py**
- `TestRRFFusion`'s three tests (lines 24-46) recompute RRF arithmetic on a bare
  Python literal and never call `HybridRetriever.hybrid_search` — they'd stay green
  even if the real fusion formula changed. Either delete (redundant with
  `TestFusionReturnsFullUnion`) or rewrite to assert on a real `SearchResult.rrf_score`
  from an actual call.

**tests/test_embeddings.py**
- `resolve_cache_dir`'s absolute-path short-circuit (embeddings.py:30-31) only
  exercised indirectly — add a direct call with `/abs/path` and a Windows-style
  `\`-prefixed path.
- The documented "raw exception propagates unwrapped from `get_embeddings_batch` on a
  `_load_model` failure" contract has no pinning test.

**tests/test_security.py**
- `TestLoggingSetup`'s docstring claims to verify "file + console handlers" but only
  asserts the file handler — add an assertion that a `StreamHandler` is actually
  attached.

**tests/test_health.py**
- `check_all()`'s config-parse-failure branch (`utils/health.py:100-101`, catching
  `OSError`/`KeyError`/`TypeError`/`yaml.YAMLError`) never triggered — patch the
  config loader to raise, assert a `healthy=False` "config" status rather than an
  unhandled exception.

**tests/test_metrics.py**
- `compute_metrics`'s `score_n == 0` branch (`metrics.py:168`, returns
  `{"avg": None, "min": None, "max": None}`) never triggered — every test fixture
  includes at least one scored event.

**tests/test_startup_robustness.py**
- Uses a bare module-level `import gate` with no init-patching, unlike
  `test_edge_cases.py`/`test_runtime_errors.py` which patch `HybridRetriever`/
  `build_graph` before importing. This risks the exact "importing gate triggers full
  app init" trap CLAUDE.md's own trap list warns about. Action: first confirm with
  `GROK_API_KEY=dummy pytest tests/test_startup_robustness.py -q --tb=short` in an
  environment with no pre-built index whether collection is actually fragile; if so,
  patch construction dependencies before the module-level import, matching
  `test_edge_cases.py`'s pattern.

**tests/test_sync_runner.py** (vs `sync/runner.py`)
- `hash_changed_files`'s `except ValueError` fallback (`sync/runner.py:332-334`, fires
  when `os.path.commonpath` raises on cross-drive paths) never exercised — monkeypatch
  `os.path.commonpath` to raise `ValueError`, assert the event still returns with
  `sha256=None`.

**tests/test_sync_scheduler.py** (vs `sync/scheduler.py`)
- `WindowsTaskScheduler.status()` (scheduler.py:348-363), both success and failure
  branches, has zero coverage unlike `install`/`remove`/`CronScheduler.status`.
- `CronScheduler._read_crontab`'s `except FileNotFoundError` branch (binary vanishes
  between the `shutil.which` pre-check and `subprocess.run`) is untested — only the
  `shutil.which() is None` pre-check path is covered.

**tests/test_sync_cli.py** (vs `sync/cli.py`)
- `test_cli_imports_without_scheduler` (lines 284-290) is **tautological**: it never
  actually blocks `sync.scheduler` from being importable, so it passes regardless of
  whether the "scheduler absent" decoupling works. Fix: use
  `unittest.mock.patch.dict(sys.modules, {"sync.scheduler": None})` before reload,
  then call `cmd_status` and assert the graceful-degradation branch
  (`sync/cli.py:324-326`) is actually taken.
- `cmd_status`'s `except ImportError` branch is never triggered by any test — same
  fix as above covers it.

**tests/test_agentic_cli.py** (vs `agentic/cli.py`)
- `EXIT_FAIL` (2) is never exercised despite 4 call sites returning it
  (`cmd_context`, `cmd_propose_skill`, `cmd_apply_skill`, `cmd_test`) — add one test
  per site forcing the underlying error.
- `cmd_context`'s `except (GhNotInstalledError, GhVersionError) → EXIT_ENV (3)` branch
  is untested (the one existing `==3` test hits a different code path).
- The `test` subcommand (`cmd_test`) has **no test at all** — add pass-all and
  partial-failure cases.
- `test_status_runs` calls the real, unmocked `check_gh_version` (real
  `shutil.which("gh")` + subprocess) and tolerates either outcome without asserting
  on either branch — this makes the test's actual behavior depend on whether `gh`
  happens to be installed on the CI/dev host. Patch it so both branches are
  deterministically exercised and asserted.

**tests/test_agentic_harness_optimizer.py**
- `decide_candidate`'s three hard-reject gates (`train_passed`, `holdout_passed`,
  `proposal_present` — `agentic/harness_optimizer/core.py:196-205`) are never
  independently tested; every test sets all three `True`. Add one test per gate set
  to `False` with the others `True`, asserting the specific `rejected_gates` entry and
  `accepted is False`.
- `score_cases(())` (empty-suite guard, `scoring.py:62-64`) is never called directly
  despite being explicitly security-relevant ("a missing suite cannot accidentally
  improve a candidate").

---

## Priority tier 6 — organizational-only, no functional risk

These are not defects; do not "fix" unless explicitly asked — noted for completeness
per the audit brief, to avoid a future session assuming they were missed.

- `tests/test_logger.py` doesn't itself exercise `audit_log`'s hash-only/no-raw-text
  contract — that's genuinely covered elsewhere (`test_audit.py`,
  `test_mcp_server.py`) via real write+read assertions. Optionally duplicate one such
  case into `test_logger.py` so the module that owns `audit_log` also directly pins
  the contract; not required.
- `tests/test_fsconnect_client.py:77` — `test_fs_read_flags_injection_advisory`
  asserts `content is not None` rather than the exact expected string. Low-value
  tightening: change to an exact-content equality check.
- `tests/test_sync_lock.py`'s multiprocessing race test (separate from the sleep issue
  in tier 4) is the one legitimate case in this repo where real OS-level timing can't
  be fully mocked away (it's testing real cross-process file-lock atomicity) — after
  applying the tier-4 fix, document in the test's own docstring that it is an
  intentional, narrowly-scoped exception to the "no real sleep/timing" rule, so a
  future contributor doesn't "fix" it into a mock that stops testing what it's for.

---

## Files with zero findings (audited in full, nothing to add)

For completeness — these were read end-to-end against their target source and found
to have no tautological assertions, no real-sleep races, no over-mocking, and no
security-relevant zero-coverage branches: `test_graph.py`,
`test_due_diligence_invariants.py`, `test_conftest_fixtures.py` (the deepcopy
isolation claim in CLAUDE.md was independently verified true, not just assumed),
`test_stemmer.py`, `test_sanitizer.py`, `test_rate_limit.py`, `test_edge_cases.py`,
`test_rag_integration.py`, `test_runtime_errors.py`, `test_sync_config.py`,
`test_sync_filters.py`, `test_agentic_config.py`, `test_agentic_context.py`,
`test_agentic_registry.py` (a model example of real lock/race/atomic-write testing),
`test_agentic_writer.py`, `test_agentic_gh_client.py`, `test_agentic_selftest.py`,
`test_agentic_deepagent_optional.py`, `test_fsconnect_pathsafe.py` (a model file —
real symlink attack payloads against real code), `test_sqlconnect_client.py` (a model
file — real SQL-bypass payloads against the real guard), `test_fsconnect_config.py`,
`test_sqlconnect_config.py`, `test_fsconnect_osutil.py`, `test_fsconnect_selftest.py`,
`test_sqlconnect_cli.py`, `test_fsconnect_writer.py`, `test_fsconnect_cli.py`,
`test_fsconnect_indexer.py`, `test_sqlconnect_context.py`, `test_guardrail_bridge.py`,
`test_ops_runner.py`, `test_guardrails_isolation.py` (genuine AST-based check, unlike
the agentic-isolation gap in tier 2), `test_mcp_server.py`, `test_audit.py`,
`test_clear_cache.py`, `test_config_validation.py`, `test_guardrails_config.py`,
`test_guardrails_integration.py`, `test_guardrails_metrics.py`, `test_guardrails_rails.py`,
`test_guardrails_cli.py`, `test_guardrails_selftest.py`, `test_personality_postgres.py`,
`test_pgvector_store.py`, `test_ratelimit_postgres.py`, `test_cyclaw_sandbox_skill.py`.

Two narrower gaps were found in otherwise-clean files and are folded into tier 5
above rather than repeated here: `test_fsconnect_quota.py` (missing direct
`is_stale()` coverage) and `test_fsconnect_trash.py` (missing `list_entries()`
coverage including the corrupt-sidecar skip path) and `test_fsconnect_ratelimit.py`
(window-expiry never advanced past a fixed clock — the injected `_Clock` fixture is
constructed but never mutated in any test).

**tests/test_fsconnect_quota.py:** add a direct `is_stale(None, now, 24)` /
stale-ledger / aged-ledger test, plus one integration test seeding an old
`computed_at` and confirming `FsWriter.quota_status()` auto-recomputes without
`recompute=True` being forced.

**tests/test_fsconnect_trash.py:** add a `list_entries()` test covering: empty when
`.cyclaw-trash` doesn't exist, real round-trip of a written `.meta.json`, and a
garbled sidecar alongside a good one to confirm the `except Exception: continue`
skip path (trash.py:143-149) actually works end-to-end, not just via the isolated
`_parse_meta` unit test.

**tests/test_fsconnect_ratelimit.py:** add `test_window_expiry_frees_budget` —
exhaust `max_ops` at `clock.t = 1000`, assert refusal, advance `clock.t = 1000 +
window_seconds + 1`, assert the next write succeeds. Deterministic, no real sleep
needed since the clock is already injectable.

---

## Suggested commit/PR sequencing for whoever implements this

Land tier 1 and tier 2 first (security/invariant-relevant, small diffs, highest
value). Tier 3 next (I5 gate coverage — also small). Tier 4 (de-flake) can land
independently at any time. Tier 5 can be split into 2-3 follow-up PRs by area (gate/
retrieval, sync/scheduler/cli, agentic) to keep each PR reviewable in one sitting per
this repo's own PR quality bar (`CLAUDE.md` §6: "one concern; diff reviewable in one
sitting"). Do not bundle tier 1-4 with tier 5/6 in one PR.

After implementing any tier, re-run the full gate before considering it done:
```bash
GROK_API_KEY=dummy pytest tests/ -q --tb=short --cov=<same --cov flags as ci.yml> --cov-report=term-missing
python3 .claude/skills/invariant-guard/check_invariants.py
ruff check --select E,F,I,B,C4,UP,S .
```
