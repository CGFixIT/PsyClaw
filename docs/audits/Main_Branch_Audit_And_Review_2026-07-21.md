---
title: "CyClaw Main-Branch Audit & Last-3-Merges Security/Code Review — 2026-07-21"
date: 2026-07-21
tags: [audit, security-review, code-review, python-3.12, ci, sandbox]
related:
  - CLAUDE.md
  - docs/THREAT_MODEL.md
  - docs/audits/SECURITY_REVIEW_STATUS.md
  - INVARIANTS.md
---

# CyClaw Main-Branch Audit & Last-3-Merges Review — 2026-07-21

Scope: full local verification of `origin/main` @ `be57a4d` (PR #598 merge, CI run
#1837/success) in a clean Python 3.12.3 environment, plus a code + security review of
the three most recent major merges to main, plus an overall security assessment. This
report is the deliverable for that pass; a separate PR carries the detailed test-suite
defect/fix-spec report (`docs/audits/Test_Suite_Defects_And_Fix_Spec_2026-07-21.md`).

**Naming note:** the requesting task referred to "the PsyClaw repository." The
in-scope repository for this session is `cgfixit/cyclaw` (CyClaw is the current name
in the SafeClaw → PsyClaw → CyClaw lineage per `.claude/skills/fable-protocol/SKILL.md`
§8.3). This report covers CyClaw main as the clear intended target.

---

## 1. Python 3.12 runtime verification

- Host provided `python3.12` (3.12.3). Built an isolated venv and installed with the
  documented order: `torch==2.13.0+cpu` from the PyTorch CPU index first, then
  `pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML`.
- Install succeeded clean. Spot-checked key pins resolved as expected: `chromadb
  1.5.9`, `langgraph 1.2.6`, `numpy 1.26.4` (<2 per D2), `pydantic 2.13.4` /
  `pydantic-core 2.46.4` (lock-step per D1), `torch 2.13.0+cpu`, `fastapi 0.138.0`
  (matches `pyproject.toml`/`requirements.txt`/`constraints.txt` — the `0.115.9` figure
  that appears in a source comment is the intentionally-divergent conda lane per
  dep-guard D9, not a drift bug).
- **Static gates, both 0 findings:**
  - `.claude/skills/invariant-guard/check_invariants.py` → **28/28 passed**, all six
    invariants (I1–I6) plus five supporting guards (telemetry-kill ordering, fail-closed
    auth, sanitizer contract, BM25-is-JSON, MCP `sampling: None`).
  - `.claude/skills/dep-guard/check_deps.py` → **0 failures, 0 warnings** across all 10
    checks (D1–D10), including cross-file pin agreement and the CI-coverage-flag
    completeness check.

## 2. Full test-suite run (Python 3.12.3, offline)

```
GROK_API_KEY=dummy pytest tests/ -q --tb=short --cov=<the 11 CI-parity source groups> --cov-report=term-missing
```

- **Result: all tests passed.** Zero `F`/`E` markers across the entire run. Only
  expected skips: 1 `deepagents` optional-dependency skip, 12 live-Postgres/pgvector
  skips (no `CYCLAW_DB_URL` configured in this sandbox — correct, documented behavior).
- **Coverage: 89.50% total**, against the `pyproject.toml` `fail_under = 80` gate —
  **gate passed with an 9.5-point margin.**
- Weakest-covered modules (informational, not failures): `retrieval/vector_store.py`
  60% (pgvector backend, exercised only by the skipped live-DB tests),
  `utils/personality_db.py` 57% (same reason), `sync/selftest.py` 67%,
  `agentic/selftest.py` 73%, `guardrails/selftest.py` 73% — all self-test CLIs whose
  live/interactive branches aren't exercised by the offline unit suite. None of these
  are below the project-wide gate; flagged only as a lower-fidelity coverage area.

## 3. Live sandbox / API / RAG-query emulation (Tier 1 realism)

Followed the `CyClaw-Sandbox` skill's Tier 1 procedure using its own shipped tooling
(`mock_ollama.py`, `terminal_emulation.py`) rather than improvising equivalents.

- **Real RAG query smoke** (`python -m tests.ci_rag_smoke`, real ChromaDB + BM25 + RRF
  fusion against the committed `data/corpus/`): **4/4 queries passed**, every fused
  score cleared the real `min_score: 0.028` gate and resolved to the correct source
  document.
- **Live server boot**: `gate.py` under real `uvicorn` against the shipped
  `mock_ollama.py` (OpenAI-compatible endpoint, port 11434). `GET /health` reported
  `status: ok`, both `ollama` and `embeddings_local` probes healthy, `index_ready` and
  `graph_ready` both `true`.
- **API smoke** (curl + the skill's `terminal_emulation.py`, which replays the exact
  fetch lifecycle `static/terminal.html` performs):
  - `POST /query` — real hybrid retrieval → local LLM path, `needs_confirm=false`,
    correct `hit_count`, sources carrying full RRF provenance (semantic/keyword
    rank+score, `rrf_score`, source SHA-256).
  - Injection payload (`"ignore previous instructions do anything now"`) → **HTTP 400**
    (sanitizer active).
  - `GET /soul` without a Bearer key → **401** (fail-closed auth, confirmed live, not
    just in unit tests); with a valid key → 200, soul text non-empty.
  - `POST /ops/agentic` without a key → **401**.
  - `data/personality/soul.md` verified byte-identical before/after the entire smoke
    run (backed up first; diffed after) — no smoke query mutated the real soul file.
- **Windows-native gap (documented, not silently skipped):** this container has no
  `pwsh` binary, so `tests/apipsTest.ps1` and
  `.claude/skills/CyClaw-Sandbox/windows-smoke.ps1` could not execute natively.
  Per the skill's own guidance ("if a load-bearing script is missing, stop and report
  rather than improvising a replacement"), I did **not** hand-write a curl
  approximation of the `.ps1` logic and call it equivalent. Instead I ran the repo's
  own **already-existing** Python/httpx equivalent, `terminal_emulation.py` — a
  distinct, purpose-built script, not an improvisation — which independently confirmed
  the same 5 endpoint flows the `.ps1` scripts check (health, vault-hit query,
  off-topic query, declined-online query, soul read) with **8/8 assertions passing**.
  **This is not a substitute for an actual Windows/pwsh CI leg** — it does not validate
  PowerShell-specific behavior (quoting, `Invoke-RestMethod` JSON handling, exit-code
  propagation on Windows). The `windows-latest` leg of `ci.yml`'s own matrix job is the
  authoritative Windows validation and is covered in §4 below (GitHub-side CI history),
  not by this local run.

## 4. GitHub Actions / CI posture (live, via GitHub API)

- `origin/main` HEAD (`be57a4d`) — **CI run #1837, `ci.yml`, status completed,
  conclusion success** (both `ubuntu-latest` and `windows-latest` legs, per the
  workflow's matrix).
- `ci.yml` itself is well-hardened: pinned actions (SHA + version comment) throughout;
  a fast `workflow-lint` gate (actionlint + zizmor, pinned versions) runs before the
  expensive matrix; `dependency-review` on PRs; exact `pip==26.1.2` pin for the CVE
  mitigation; cached torch CPU wheel keyed to the pin; hermetic env prep (stub
  `soul.md`, `GROK_API_KEY=dummy`); real RAG smoke as a separate step before
  unit+coverage; concurrency cancellation scoped to non-`main` refs (main keeps full
  history); a real-socket Ollama mock-smoke job separate from the monkeypatched
  client tests; an opt-in `deepagents-harness` job; a Postgres-backend job against a
  digest-pinned `pgvector/pgvector:pg16` image; and a dynamically-discovered
  skill-verification matrix that is intentionally non-blocking
  (`continue-on-error: true`) so environment-flaky e2e smokes don't gate merges.
- No functional or configuration defects found in `ci.yml` itself during this pass.

## 5. Code + security review of the last 3 major merges to main

Three independent reviews were run in parallel, each reading the actual diff and
current file contents (not the PR description). Full findings below; only
**verified** items (quoted against real line numbers) are included — nothing here is
speculative.

### 5.1 PR #598 — "isolated Codex PR review workflows" (`af4f645..be57a4d`)

**Verdict: sound work.** Correctly closes the classic pwn-request, script-injection,
TOCTOU, and token-exposure traps for an LLM-based PR reviewer running on
`pull_request_target`. Verified clean: no `${{ github.event.* }}` interpolated into
any `run:`/script body; candidate PR code is checked out but never executed (only a
trusted-base copy of `check_invariants.py` runs, with `python -I` keeping candidate
paths off `sys.path`); immutable-SHA checkouts (not `refs/pull/N/merge`); least
privilege (`contents: read` for the reviewer, `issues: write`-only for the poster,
`pull-requests: write` removed from the codex.yml poster in this PR);
`persist-credentials: false` everywhere; all actions SHA-pinned;
`codex-version` pinned to `0.144.6` (not `latest`).

Residual findings (none block, all worth a small follow-up):
- **F1 (medium)** — `codex.yml`'s trigger only gates on the *commenter* being the repo
  owner, not on the PR author/fork (`pr-review.yml` does check `head.repo`). A fork PR
  a maintainer comments `@codex` on puts attacker-controlled diff content in the
  reviewer's context. The reviewer itself holds no write token (the "isolated" claim
  holds for execution/write-access), but `codex.yml`'s poster job posts
  `CODEX_FINAL_MESSAGE` **verbatim** with no length cap or untrusted-content marker,
  unlike `pr-review.yml`'s validated poster (JSON schema check, 60,000-char cap,
  verdict cross-check, fail-closed). Recommend adding a length cap + an "advisory,
  auto-generated over untrusted PR content" header to codex.yml's poster, and/or a
  `head.repo.full_name == github.repository` gate if fork review isn't intended.
