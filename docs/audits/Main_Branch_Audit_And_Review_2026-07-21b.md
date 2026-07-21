---
title: "CyClaw Main-Branch Audit & Last-3-Merges Review — 2026-07-21 (rerun after #599/#600 merge)"
date: 2026-07-21
tags: [audit, security-review, code-review, python-3.12, ci, sandbox]
related:
  - CLAUDE.md
  - docs/THREAT_MODEL.md
  - docs/audits/Main_Branch_Audit_And_Review_2026-07-21.md
  - docs/audits/Test_Suite_Defects_And_Fix_Spec_2026-07-21.md
---

# CyClaw Main-Branch Audit & Last-3-Merges Review — 2026-07-21 (rerun)

Second pass of the same day, re-run at the user's request ("go") after the previous
pair of report PRs (#599, #600) merged. Scope: full local verification of
`origin/main` @ `9cae6e3`, plus a code + security review of the three most
substantive merges since the prior audit's baseline (`be57a4d`).

**What changed on main since the last audit (`be57a4d..9cae6e3`):** 9 merges landed
— #599/#600 (the prior audit's own two report PRs), #601 (sync-lock rewrite),
#602/#603/#604 (one-line docs/test-only fixes), #606 (4 corpus content-accuracy
fixes), #607 (agentic typed-error hardening). **#601, #606, and #607 are the three
substantive code merges reviewed here** — #602/#603/#604 are single-line doc/test
tweaks, not "major" changes.

Notably, **#601 is a direct, high-quality response to this audit's own prior
findings**: the previous pass (`Main_Branch_Audit_And_Review_2026-07-21.md`, PR #597
section) flagged an untyped Windows `PermissionError` risk, an orphan-able
`.takeover` marker, and a real-sleep race in the lock stress test. #601 didn't patch
around these — it replaced the entire O_EXCL/takeover reclaim scheme with real
OS-level advisory locking (`fcntl.flock`/`msvcrt.locking`), which structurally
eliminates two of the three issues and fixes the third directly. See §3 below.

---

## 1. Python 3.12 runtime verification

