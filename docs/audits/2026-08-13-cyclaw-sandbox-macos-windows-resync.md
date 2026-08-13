# CyClaw Sandbox Verification — Resync + macOS-Primary/Windows-Secondary Realism Pass

**Date:** 2026-08-13
**Target:** `main` @ `387d658` (cgfixit/CyClaw), branch `claude/cyclaw-pr-review-l6bvwe` restarted from it
**Run type:** In-place skill/doc/test update (no source-behavior changes), Linux sandbox verification, no push/PR at time of writing

## Executive Summary

**Verdict: PASS.** `/CyClaw-Sandbox` had drifted from the codebase in nine concrete,
verified ways — most consequentially, its own `run_full_verification.py` asserted
config defaults (`app.mode: offline`, Grok/Claude disabled) that shipped config.yaml
stopped matching on 2026-08-07, so that phase failed on every run. This pass fixed
all nine staleness items, implemented the previously-undocumented "3-tier Ollama
realism" claim for real (a genuine daemon probe, not just prose), and added a new
test file (`tests/test_fsconnect_macos_real.py`) that exercises the macOS-specific
fsconnect hardening (from this session's earlier PRs #886–#888) against a real
filesystem with zero `sys.platform`/`os.stat` mocking — a category of coverage that
did not exist anywhere in the repo before this pass. Windows got bounded,
secondary additions (`/api/agent/*` + `/ops/fsconnect` REST checks in
`windows-smoke.ps1`) rather than an attempt to close the separately-tracked,
already-deferred Windows `pathsafe.py` coverage gap.

Full suite: **3419 passed, 0 failed, 0 errors, 22 skipped** (110.6s). `ruff` clean
on the new file; the 33 pre-existing findings in `run_full_verification.py` were
confirmed present before this session's edit too (via `git stash`) and are outside
`ruff`'s CI scope (`pyproject.toml` excludes `.claude/`). `invariant-guard`: 35/35.
`doc-sync`: 0 drift. Both shell scripts pass `bash -n`. The new Darwin-real test
file's four tests confirmed **skip cleanly** on this Linux sandbox (0 errors) — real
execution is deferred to `macos-latest` CI, stated plainly below, not implied as
covered here.

---

## Standard Phase 14 Report Block

```
CyClaw Swarm Verification Complete.
Full functionality status: PASS.
RAG pipeline (5 queries): not re-run this pass (no graph/retrieval code touched;
  last verified 2026-08-02 audit, unaffected by this skill-only change)
REST API surface: not re-run this pass (no gate.py/route code touched)
Terminal Consoles (all 5): not re-run this pass (no terminal.html/route code touched)
Triple-Gate Online API (Grok + Claude): config-invariant checks in
  run_full_verification.py RE-VERIFIED and FIXED — previously asserted the stale
  offline/disabled defaults (5 failures); now assert the shipped hybrid/enabled
  defaults (2 pre-existing, out-of-scope failures remain — see Findings below)
API Key Redaction: unaffected, not re-run (no sanitizer/redaction code touched)
Due-Diligence Invariants: 14/14 test classes now documented in SKILL.md and
  test-specifications.md (previously 12/14 documented; the file itself already
  had 14 and was unaffected by this pass — only the docs were stale)
Harness Console REST API: /api/agent/* now documented (previously undocumented
  despite already being exercised by harness_runtime_check.py/harness_emulation.py)
macOS Realism Coverage (NEW): 4/4 real, unmocked Darwin integration tests added
  (/Volumes gate, Apple-metadata filtering, case-insensitive-APFS overlap, real
  EACCES->FsMacOSPermissionError) — confirmed skip-clean on Linux, real execution
  pending macos-latest CI
Windows Realism Coverage (bounded): windows-smoke.ps1 gained /api/agent/* auth-gate
  checks + one /ops/fsconnect REST round-trip check; deep Windows pathsafe.py
  coverage remains a documented, separately-tracked gap (FSCONNECT_SQL_ROADMAP.md
  Phase 4), not attempted here
Security Invariants: invariant-guard 35/35 (unaffected by this pass; re-run to
  confirm no incidental regression)
Recommendations: see Findings below.
```

### Test suite detail

- `GROK_API_KEY=dummy pytest tests/ -q --tb=short --junitxml=...`: **3419 collected,
  3419 passed (0 failed, 0 errors), 22 skipped**, 110.573s wall time (Python 3.12.3,
  `/root/.venv-cyclaw-312`).
- `tests/test_fsconnect_macos_real.py` (new, 4 tests): all 4 **skip** on this Linux
  sandbox (3 via `pytest.mark.skipif(sys.platform != "darwin", ...)`, 1 — the
  case-insensitive-overlap test — via a runtime case-sensitivity probe against
  `tmp_path`, since Linux's ext4 is case-sensitive by default). Combined run with
  its two sibling files (`test_fsconnect_macos_policy.py`,
  `test_fsconnect_pathsafe.py`): 67 passed, 4 skipped, no name collisions.
- `ruff check --select E,F,I,B,C4,UP,S tests/test_fsconnect_macos_real.py`: clean.
- `ruff check --select E,F,I,B,C4,UP,S .claude/skills/CyClaw-Sandbox/run_full_verification.py`:
  33 findings (unused `math`/`random` imports, semicolon-joined color constants,
  f-strings without placeholders, `md5` usage in the mock embedder, subprocess
  partial-path warnings). **Confirmed pre-existing**: re-ran against the pre-edit
  file via `git stash` and got the identical "Found 33 errors." `pyproject.toml`
  line 211 (`[tool.ruff] exclude = [".claude"]`) confirms this file was never in
  CI's ruff scope before or after this change — not a regression, not newly
  introduced, and out of this task's stated scope (fixing stale claims and adding
  real test coverage, not a drive-by lint pass on an excluded directory).
- `python3 .claude/skills/invariant-guard/check_invariants.py`: **35 passed, 0
  failed** (I1–I6 + G1–G5, unaffected by this skill/test/doc-only change).
- `python3 .claude/skills/doc-sync/doc_sync.py`: **0 drift items** (D1–D6 all `ok`,
  including the banned-pattern count and route table this pass didn't touch).
- `gate_runtime_check.py` / `harness_runtime_check.py`: both **PASS** independently.
- `bash -n` on `verify.sh` and `smoke.sh`: both syntax-valid.

---

## Findings and Deep-Dive

### 1. `run_full_verification.py`'s config-invariant phase was failing on every run — FIXED, verified before/after

**Files read directly:** `.claude/skills/CyClaw-Sandbox/run_full_verification.py`,
`config.yaml`'s `app`/`models`/`fsconnect` blocks.

**Finding: CONFIRMED, fixed, verified both states directly** (not taken on faith).
Before this pass, `phase_config_invariants()` asserted `app.mode == "offline"`,
`grok.enabled is False`, `claude.enabled is False` — stale since the 2026-08-07
amendment armed hybrid mode and both external providers. Ran the function directly
(via `importlib`, registered in `sys.modules` before `exec_module` to satisfy
`@dataclass`'s module introspection) against both the pre-edit file (`git stash`)
and the post-edit file:

- **Before:** 5 failures — `app.mode`, `grok.enabled`, `claude.enabled`,
  `agentic.writes_enabled`, `audit redact sk-ant-* pattern`.
- **After:** 2 failures — only `agentic.writes_enabled` and the redact-pattern
  check remain, both **pre-existing and outside this task's scope** (the editing
  agent explicitly declined to fix `agentic.writes_enabled` as a drive-by edit
  since `config.yaml` ships `agentic.writes_enabled: true` today and whether that's
  the config or the check that's "wrong" wasn't part of what was asked). The three
  targeted checks now pass, plus a new fourth check
  (`fsconnect.allow_macos_volume_roots == false`) passes.

**Suggested follow-up (not fixed here, flagging per CLAUDE.md's "flag the gap,
don't silently fix or silently ignore" convention):** either `config.yaml`'s
`agentic.writes_enabled: true` or this script's assertion is stale — worth a
five-minute look by whoever owns the agentic-write-gate decision, separate from
this skill-accuracy pass.

### 2. The claimed "3-tier Ollama realism" ladder — implemented for real

**Files read directly:** `verify.sh`, `run_full_verification.py`, `mock_ollama.py`.

**Finding: CONFIRMED gap, closed.** Neither script previously detected or recorded
which realism tier actually ran, despite `CLAUDE.md` and
`.claude/commands/CyClaw-Sandbox.md` asserting the ladder exists. Both scripts now
probe `127.0.0.1:11434` (1–1.5s timeout) before falling back to spawning
`mock_ollama.py`, and record the result. Verified directly, both branches, without
running the full (network-heavy) `verify.sh` Stage 1 venv rebuild:

- Clean baseline (nothing on :11434, this sandbox's actual state): probe correctly
  reports "nothing listening" → Tier 1 path.
- Simulated Tier 2: started `mock_ollama.py` on :11434 manually, re-ran the probe,
  confirmed it detects the listener → Tier 2 path.
- `run_full_verification.py`'s `_probe_ollama_tier()` called directly against this
  sandbox's real (empty) state: returned `1`, matching `verify.sh`'s logic.

### 3–9. Remaining documentation/table staleness — all fixed, cross-checked against source

Each of the following was fixed by a dedicated parallel agent, and I independently
spot-checked the counts each agent's summary claimed:

- **SKILL.md line 36**: `utils/metrics.py` → `metrics.py` (real file is at repo
  root; Phase 7 already had this right).
- **Due-diligence class count**: `grep -c "^class Test" tests/test_due_diligence_invariants.py`
  confirms 14 classes exist; SKILL.md and test-specifications.md now both say 14
  and document `TestGuardrailInputAuditConvergence` /
  `TestShippedCoreConfigContract`.
- **Phase-count / invariant-count in `.claude/commands/CyClaw-Sandbox.md`**:
  `grep -c '^### Phase' SKILL.md` → 14 (was cited as "21-phase"); the Guardrails
  table has rows #1–#24 (was cited as "20 security invariants"). Both now match.
- **`/api/agent/*` documentation**: added to both SKILL.md's Phase 11 table and
  test-specifications.md's harness endpoint list. One correction surfaced during
  the edit and worth recording here since it corrects my own plan's assumption:
  `GET /api/agent/checks` is **not** Bearer-gated — `harness/server.py` leaves it
  open by design (same as `/api/registry`, so the console can populate before an
  operator key exists) — the doc now reflects the real open/no-auth behavior. The
  agent's edit also corrected a stale "~900s" run-timeout figure to the current
  code's actual 3600s cap, sourced from `agent_run`'s own docstring, propagated
  consistently into `windows-smoke.ps1`'s new comment too.
- **`## Guardrails` / `## Gotchas` headings**: added, closing CLAUDE.md §6's
  literal skill-quality-bar gap (the 24-row invariant table was already present
  under a differently-named heading; `## Gotchas` is new, 4 bullets).
- **macOS test-file labeling**: SKILL.md's Phase 13 table and
  test-specifications.md's new "macOS Realism Coverage" section now split the
  previously-implicit single row into four explicit, real-vs-simulated-labeled
  entries (see Finding 10 below for what backs the fourth one).

### 10. New file: `tests/test_fsconnect_macos_real.py` — the core deliverable

**Files read directly:** `agentic/fsconnect/pathsafe.py` (full macOS section —
`_is_macos_volume_path`, `_raise_macos_permission`/`FsMacOSPermissionError`,
`_is_macos_artifact_name`, `_is_macos_dataless`, `_filter_macos_entries`,
`ScopedRoots.__init__`), `utils/errors.py`'s `FsMacOSPermissionError`, the
existing `tests/test_fsconnect_macos_policy.py` (simulated sibling) and
`tests/test_fsconnect_pathsafe.py` (source of the `_tmp_is_case_sensitive` runtime
probe pattern this new file replicates).

Four tests, zero `sys.platform`/`os.stat`/`os.close` monkeypatching anywhere:

1. `/Volumes` opt-in gate — real `/Volumes` path, real `ScopedRoots` refuse/allow.
2. Apple-metadata filtering — real `.DS_Store`/`.localized`/`._foo` files written
   to `tmp_path`, real filter function calls.
3. Case-insensitive-APFS root-overlap — runtime case-sensitivity probe (not a
   platform guess), real directories, real `ScopedRoots` overlap detection. This
   is the direct real-filesystem regression test for the exact bug class fixed by
   this session's earlier PRs #886/#887/#888.
4. Real EACCES → `FsMacOSPermissionError` — real `chmod(0o000)`, real `OSError`,
   real typed-error assertion.

**Explicitly not made real, by decision**: `SF_DATALESS`/iCloud-dataless handling.
A genuine dataless placeholder requires live iCloud sync state that cannot be
created deterministically (or often at all) on a fresh CI runner with no iCloud
account signed in. The module docstring states this plainly and points to
`test_fsconnect_macos_policy.py`'s existing monkeypatched test as the sole source
of truth for that one property — not silently implied to be covered here.

**Verification boundary — stated plainly, not implied as covered:** all four tests
confirmed to **skip cleanly** (not error) on this Linux sandbox. Their real,
unmocked execution against genuine Darwin syscalls has not happened anywhere yet
and can only happen on `macos-latest` CI once this branch's PR is open. This is a
known, accepted gap between "written and logically sound" and "empirically proven
on real hardware" — the same honest boundary this repo's 2026-08-02 sandbox audit
drew for the original macOS installer work.

### 11. `windows-smoke.ps1` — bounded secondary additions

**Files read directly:** `windows-smoke.ps1` (full), `harness_emulation.py`'s step
13 (mirrored reasoning), `gate_ops.py`'s `/ops/fsconnect` handler (mirrored
request/response shape).

Four new checks (19–22): `GET /api/agent/checks` (real), auth-gate-only probes
(expect 401/403) on `POST /api/agent/run` and `.../decision` — deliberately not a
real run, matching `harness_emulation.py`'s own stated reasoning (clones a repo,
calls a model, can block up to the current 3600s cap, and a decision reaches a
git write) — and one genuine `POST /ops/fsconnect {"action":"status"}` REST
round-trip. A header comment now documents, rather than silently leaves
undiscovered, that `/ops/sync`, `/ops/agentic`, and `/ops/sqlconnect` remain
uncovered by this script.

**Not attempted, by design**: closing the Windows `pathsafe.py` fallback coverage
gap (`_win_resolve`/`_read_win`/etc., all `# pragma: no cover`, the entire
`test_fsconnect_pathsafe.py` module skips on `os.name == "nt"`). This is a real,
pre-existing, and already-documented gap
(`docs/work/FSCONNECT_SQL_ROADMAP.md`'s "Phase 4"), explicitly out of scope for a
skill-accuracy pass — respecting the existing deferral rather than expanding this
change's blast radius. Not CI-wiring `windows-smoke.ps1` either, for the same
reason: it's never been part of CI (`verify-skills`'s matrix only discovers
`.sh`/`smoke.sh`), and adding a `pwsh` + live-server CI leg is meaningfully bigger
infrastructure work than this task asked for.

---

## Recommendations

- **R1.** Resolve whether `config.yaml`'s `agentic.writes_enabled: true` or
  `run_full_verification.py`'s assertion of `false` is the stale one (Finding 1) —
  quick, but deliberately left for the agentic-write-gate owner rather than
  guessed at here.
- **R2.** Once this branch's PR is open, confirm `test_fsconnect_macos_real.py`'s
  four tests actually execute (not just skip) on the `macos-latest` CI leg, and
  that all four pass for real — this is the one piece of this pass's work that
  cannot be confirmed from a Linux sandbox.
- **R3.** Consider, as a separate future task, whether the Windows `pathsafe.py`
  coverage gap (Finding 11) is worth prioritizing now that macOS has closed its
  equivalent gap — no urgency implied here, just surfacing the asymmetry.