- **F2 (low)** — The Semgrep `pull-request-target-code-checkout` rule is suppressed
  **repo-wide** (not per-file) as of `239ce40`, replacing correctly-scoped inline
  `# nosemgrep:` comments from the intermediate commit `5c38f98`. Verified the stated
  compensating control is real: zizmor (pinned 1.27.0) runs in `ci.yml` at
  `--min-severity=high` and its `dangerous-triggers` audit fires on any new
  `pull_request_target` usage — so a future dangerous workflow still fails CI unless
  explicitly annotated. Defensible trade, but note it in `ZIZMOR_FINDINGS_PLAN.md` so
  the repo-wide scope is a deliberate, tracked decision rather than implicit.
- **F3 (low)** — `OPENAI_API_KEY` is available in the same job that processes
  untrusted PR content in both workflows; exfil is constrained by Codex's read-only
  sandbox mode and pinned CLI version, but worth re-verifying against the specific
  pinned `codex-version` if it's ever bumped.
- **F4 (low)** — `pr-review.yml`'s `paths` filter includes `.codex/prompts/pr-review.md`
  but not `.codex/prompts/pr-agent.md`, so edits to the comment-agent's trust-boundary
  prompt get no automated review.
- **F5/F6 (info)** — `codex.yml` lacks a top-level `permissions: {}` default (relies on
  per-job blocks only); the verdict cross-check regex in `pr-review.yml` is
  format-brittle but fails closed (acceptable).
