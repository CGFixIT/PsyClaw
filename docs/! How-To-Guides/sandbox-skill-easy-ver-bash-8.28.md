# CyClaw-Sandbox Skill Consolidation

Date: 2026-08-28
Baseline: `origin/main` at `a4ca399c2d0727ba93922ce3113b6438d5dcf7d1`
Version: `1.9.0`
Branch under test: `claude/issue-1135-cyclaw-1wdqhq` (this PR)

## Verdict

**Consolidation, not a feature change.** `.claude/skills/CyClaw-Sandbox/`
shipped two competing skill documents since PR #1090 (`SKILL.md`, the one
the skill runtime actually loads, and an inert `NEW_SKILL.md` with broken
YAML frontmatter and zero inbound references). `SKILL.md` made roughly a
dozen confirmed-wrong claims about the current codebase; several of the
support scripts it describes failed on a clean tree. This PR: (1) heals
the scripts against current main, (2) merges the two documents into one
accurate `SKILL.md` and deletes the inert one, (3) syncs the small ripple
of command docs that reference this skill. No gate/graph/config.yaml
behavior changed.

## What was wrong (confirmed against this tree, not assumed)

The 2026-08-26 audit (`2026-08-26-sandbox-verification-latest-main.md`,
this same directory) had already caught three of these on a
report-only branch and proposed the fix; this PR is that fix, plus the
rest of the drift its own scope didn't cover:

| # | Claim in the old `SKILL.md` / `run_full_verification.py` | Current fact | Fixed by |
|---|---|---|---|
| 1 | `run_full_verification.py` Phase 3 imports `retrieval.stemmer.PorterStemmer` | Symbol removed; `tokenize_and_stem` is the current call | commit 1 |
| 2 | Phase 6 reads `logging.audit.redact_secrets_like` | Real path is `policy.privacy.redact_secrets_like` (audit's own F-02) | commit 1 |
| 3 | Phase 9 greps only `static/terminal.html` for console logic | Console JS moved to `static/terminal.js` (CSP `script-src 'self'`); this is F-03 | commit 1 |
| 4 | Phase 2 greps `gate.py` for 10 telemetry names (audit's F-06: reported `0/10` against 18 independently-confirmed-active vars) | Canonical maps are `utils.telemetry_kill.{TELEMETRY_KILL,UPDATE_CHECK_OPT_OUT,SCRUBBED_ENV_KEYS}` (21/4/7 entries) | commit 1 |
| 5 | Phase 8 greps `gate.py` alone for `/ops/*` routes | Those routes live in `gate_ops.py` since the auth/memory split | commit 1 |
| 6 | SKILL.md: "Auth ... Stage 3 has not landed" | `require_session_or_token` attaches to `POST /query` whenever an `AuthManager` exists; wired | commit 2 |
| 7 | SKILL.md: "10 env vars killed at import time" | 21+4+7 canonical entries | commit 2 |
| 8 | SKILL.md: "four `/api/agent/*` routes" | Seven (`checks/run/runs/{id}/decision/push/publish/discard`) | commit 1 (script), commit 2 (doc) |
| 9 | SKILL.md: "13 harness slash commands" (hardcoded tuple) | `COMMANDS` array carries ~19 distinct commands plus a hidden `registry` alias of `/connectors` | commit 1 (script derives from the array now), commit 2 (doc) |
| 10 | SKILL.md / `run.md` / `.claude/README.md`: "29 (smoke/out-of-band) checks" | `smoke.sh`'s internal numbering was never 29 anywhere it's counted; the script prints its own dynamic total | commit 2, commit 3 |
| 11 | `test_terminal_consoles.py`: four `unknown action -> 400` assertions | `schemas/api.py`'s `action` fields are closed `Literal`s; an unrecognized value 422s at the schema boundary, never reaches the 400-returning handler code | commit 1 |
| 12 | (discovered live, not previously documented) `test_terminal_consoles.py`'s security-header check compared mixed-case names against `urllib`'s lowercased header dict | Headers are present on every response (confirmed via `curl`); the check itself was wrong | commit 1 |
| 13 | (discovered live) `test_terminal_consoles.py`'s DROP-rejection check assumed `sqlconnect` was enabled | Shipped `sqlconnect.enabled: false` short-circuits before the read-only guard runs -- an equally safe outcome the check didn't allow for | commit 1 |

Two documents, one accurate now: the previously-inert `NEW_SKILL.md` was
right about several of the above (12 graph nodes, Auth Stages 3+4 wired)
and covered real surfaces the loaded document never mentioned (spend
ledger, Numbat, sequence detection, Telegram/OpenTweet/unslop, MCP
manifest pin, `/api/keys`) -- all folded into the new consolidated
document; see commit 2's message for the full absorption/drop map.

## Test Environment

- Interpreter: `/root/.venv-cyclaw-312/bin/python`, Python 3.12.3.
- Ollama realism: Tier 1, this skill's own `mock_ollama.py` over loopback
  HTTP (`qwen3.8:27b-mlx`).
- Real Ollama/Grok/Claude: not used. No API cost incurred.
- `CYCLAW_API_KEY` used for all live gated probes: a fixed CI-style value
  (`verify-key-ci`), not committed, not printed beyond this note.
- `run_full_verification.py` was pointed (`CYCLAW_REPO=`) at a disposable
  `git clone --depth 1 file://<repo>` scratch checkout, not the working
  tree, so its Phase-3-onward writes (mock corpus, BM25 index, report
  JSON) never touched the repository's real `data/corpus/`.

## Evidence Summary

| Check | Result | Evidence |
|---|---|---|
| `gate_runtime_check.py` | PASS | 37 routes (incl. the new `/index/*`, `/ops/*`, `/auth/setup-status`, `/auth/login`, `/memory/status`), 21 telemetry-kill vars |
| `harness_runtime_check.py` | PASS | 43 routes (incl. `/api/keys`, the 3 new agent routes, `/auth/setup-status`, `/auth/login`), ToolBroker import check, auto-docs disabled |
| In-process swarm (`run_full_verification.py`) | 222/225 | Every phase full-PASS except Key Redaction 4/5 -- see the one open item below |
| `test_terminal_consoles.py` (live gate) | 41/41 | All four 422 fixes confirmed; both bonus fixes (header case, DROP-disabled) confirmed |
| `harness_emulation.py` (live harness) | full PASS, 18 steps | Including all 5 new agent-route auth-gate checks and the 2 new steps (`/api/keys`, `/api/auth/setup-status`) |
| `doc_sync.py` | 0 drift | Frontmatter parses; D4's pattern-count scanner finds no other bare count claim in the new prose |
| `invariant-guard` | 47/47 | Unaffected -- no core file touched by this PR |
| Repo-wide `ruff check --select E,F,I,B,C4,UP,S .` | clean (see note) | `.claude/` is excluded from ruff's own `pyproject.toml` config, so every file this PR touches under `.claude/skills/CyClaw-Sandbox/` is out of ruff's scope either way; verified with `.claude` excluded explicitly |

## One open item (not fixed, documented instead)

`run_full_verification.py`'s Phase 6 check `anthropic_key_sanitized` fails
in this venv with `cannot import name 'Settings' from 'chromadb.config'`.
Root-caused: the script's `_install_stubs()` assumes a bare interpreter
with nothing installed and unconditionally overwrites
`sys.modules["chromadb"]`/`chromadb.config` with empty stub modules; this
venv has real chromadb installed (needed for other work in this session),
and depending on import order a later `from chromadb.config import
Settings` inside `gate.py`'s import chain can hit the stub instead of the
real module. Reproduced independent of every change in this PR: the exact
same failure occurs from only `_install_stubs()`'s untouched chromadb-stub
loop plus a bare `from gate import _sanitize_error`, in a fresh
interpreter, before any of this PR's edits are even in the picture. This
is an environment artifact of a venv that partially, rather than fully,
matches the script's "sandbox mode" assumption -- not a CyClaw regression,
and not in this PR's scope (the stub layer is a different subsystem from
the stale-claim drift this consolidation targets). Documented here per
this repo's own convention for this exact class of finding (see the
2026-08-26 audit's own environment-specific F-04/F-05).

## Commands Run

```bash
GROK_API_KEY=dummy /root/.venv-cyclaw-312/bin/python .claude/skills/CyClaw-Sandbox/gate_runtime_check.py
GROK_API_KEY=dummy /root/.venv-cyclaw-312/bin/python .claude/skills/CyClaw-Sandbox/harness_runtime_check.py
CYCLAW_REPO=<scratch-clone> GROK_API_KEY=dummy /root/.venv-cyclaw-312/bin/python \
  .claude/skills/CyClaw-Sandbox/run_full_verification.py
CYCLAW_API_KEY=verify-key-ci /root/.venv-cyclaw-312/bin/python \
  .claude/skills/CyClaw-Sandbox/test_terminal_consoles.py
CYCLAW_API_KEY=verify-key-ci /root/.venv-cyclaw-312/bin/python \
  .claude/skills/CyClaw-Sandbox/harness_emulation.py http://127.0.0.1:8790
python3 .claude/skills/doc-sync/doc_sync.py
python3 .claude/skills/invariant-guard/check_invariants.py
ruff check --select E,F,I,B,C4,UP,S . --extend-exclude "docs/CyClaw Audit Bundle Export Script.py"
```

The live gate/harness/mock_ollama processes were loopback-only and were
stopped after the probes; no real provider credentials were used.

## Unrelated finding noted, not fixed

`docs/CyClaw Audit Bundle Export Script.py` (added by a commit already on
`main` before this branch was cut, unrelated to this PR) fails to parse:
`ruff` reports `invalid-syntax: Expected a statement` at line 248 (a
malformed ternary spanning lines 246-248). This will make a bare
repo-wide `ruff check --select E,F,I,B,C4,UP,S .` (without excluding that
one file) report 2 errors regardless of this PR's content -- confirmed
pre-existing on `main` at `a4ca399`, out of this PR's scope to fix.

## Invariant Impact

This PR changes no gate/graph/config.yaml behavior and does not alter any
of the six CyClaw security invariants or I6 module isolation --
`invariant-guard` is 47/47, unaffected. It heals a verification-only
toolchain and its own documentation.