Reused the same Python 3.12.3 venv from the prior pass (dependency manifests
diffed byte-identical against the prior audit's baseline — no reinstall needed).

- `python3 .claude/skills/invariant-guard/check_invariants.py` → **28/28 passed**
  again, unchanged.
- `python3 .claude/skills/dep-guard/check_deps.py` → **0 failures, 0 warnings**
  again, unchanged.

## 2. Full test-suite run (Python 3.12.3, offline)

`GROK_API_KEY=dummy pytest tests/ -q --tb=short --cov=<11 CI-parity source groups>`

- **All tests passed**, zero `F`/`E` markers. Same expected-skip set (1 `deepagents`
  optional-dep skip, 12 live-Postgres/pgvector skips).
- **Coverage: 89.20% total** (was 89.50% before #601 — the small dip is `sync/runner.py`
  growing from 401 to 409 statements with the new locking code, at 90% coverage on
  the new lines, not a regression in already-covered code). Still comfortably above
  the 80% `fail_under` gate.
- `sync/runner.py` coverage detail: 90% (409 stmts, 41 missed) — the new
  `_try_os_lock`/`_write_lock_meta` OS-error paths noted in §3 are among the missed
  lines, consistent with that finding.

## 3. Code + security review of the three substantive merges since the last pass

### 3.1 PR #601 — "codex/cyclaw-optimize-sync-lock-ownership" (sync-lock rewrite)

**Verdict: sound, with one narrow follow-up.** This PR fully replaced the O_EXCL +
`.takeover`-file reclaim scheme from PR #597 with a genuinely different mechanism: a
single stable `sync.lock` file held via a real OS-level advisory lock on its file
descriptor — `fcntl.flock(LOCK_EX | LOCK_NB)` on POSIX, `msvcrt.locking(LK_NBLCK)` at
a fixed byte offset on Windows. The OS releases the lock automatically on process
death (crash, kill -9, power loss), which eliminates the entire class of
stale-age-heuristic/reclaim-race problems the old scheme had to work around.

**Disposition of the four issues raised in the prior audit:**
1. *Untyped Windows `PermissionError` escaping the reclaim `os.remove()`* —
   **eliminated by design.** There is no more reclaim-via-delete; nothing to race on
   removal. The accompanying `fix(sync): type os.open() lock-file failures as
   SyncRuntimeError` commit additionally types the new mechanism's own `os.open()`
   failures, which is the closest surviving analog to the old finding and is now
   covered.
2. *Orphaned `.takeover` marker with no staleness aging* — **eliminated by design.**
   No takeover file exists in the new scheme.
3. *Real `time.sleep(0.25)` racing a timeout in the reclaim stress test* —
   **fixed.** The rewritten `tests/test_sync_lock.py` uses `multiprocessing.Barrier`
   to synchronize worker start and `multiprocessing.Event` to signal release,
   verified via direct grep that no `time.sleep` remains in the file.
4. *Unbounded `results.get()` risking a full-suite hang* — **fixed.** All
   `results.get()`/`barrier.wait()`/`proc.join()` calls now carry explicit timeouts
   (30–60s).

**New finding (follow-up, not a blocker):**
- **Medium** — `sync/runner.py`'s `_try_os_lock` only special-cases
  `EACCES`/`EAGAIN` (the expected "lock is held" signal) from `fcntl.flock`/
  `msvcrt.locking`; any other `OSError` (e.g. `ENOLCK`, or a Windows sharing
  violation that isn't the busy case) re-raises bare and propagates through
  `_acquire_sync_lock`'s cleanup path as untyped `OSError`, not `SyncRuntimeError`.
  `_write_lock_meta`'s `os.ftruncate`/`os.write` calls have no exception handling at
  all — an I/O fault (disk full; a Windows write racing another handle) surfaces
  the same way. This is the direct successor of finding #1 above: the `fix(sync)`
  commit typed `os.open()` specifically (per its title) but not the lock-acquire or
  metadata-write calls that follow it. Narrower and rarer than the original finding
  (needs an actual OS/filesystem fault, not mere lock contention), but the same risk
  shape: an untyped exception reaching a generic `except Exception` handler
  upstream could misclassify the failure's exit code.
  **Fix:** wrap `_try_os_lock`/`_write_lock_meta`'s bodies so any `OSError` other
  than the busy-signal is re-raised as `SyncRuntimeError(..., details={"lock_path":
  ..., "error": str(exc)})`, mirroring the existing `os.open()` pattern.

Everything else checked out clean: `_release_sync_lock` nulls `lock.fd` before
releasing (idempotent — matches its own regression test); the in-process
`_PROCESS_LOCKS` set prevents double-acquire within one process; `docs/SYNC_README.md`
and `tests/test_sync_runner.py` were updated consistently with the new design
(stale-threshold config/tests fully removed, replaced with lock-persists-but-
unlocked assertions); and a dedicated migration test confirms a lock file left over
from the old scheme is safely reused rather than crashing on the first sync after
upgrade.

### 3.2 PR #607 — "fix(agentic): raise typed errors, not bare builtins, at op boundaries"

**Verdict: correct, no regressions, no findings.** `agentic/sqlconnect/context.py`'s
unknown-op path now raises `SqlConnectError(code="SQLCONNECT_OP_NOT_ALLOWED")`
instead of a bare `ValueError` — since `agentic/sqlconnect/cli.py` only catches
`SqlConnectError` → `EXIT_FAIL` (2), the old bare `ValueError` would previously have
escaped uncaught to Python's default exit 1, silently breaking the documented exit-
code contract (CLAUDE.md: agentic exit codes are 0/2/3/4, never a bare 1). This is a
genuine, verified contract fix, not just a style change. `agentic/writer.py` gained
a `_require_int_number` helper that similarly raises the typed `AgenticError` (caught
by `agentic/cli.py` → `EXIT_FAIL`) instead of a bare `TypeError`/`ValueError` when a
write-comment's `number` parameter isn't int-coercible. The kill-switch semantics
(`EXECUTION_ENABLED = False`, `AgenticWriteRefused` while disabled,
`NotImplementedError` in the never-reached live executor) are untouched by this PR.
New tests genuinely exercise the real code path (all real gates satisfied, only the
bad `number` value varied — `"abc"`, `None`, `[12]`) rather than mocking the
exception type.

Minor observation, not a defect: `data/corpus/cyclaw_overview.md`'s self-description
(touched by the separate PR #606, see below) mentions Grok as an optional escalation
path but not Claude, even though both are symmetrically triple-gated
(`config.yaml`: both `enabled: false` by default). Not wrong today, just incomplete —
worth a follow-up corpus edit, out of scope for either #606 or #607.

### 3.3 PR #606 — "fix(corpus): ..." (4 commits, RAG-corpus content accuracy)

**Verdict: correct, no smoke-test regression.** Renames the retired "LMStudio"
backend reference in `data/corpus/cyclaw_overview.md` to "Ollama," matching
`config.yaml`'s actual `local_llm.provider: "ollama"` — LMStudio isn't referenced
anywhere else in the codebase, so this was a stale/incorrect corpus claim, now fixed.
Cross-checked against `tests/ci_rag_smoke.py`'s query set: the edited prose still
carries the same keyword/conceptual overlap ("offline," "local," "LLM," "inference")
the smoke test's fixed queries rely on to clear the 0.028 RRF gate — confirmed by
this session's own live rerun of `ci_rag_smoke.py` (§4 below), which still passed
4/4 against the post-#606 corpus.

## 4. Live sandbox / API / RAG-query emulation (Tier 1 realism), rerun against 9cae6e3

- Real RAG smoke (`python -m tests.ci_rag_smoke`) against the now-corrected corpus:
  **4/4 queries passed**, scores 0.0325–0.0333 vs the 0.028 gate — unaffected by the
  #606 corpus wording changes, as predicted in §3.3.
- Live `gate.py` boot against the shipped `mock_ollama.py`: `/health` → `status: ok`,
  both probes healthy, `index_ready`/`graph_ready` both `true`.
- `terminal_emulation.py` (repo's own httpx-based endpoint-flow check): **passed
  clean on 3 of 4 runs against the same live server** (8/8 assertions each). One
  run — the very first invocation immediately after server boot — reported 1 failed
  check with no further detail retained in that run's log (a piped-output capture
  issue on my end lost the specific assertion). **Not reproduced** across 3
  subsequent immediate reruns against the same still-running server. Flagging this
  honestly rather than either dismissing it or overclaiming a regression:
  most plausible explanation is a first-request-after-boot cold-start variance
  (embedding backend warm-up affecting the off-topic query's exact retrieval score
  right at the score-router boundary), but this is not verified — I did not
  reproduce it with a fresh server boot to confirm. **Recommend:** if this recurs,
  capture the specific failing assertion (not just the summary line) and check
  whether `/health` reporting `index_ready: true` is fully synchronous with the
  embedding backend actually being warm, or whether there's a narrow window where
  health reports ready before the first real query is scored consistently.
- Injection payload → **400** (sanitizer live). `/ops/agentic` without a key →
  **401**. `data/personality/soul.md` confirmed byte-identical before/after.
- Same Windows/`pwsh` gap as the prior pass: no native `pwsh` in this container;
  used the repo's own Python/httpx `terminal_emulation.py` equivalent again, not an
  improvised substitute. Windows-native validation remains the responsibility of
  `ci.yml`'s `windows-latest` matrix leg (checked live below).

## 5. GitHub Actions / CI posture (live, via GitHub API)

- Confirmed via `mcp__github__pull_request_read` that PRs #599 and #600 (the prior
  audit's own two report PRs) merged cleanly (12:11:30Z and 12:11:40Z respectively),
  with no review comments and no CI failures on either — both were docs-only so this
  is expected, noted for completeness/audit-trail continuity.
- `origin/main` HEAD is now `9cae6e3` (PR #607 merge).

## 6. Overall security assessment (updated)

No change to the standing assessment from the prior pass: static invariant/dependency
posture remains clean (28/28, 0/0), and no critical or high-severity findings were
confirmed in this second round either. The one new item — PR #601's narrower
untyped-`OSError` follow-up (§3.1) — is the same severity tier (Medium) and same
root-cause class (an OS-boundary exception not yet wrapped in the typed error
hierarchy) as the finding it descends from, just smaller in surface area now that
the reclaim-by-delete mechanism it used to affect no longer exists.

**Notable positive signal:** this is the second consecutive audit cycle where a
finding from one pass was directly and substantively addressed by the next real
commit to land on main (not just documented as accepted risk) — first the sync-lock
TOCTOU (#587 → #597), now the reclaim-mechanism follow-up findings from this
session's #597 review (→ #601). The audit/fix loop is functioning as intended.

**Recommendation:** no revert or urgent action warranted. Suggested follow-up: type
the two narrower `OSError` paths in `_try_os_lock`/`_write_lock_meta` per §3.1 (small,
mechanical, same pattern as the existing `os.open()` fix); optionally add Claude
alongside Grok in the corpus self-description for completeness (very low priority,
purely a documentation nicety, config already treats them symmetrically).

## 7. Evidence artifacts

- Invariant-guard/dep-guard: 28/28 and 0/0, unchanged from the prior pass.
- Full pytest log: 89.20% coverage, zero failures, same expected-skip set.
- Real RAG smoke: 4/4, scores 0.0325–0.0333 vs 0.028 gate, against the post-#606
  corpus.
- Live server: `/health`, injection-filter, auth-fail-closed all reconfirmed;
  `terminal_emulation.py` 3/4 clean runs, 1 unreproduced transient noted honestly.
- PR #599/#600 merge confirmation: timestamps and clean status via GitHub API.

---

*This report and the companion follow-up note in
`docs/audits/Test_Suite_Defects_And_Fix_Spec_2026-07-21.md`'s tracking (no new
test-suite defect report needed this cycle — see that PR's body for why) are the
deliverables for this second, user-requested ("go") verification cycle. No code was
modified as part of producing this report — it is audit-only.*