- **F7 (info)** — `claude.yml` (unchanged in this PR's range) still uses the
  *pre-#598* pattern: an LLM agent (`anthropics/claude-code-action`) holds
  `pull-requests: write` + `issues: write` directly, on the same owner-gated
  `issue_comment` trigger. Recommend a follow-up PR applying the #598 split
  (read-only reviewer job → validated poster job) to `claude.yml` too.

### 5.2 PR #597 — "race-safe O_EXCL lock file replaces rmdir/mkdir reclaim" (fixes #587)

**Verdict: sound fix, safe as merged.** Traced the full syscall sequence: the O_EXCL
fast-path create is atomic on POSIX and Windows; the takeover-file serialization +
re-validate-before-remove sequence provably closes the original #587 TOCTOU **for its
stated precondition** (a crashed lock holder) — a live holder cannot have its lock
stolen by an honest reclaimer under default config. 9/9 new tests in
`tests/test_sync_lock.py` pass locally, including a genuine multi-process race test
(not mocked).

Residual findings, all edge/platform cases, none reachable under normal single-operator
operation:
- **Medium** — On Windows, a concurrent reader can make the reclaimer's `os.remove()`
  raise a raw `PermissionError` (only `FileNotFoundError` is caught) that escapes
  untyped past the `SyncError` hierarchy and can surface as exit code 1 (collides with
  the documented "safety abort" exit code). Fix: catch `OSError`, translate non-ENOENT
  into the typed busy error.
- **Medium** — An orphaned `.takeover` marker (crash mid-reclaim) has no staleness
  aging of its own, permanently wedging future auto-reclaim attempts — a new failure
  mode the old rmdir/mkdir scheme didn't have. Fix: age out a stale `.takeover` file
  the same way the main lock is aged.
- **Medium (degraded-config only)** — the documented "a reclaimer can never delete a
  live run's lock" guarantee only holds when reclaimer and holder agree on the
  staleness threshold; a live run under `sync_timeout_sec: 0` or a subsequently-lowered
  threshold can theoretically be reclaimed. Pre-existing property of the old lock too;
  docstring overstates the guarantee slightly.
- **Medium (test quality)** — the new race test uses a real `time.sleep(0.25)` to
  prevent a late "loser" from double-winning, which is exactly the "real sleep racing
  a timeout" pattern CLAUDE.md §4 bans elsewhere; on a loaded CI runner (esp. the
  8-process spawn on `windows-latest`) this could flake. A companion `results.get()`
  has no timeout, so an unexpected exception in a worker can hang the whole test
  process rather than fail it. Recommend an `mp.Event`-coordinated hold instead of the
  sleep, and a bounded `results.get(timeout=...)` with explicit exitcode assertions.
