---
title: "CyClaw Test-Suite Defect Tracking — 2026-07-21 update (rerun after #599/#600)"
date: 2026-07-21
tags: [tests, audit, fix-spec, coverage]
related:
  - CLAUDE.md
  - docs/audits/Main_Branch_Audit_And_Review_2026-07-21b.md
  - docs/audits/Test_Suite_Defects_And_Fix_Spec_2026-07-21.md
---

# CyClaw Test-Suite Defect Tracking — 2026-07-21 update

Second-cycle companion to `Test_Suite_Defects_And_Fix_Spec_2026-07-21.md` (the
original, still-unaddressed backlog — see that file for the full priority-tiered
spec). This update is **short by design**: between the first audit cycle and this
one, only four test files actually changed (`tests/test_sync_lock.py`,
`tests/test_sync_runner.py`, `tests/test_agentic_writer.py`,
`tests/test_sqlconnect_context.py`, via PRs #601 and #607), and both were covered by
this cycle's code+security review agents rather than a separate blind test-audit
pass — re-running the full 75-file audit against ~71 unchanged files would have
reproduced the same findings already tracked in the original spec document for no
new information.

**Status of the original spec's tier-4 item:** `tests/test_sync_lock.py`'s
real-`time.sleep(0.25)` race (tier 4 in the original spec) is **now fixed** — PR
#601 rewrote this test using `multiprocessing.Barrier`/`Event` instead of a sleep,
and bounded every previously-unbounded `results.get()`/`join()` call with an explicit
timeout. This item can be marked resolved in any future consolidation of the
original spec. All other tiers (1, 2, 3, 5, 6) in the original document are
**still open** — nothing in this cycle's changes touched
`retrieval/hybrid_search.py`, `retrieval/indexer.py`, `tests/test_agentic_isolation.py`,
or `tests/test_personality.py`, so those findings stand exactly as documented.

## New item surfaced this cycle

**`tests/test_sync_lock.py` / `tests/test_sync_runner.py` — missing coverage:
`_try_os_lock`'s non-busy `OSError` path and `_write_lock_meta`'s I/O-failure path
are untested.**

This cycle's review of PR #601 (full detail in the companion audit report, §3.1)
found that the new OS-level advisory-lock implementation in `sync/runner.py`
correctly types `os.open()` failures as `SyncRuntimeError`, but two adjacent paths
remain untyped and untested:

- `_try_os_lock` only special-cases `errno.EACCES`/`EAGAIN` (the expected
  "lock is held by someone else" signal) from `fcntl.flock`/`msvcrt.locking`. Any
  other `OSError` (e.g. `ENOLCK`, or a Windows sharing-violation variant that isn't
  the plain busy case) is re-raised bare and reaches `_acquire_sync_lock`'s cleanup
  block as an untyped `OSError`.
- `_write_lock_meta`'s `os.ftruncate`/`os.write` calls have no exception handling at
  all — an I/O fault (disk full, a Windows write racing another handle) surfaces the
  same way.

**Recommended fix (source):** wrap `_try_os_lock`/`_write_lock_meta` so any
`OSError` other than the busy-signal is re-raised as `SyncRuntimeError(...,
details={"lock_path": ..., "error": str(exc)})`, mirroring the existing `os.open()`
handling.

**Recommended fix (tests, this file's actual subject):** add to
`tests/test_sync_lock.py`:
```python
def test_try_os_lock_reraises_unexpected_oserror_as_typed(monkeypatch):
    # Simulate a non-busy OSError from the platform lock call (e.g. ENOLCK) and
    # confirm it surfaces as SyncRuntimeError, not a bare OSError, once the
    # source fix above lands. Until then, this test should be written to
    # *document* the current untyped-escape behavior (xfail or an explicit
    # assertion that it currently raises OSError), then flipped to assert
    # SyncRuntimeError once the fix is applied — don't skip writing the test
    # just because the fix isn't in yet.
    ...

def test_write_lock_meta_ioerror_does_not_leave_untyped_exception(monkeypatch):
    # Force os.write to raise OSError (e.g. simulate ENOSPC) during
    # _write_lock_meta and confirm the acquire path surfaces a typed
    # SyncRuntimeError, not a bare OSError.
    ...
```
This is a small, self-contained addition — implement alongside the source fix in
the same follow-up PR (both source and test changes belong in one reviewable PR per
this repo's own convention, unlike the original spec's items which were
intentionally test-only).

## Confirmed clean (no action needed)

- `tests/test_agentic_writer.py`'s new `test_non_integer_number_raises_typed_error`
  and `tests/test_sqlconnect_context.py`'s corresponding case (added by PR #607) are
  genuine exercises of the real typed-error path — all real gates satisfied, only
  the offending parameter varied, exception type and `.code` both asserted. No
  findings.
- `tests/test_sync_runner.py`'s updates (accompanying #601) were checked against the
  new lock design and found consistent — stale-threshold config/tests fully removed
  rather than left as dead assertions against a removed mechanism, replaced with
  lock-persists-but-unlocked assertions matching the new file-descriptor-based
  design.

## Sequencing note

Land the new item above (source fix + its two tests) as its own small follow-up PR,
separate from any future work on the original spec's tier 1-3/5/6 backlog — they
touch unrelated files (`sync/` vs `retrieval/`/`agentic/`/`gate.py`) and this repo's
PR convention favors one concern per PR.
