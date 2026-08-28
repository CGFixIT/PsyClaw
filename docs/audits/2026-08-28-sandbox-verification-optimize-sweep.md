# CyClaw Sandbox Verification — 2026-08-28 (optimize sweep)

Verification evidence for the `/CyClaw-Optimize` sweep on branch
`claude/cyclaw-optimize-operator-correctness`. Run in a Linux container with a
Python 3.12 venv and a **Tier-1 mock Ollama** (`mock_ollama.py`) — protocol
realism, not timing realism. Companion to
`docs/audits/2026-08-28-timeout-token-budget-audit.md`, which covers the
separate timeout/token retune on `claude/clone-github-origin-main-6dstt1`.

## What was verified

| Lane | Result |
|---|---|
| Ladder B — `run_full_verification.py`, 11 phases | **222/225** checks passed |
| Ladder D — `smoke.sh`, sections A–G | **all checks passed** (10 passed, 3 skipped) |
| Full `pytest tests/` | 1 failure, environment-caused (see "Known non-defect") |
| `ruff check --select E,F,I,B,C4,UP,S .` (pinned 0.16.1) | clean |
| `bandit` on the changed module (`utils/spend.py`) | 0 issues |
| `invariant-guard` | **47 passed, 0 failed** |
| `config-guard` (non-strict) | 0 failures, 1 warning (C9, the armed-posture warning this sweep documents) |
| `config-guard` `verify.sh` mutation self-tests | PASS (C2 relation, C7 RRF-scale trap incl. `--strict` escalation) |
| `doc-sync` | 0 drift items |

Ladder B detail — every gate green except the one artifact below:

```
RAG pipeline (5 queries): PASS
Triple-Gate Online API (Grok): PASS
Triple-Gate Online API (Claude): PASS
Triple-Gate shared/cross-provider: PASS
API Key Redaction (both providers): FAIL   <- environment artifact, see below
Due-Diligence Invariants: PASS
REST API surface: PASS
Terminal HTML contract: PASS
Harness Console REST API: PASS
Harness HTML contract: PASS
Security Invariants: 24/24 passed
```

## Known non-defects (do not chase these)

Both reproduce on unmodified `main` in this environment and are documented
upstream — neither is caused by the sweep.

1. **Ladder B's 3 failures are one cause: the chromadb stub collision.** All
   three carry the identical error `cannot import name 'Settings' from
   'chromadb.config'`. `.claude/skills/CyClaw-Sandbox/SKILL.md` documents this
   for a partially-real-deps venv: `_install_stubs()` assumes a bare
   interpreter and replaces `sys.modules["chromadb"]`, so a later
   `from chromadb.config import Settings` inside `gate.py`'s import chain can
   hit the stub. **The one check it hides was proven directly instead**: with
   `GROK_API_KEY`/`ANTHROPIC_API_KEY` set to realistic values,
   `gate._sanitize_error` redacted both — `_SECRET_PATTERNS` carries dedicated
   `sk-ant-` and `xai-` shapes on top of the live-env-value replacement loop.

2. **`tests/test_fsconnect_quota.py::test_quota_recompute_fail_closed_on_unreadable_root`
   fails as root.** The test makes a directory unreadable and expects
   `FsWriteRefused`; root bypasses the permission bit, so nothing raises. CI's
   non-root runner passes it. Verified failing on pristine `origin/main` in this
   same container before any change was made.

## Ladder D — smoke suite

`bash .claude/skills/CyClaw-Sandbox/smoke.sh` (starts and stops its own gate,
builds an index if absent). Sections **A–G**: A core API, B `fsconnect`,
C `sqlconnect` read-only guard, D NeMo guardrails, E PostgreSQL backends
(skipped — no `CYCLAW_DB_URL`), F full pytest, G summary.

Result on this branch: `[smoke] All checks passed (10 passed, 3 skipped)` —
the 3 skips are section E's PostgreSQL lanes, which require `CYCLAW_DB_URL`.

> Note for the skill's own docs: `SKILL.md` and `.claude/commands/run.md`
> describe the suite as "sections A-G" and a live run confirms **section G
> exists** — an earlier static reading of `smoke.sh` suggested A–F only, so
> that suspected doc drift is withdrawn rather than filed.

## Scope limits — what this run does NOT prove

Stated explicitly so a green result is not over-read:

- **Timing behaviour is unverified.** Tier-1 `mock_ollama.py` has no latency
  knob (its only delay is `time.sleep(0.01)` per streamed chunk), so the
  720 s / 780 s / 790 s timeout relationships are exercised only by unit pins,
  never by a real clock. Only Tier 2 (a real Ollama daemon) would.
- **No real external provider was called.** Triple-gate checks are
  connection-shape only; no Grok or Claude spend was incurred.
- **Not a macOS run.** The primary target is Apple Silicon; this is Linux
  x86-64. Platform-specific paths (`macos/*.sh`, MLX backend behaviour) are
  untested here.
- **PowerShell is unexecuted.** The sweep's Windows `Set-StrictMode` finding was
  deliberately left unfixed for exactly this reason.

## Reproduce

```bash
# 3.12 venv (bare python3 is 3.11 in this image -> 142 spurious failures)
python3.12 -m venv /root/.venv-cyclaw-312
/root/.venv-cyclaw-312/bin/pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu
/root/.venv-cyclaw-312/bin/pip install -r requirements.txt -r requirements-test.txt \
    -c constraints.txt --ignore-installed PyYAML

# Ladder B — point CYCLAW_REPO at a scratch COPY: it writes corpus/index/report
# files into whatever it targets, and a fresh clone of main would validate the
# wrong tree.
cp -a . /tmp/sb && rm -rf /tmp/sb/.emb_cache /tmp/sb/index/chroma_db
CYCLAW_REPO=/tmp/sb /root/.venv-cyclaw-312/bin/python \
    .claude/skills/CyClaw-Sandbox/run_full_verification.py

# Ladder D
PYTHON=/root/.venv-cyclaw-312/bin/python GROK_API_KEY=dummy \
    bash .claude/skills/CyClaw-Sandbox/smoke.sh

# Static gates
python3 .claude/skills/invariant-guard/check_invariants.py
/root/.venv-cyclaw-312/bin/python .claude/skills/config-guard/check_config.py
/root/.venv-cyclaw-312/bin/python .claude/skills/doc-sync/doc_sync.py
/root/.venv-cyclaw-312/bin/python -m ruff check --select E,F,I,B,C4,UP,S .
```

The sandbox skill's own step 10 (dated report + PR) is satisfied by this
document riding with the sweep's PR rather than opening a second one for a
single file.