- **Low** — metadata-write failure (e.g. ENOSPC) after the O_EXCL create propagates
  instead of degrading, contradicting the function's own "best-effort" docstring;
  dead `ANALYSIS.md` doc reference; minor loss of forensic logging (age no longer
  logged on reclaim).

### 5.3 PRs #592/#593/#594 (+ #595 review-fix merge) — sync hardening, fsconnect gates, guardrails

**Verdict: solid hardening, well-tested.** Confirmed clean: module isolation (I6)
untouched and `invariant-guard` still 28/28; chunk sanitization preserved in the
indexer diff; audit privacy preserved (errors redacted to exception type name only,
never message/argv/remote spec — verified against the actual redaction code, not
assumed); retries correctly gated to exit-code-5-only with a bounded wall-clock
budget; Windows write-refusal fires before any directory creation; strict-bool config
validation covers all declared gates; path-traversal/UNC/ADS guards hold for both
staging and pruning. Tests in this range are behavior-focused, deterministic (no real
sleeps), and include two standouts that pin real contract-drift bugs rather than
testing stubs (a stub/real-signature mismatch test, and a shipped-config-matches-
template test).

Residual findings — the material ones are **failure-path lifecycle gaps**, not
exploitable security holes under the single-operator/loopback threat model:
- **Medium** — if `SyncError` raises *after* files were already partially copied, the
  auto-reindex (exit-10) signal never fires because no `SyncResult` exists to carry
  the "corpus changed" evidence — the retrieval index can go stale until the next
  clean run touches the same files or an operator manually reindexes after reading
  `audit.jsonl`.
- **Medium** — a raising post-sync check (subprocess timeout) after a **fully
  successful** sync destroys that run's per-file audit rows and forces a `sync_failed`
  summary for a run whose rclone exit code was 0 — compounding the above (files
  really were copied, evidence is lost twice over).
- **Medium** — `fsconnect`'s incremental indexer prunes any staged file absent from
  the current eligible set; an unmounted-but-present share (empty directory, not a
  missing one) is indistinguishable from "every source file deleted" and triggers a
  full prune of previously-staged content. A single transient read error on one file
  also immediately prunes that file's staged copy (by design, per an existing pinned
  test) rather than tolerating a few failed runs first.
- **Low (adversarial-flavored)** — the fsconnect prune "ownership manifest" cache file
  defaults to living inside the synced corpus tree itself
  (`data/corpus/fsconnect/.fsindex_cache.json`) and `sync/filters.py`'s hardened
  excludes don't exclude it — a poisoned Dropbox-side copy of that cache file could
  influence what the pruner considers "ours to delete," within the staging directory's
  confinement (verified `split_components` still rejects `..`/absolute/UNC/ADS
  regardless).
- **Low** — POSIX cron scheduling's shell-metacharacter escaping (fixed for the shell
  layer in #592) doesn't cover `%`, which `crontab(5)` itself treats as a
  newline/stdin separator before any shell involvement; a path containing `%` can
  silently truncate or split the scheduled cron line. Windows' `.bat` quoting was
  correctly hardened against the equivalent issue; POSIX cron was not.
- **Low** — two places (`agentic/fsconnect/indexer.py:_run_reindex`, and the pruning
  cache-name comparison) reintroduce narrower versions of bug classes #592 fixed
  elsewhere (missing `--config` propagation to a reindex subprocess; case-sensitive
  reserved-filename comparison on a case-insensitive filesystem).

## 6. Overall security assessment

**Static invariant/dependency posture: clean.** `invariant-guard` (28/28) and
`dep-guard` (0/0) both pass with no exceptions or suppressions needed. The six
security invariants (RAG-first, topology=policy, triple-gated external fallback,
audit convergence, soul governance, module isolation) are enforced by graph
topology/import structure, not by convention, and this was verified both statically
and live (fail-closed auth confirmed against a running server, not just unit-mocked;
injection filter confirmed live returning HTTP 400; soul.md confirmed byte-identical
across a full smoke run).

**No critical or high-severity findings** were confirmed across the three merges
reviewed. The findings that exist cluster into two classes:
1. **CI/Actions-layer residue (PR #598)** — the LLM-reviewer isolation pattern is
   sound where it counts (no execution of untrusted code, no write token in the
   reviewer), with the one real gap being an unvalidated verbatim-post path in
   `codex.yml` reachable only if a maintainer invokes `@codex` on a fork PR, plus the
   pre-existing `claude.yml` workflow not yet having received the same treatment.
2. **Failure-path/lifecycle gaps in the sync and fsconnect layers** (PRs #592-595,
   #597) — these are reliability/data-integrity gaps (stale index after a partial
   failure, full-prune-on-empty-mount, a Windows-only untyped-exception escape, an
   orphan-able takeover marker) rather than exploitable vulnerabilities. All are
   scoped by the single-operator/loopback threat model documented in
   `docs/THREAT_MODEL.md`; none crosses a trust boundary the threat model claims to
   defend.

**Test-suite security-relevant coverage gaps worth calling out here specifically**
(full detail in the companion test-suite PR): the fail-soft dual-leg retrieval
degrade path (`retrieval/hybrid_search.py:210-220`) and the corpus-indexer's
symlink-escape guard (`retrieval/indexer.py:71-76`) both have **zero** direct test
coverage despite being reliability/security-relevant code paths — these are the two
highest-priority items in the companion report.

**Recommendation:** no revert or urgent hotfix is warranted for anything found in
this pass. Suggested follow-up PRs, roughly in priority order: (a) cap/mark
`codex.yml`'s verbatim poster output and consider porting the #598 isolation pattern
to `claude.yml`; (b) close the sync partial-failure/post-check-failure reindex-signal
gaps; (c) add an empty-vs-missing-mount guard to fsconnect pruning; (d) the Windows
`PermissionError`/orphaned-`.takeover` edge cases in the new lock code; (e) the two
zero-coverage security-relevant retrieval/indexer branches noted above (tracked in the
companion test-suite PR).

## 7. Evidence artifacts

- Invariant-guard and dep-guard: full pass/fail tables in §1 (all green).
- Full pytest log: coverage table + 89.50% total (this session's scratch copy; not
  committed — regenerate with the command in §2).
- Real RAG smoke output: 4/4 queries, scores 0.0325–0.0333 vs the 0.028 gate.
- Live server: `/health`, `/query`, `/soul`, `/ops/agentic` responses quoted above;
  `terminal_emulation.py` 8/8 pass.
- CI: run #1837 on `be57a4d`, both OS legs, conclusion `success`.

---

*This report and the companion `Test_Suite_Defects_And_Fix_Spec_2026-07-21.md` are
the deliverables for the requested main-branch verification, last-3-merges
code/security review, and overall security assessment pass. No code was modified as
part of producing this report — it is audit-only.*
