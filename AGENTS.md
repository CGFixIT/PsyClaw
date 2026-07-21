# AGENTS.md

Guidance for Codex and other AI coding agents working in CyClaw. Read this before editing, then consult the canonical docs linked here instead of duplicating them. **`CLAUDE.md` is the detailed operating contract — this file is a thin, Codex-oriented layer on top of it; it restates only what a Codex session needs at a glance or what CLAUDE.md doesn't cover.**

Canonical references:

- `README.md` for product overview and architecture.
- `CLAUDE.md` for the detailed agent operating contract — invariants, module
  map, load-bearing config numbers, traps, conventions, escalation rules.
- `.github/copilot-instructions.md` for Copilot and PR behavior.
- `docs/SETUP.md` for local setup details.
- `docs/THREAT_MODEL.md` and `.github/SECURITY.md` for security assumptions.
- `.codex/skills/fable-protocol/SKILL.md` for Codex session-start reasoning,
  verification discipline, findings-before-writes, security posture, and
  shipping-first prioritization.
- `.codex/README.md` for Codex routines, prompts, and checklists.
- `docs/memories/CONSOLIDATED.md` for the current consolidated memory (this
  file auto-regenerates from session snapshots — treat it as a rolling
  summary, not a stable citation target).

## Project Overview

CyClaw is an offline-first Python RAG server and retrieval-only MCP server — see `CLAUDE.md` §1-2 for the full map. Core app: `gate.py` (FastAPI) → `graph.py` (LangGraph security topology) → hybrid `retrieval/` (ChromaDB + BM25). Binds to `127.0.0.1:8787` only; local Ollama model; Grok and/or Claude reachable only through explicit hybrid-mode gates.

The six security invariants (`CLAUDE.md` §3) are design constraints, not implementation suggestions: RAG-first, topology=policy, triple-gated external fallback, audit convergence, soul governance, module isolation.

## Tech Stack Detected

- Python 3.12.
- FastAPI, Uvicorn, Pydantic.
- LangGraph, ChromaDB embedded `PersistentClient`, BM25, sentence-transformers.
- Local Ollama endpoint at `127.0.0.1:11434/v1`.
- Optional Grok (xAI) and/or Claude fallback, hybrid mode only, triple-gated (`CLAUDE.md` §3 I3).
- Optional `sync/`, `agentic/`, and `guardrails/` layers.
- Packaging via `pyproject.toml`, legacy/CI `requirements.txt`, reproducibility `constraints.txt`, and uv where available.
- pytest, pytest-cov, Ruff, mypy config, Bandit config.
- Docker and Docker Compose (`Dockerfile`, `docker-compose.yml`).
- CI/security workflows for tests, lint, conda, CodeQL, Gitleaks, OSV, pip-audit, DevSkim, Defender, and Fortify.
- No `Makefile`, `justfile`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, or Gradle build file was found during setup inspection.

## Repository Layout

Code modules mirror `CLAUDE.md` §2's "Key modules" table (more granular — treat that as the source of truth). Directories `CLAUDE.md`'s module table omits:

- `static/` - browser terminal UI (`terminal.html`, served at `/`).
- `tests/` - pytest suite and smoke helpers.
- `.github/workflows/` - CI, lint, conda, CodeQL, and security workflows.
- `.claude/` - Claude project skills, commands, hooks, rules, and patterns.
- `.codex/` - Codex instructions, skills, checklists, prompts, and routines.
- `docs/` - setup, threat model, audits, agentic docs, sync docs, planning.

## Session Bootstrap For Codex

Codex reads this `AGENTS.md` automatically at startup. For every substantive
CyClaw task, activate/read `.codex/skills/fable-protocol/` before planning or
editing. This is the always-on discipline layer for premise testing, uncertainty
marking, findings-before-writes, security review, and shipping-first
prioritization.

Do not treat the Fable skill as permission to expand scope, override user
instructions, or duplicate private/source-protocol details in public files. For
trivial questions, apply its defaults silently and keep the response small.

## Codex Skills And Routines Map

Use this map to choose the narrowest reusable Codex workflow before opening
large docs or making edits. **This is the canonical copy** — `.codex/README.md`
points back here rather than duplicating the list.

Skills:

- `.codex/skills/fable-protocol/` - use at the start of every substantive repo
  task as the session-start discipline layer for premise testing, uncertainty,
  findings-before-writes, security review, and shipping-first prioritization.
- `.codex/skills/cyclaw-project-guidance/` - use before substantial CyClaw work
  to load repository invariants, architecture, test expectations, and canonical
  reference docs.
- `.codex/skills/cyclaw-run-cyclaw/` - use when asked to prepare, start, run,
  smoke-test, or interact with the local FastAPI RAG server.
- `.codex/skills/cyclaw-sandbox-test/` - use for a fresh-main local sandbox
  audit with mock Ollama and terminal/API smoke coverage before PRs.
- `.codex/skills/cyclaw-command-status/` - use for read-only environment,
  config, index, soul, telemetry, and live health status checks.
- `.codex/skills/cyclaw-command-run/` - use for focused endpoint smoke checks
  and local runtime verification.
- `.codex/skills/cyclaw-command-audit/` - use to summarize
  `logs/audit.jsonl` with `metrics.py` and flag audit anomalies.
- `.codex/skills/cyclaw-command-check-soul/` - use to validate
  `data/personality/soul.md` presence, hash, readability, and drift without
  mutating it.
- `.codex/skills/refactor/` - use for the combined architecture-cleanup and
  speed-optimization loop with tracker, measurement, self-review, and commits.
- `.codex/skills/cyclaw-optimize/` - use when asked to scan `main` for
  optimization opportunities and open focused draft PRs.

Routines:

- `.codex/routines/first-pass-repo-review.md` - orient in a new subsystem or
  verify setup before edits.
- `.codex/routines/bugfix.md` - reproduce, diagnose, fix, and verify a reported
  defect or failing check.
- `.codex/routines/feature.md` - implement new behavior while preserving
  CyClaw invariants and optional-layer isolation.
- `.codex/routines/refactor.md` - improve structure while preserving behavior
  and keeping review diffs narrow.
- `.codex/routines/test-and-verify.md` - choose targeted, CI-parity, or static
  checks and report skipped verification honestly.
- `.codex/routines/pr-review.md` - review a PR, local diff, or patch with
  findings first and summaries second.
- `.codex/routines/security-review.md` - assess auth, secrets, telemetry,
  network exposure, LangGraph routing, retrieval boundaries, dependencies, or
  optional-layer changes.

## Current Product And PMF Posture

- As of `2026-07-03`, default to a feature freeze unless the user explicitly
  asks for net-new product behavior.
- CyClaw's primary role is a polished portfolio and judgment artifact; the
  commercial track is option B, not the default planning assumption.
- Use this test before proposing product work: `what is the mechanism by which
  more code moves PMF probability right without customer conversations?`
- If that mechanism is weak or absent, prioritize documentation accuracy, demo
  crispness, packaging, tests, evidence quality, architecture clarity, and
  operational polish instead.
- Current calibrated business view: roughly `25-35%` odds of a repeatable
  `$15k+/mo` motion in `18-24` months, earliest realistic revenue
  `Q4 2026-Q1 2027`, with the W-2 path still dominating expected value.
- Atlanta small-law remains a plausible discovery niche, not a proven product
  market; the honest year-1 outcome from 10 conversations is `0-2` paid
  concierge engagements (`$0-10K`), and naming collision risk means any serious
  commercial artifact should assume rename/trademark work first.

## Setup Commands

Preferred local setup (uv-based, when uv is available):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade "pip>=26.1.2"
pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu
uv pip install -e . -c constraints.txt
```

Legacy/CI-compatible fallback (`CLAUDE.md` §8 documents this exact command; CI runs the same `pip install -r requirements.txt -c constraints.txt` core but without `--ignore-installed PyYAML`, and installs torch from a cached local wheel rather than the index):

```bash
python -m pip install --upgrade "pip>=26.1.2"
pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML
```

Runtime prep required before app/tests that touch the gateway:

```bash
mkdir -p data/personality index logs
[ -f data/personality/soul.md ] || printf '# Soul\n' > data/personality/soul.md
export GROK_API_KEY=dummy
```

Windows PowerShell setup is documented in `docs/SETUP.md`. Keep the same torch-first rule there too.

## Build And Run Commands

Same commands as `CLAUDE.md` §8 (index build, `gate.py`/`cyclaw-server`, MCP server), plus the raw uvicorn invocation `CLAUDE.md` doesn't spell out:

```bash
uvicorn gate:app --host 127.0.0.1 --port 8787
```

`Dockerfile` and `docker-compose.yml` exist for containerized runs; their exact invocation is unverified against a documented CI target — confirm in the target environment before relying on it rather than assuming `docker build -t cyclaw .` / `docker compose up --build` work as-is.

## Test Commands

Fast retrieval smoke used by CI:

```bash
python -m tests.ci_rag_smoke
```

General pytest run (matches `CLAUDE.md` §8):

```bash
GROK_API_KEY=dummy pytest tests/ -q --tb=short
```

`tests/` auto-discovers every `test_*.py` file — CI does **not** hardcode a file
list for the main run (`ci.yml`'s own comment confirms this); new test files
need no `ci.yml` edit. `ci_rag_smoke.py` is deliberately excluded from that
auto-discovery (it doesn't match `test_*.py`) and runs as its own CI step —
don't rename it to `test_ci_rag_smoke.py` (see `CLAUDE.md` §4).

Agentic targeted tests:

```bash
GROK_API_KEY=dummy pytest tests/test_agentic_*.py -q
python -m agentic.cli test
```

Postgres/pgvector backend tests require a live Postgres/pgvector service and are wired in `.github/workflows/ci.yml`:

```bash
pytest tests/test_personality_postgres.py tests/test_ratelimit_postgres.py tests/test_pgvector_store.py -q --tb=short
```

Windows live HTTP smoke, with the server running:

```powershell
powershell -File tests/apipsTest.ps1
```

## Lint, Format, And Typecheck Commands

CI-enforced:

```bash
ruff check --select E,F,I,B,C4,UP,S .
```

Best-effort, not CI-enforced (see `CLAUDE.md` §4 for why a bare `mypy .` fails immediately):

```bash
mypy --strict --python-version 3.12 --explicit-package-bases <touched files>
bandit -r gate.py graph.py retrieval utils llm sync agentic guardrails
```

No markdown formatter or markdown lint command is configured in this repo.

## Safe Development Workflow, Coding Conventions, Testing Expectations, Dependency Rules, Security Rules, Git Workflow, PR Expectations

These are fully covered by `CLAUDE.md` §3-7 (invariants, conventions, quality
bar, escalation tiers) — read those sections rather than a duplicate summary
here. The few points below have no `CLAUDE.md` equivalent and are worth
keeping close to this file:

- Prepare the hermetic runtime directories (Setup Commands above) before any
  gateway test, not just before running the server.
- Preserve committed `data/personality/soul.md` unless the task is explicitly
  about soul content, or the test isolates and restores it.
- Treat `data/corpus/` as potentially private user knowledge; do not expose
  its content in summaries unless necessary and requested.
- For security/routing/retrieval changes, expand verification to the
  CI-equivalent command from `.github/workflows/ci.yml` rather than a
  narrower local check.
- For dependency or CI changes, compare `pyproject.toml`, `requirements.txt`,
  `constraints.txt`, `Dockerfile`, and `environment.yml` for drift.
  `.claude/skills/dep-guard/check_deps.py` (stdlib-only, runs without
  installing anything) does this deterministically for pin agreement; there is
  no `.codex/`-side equivalent yet.
- Report exactly what ran, what failed, and what remains unverified — every
  response, not just PR bodies.
- GitHub App / connector permissions observed during Codex setup: `admin`,
  `maintain`, `pull`, `push`, `triage`. The connector's Git contents API can
  return `403 Resource not accessible by integration` for direct file writes —
  that's an installation-permission gap, not something a repo file can fix; it
  needs the GitHub App/connector installation updated outside the repo. PR
  conversation comments specifically need pull-request/issues write
  permission — if the connector's PR comment/review tools return 403, that is
  the same class of gap.
- Local `gh` availability is environment-specific and may be a non-GitHub-CLI
  shim — verify the actual binary first (`Get-Command gh` on Windows /
  `gh --version` elsewhere), then confirm auth with `gh auth status`; prefer
  connector/API metadata when `gh` is missing, unauthenticated, or not the
  official CLI.
- `.github/workflows/claude.yml` already grants `contents: write`,
  `pull-requests: write`, `issues: write`, and `id-token: write` for the
  existing Claude PR-comment workflow.
- Do not edit open PR branches unless the user clearly asked or the repo's
  documented CI-failure policy applies.
- For PR reviews, lead with findings and file/line references; keep
  summaries secondary.
- Do not rewrite existing Claude/Copilot instructions when adding Codex
  guidance to this file — extend, don't overwrite another agent's contract.
- Do not invent commands; mark unknowns as needs verification (see the Build
  And Run Commands note on Docker above for how to phrase that).

## Known Gotchas

Covered by `CLAUDE.md` §4 (torch-first install order, `data/personality/soul.md`/`index/`/`logs/` expected at boot, `/soul/*` fail-closed without `CYCLAW_API_KEY`, loopback-only binding, `sync/` needs `rclone` and tests should mock it). Two with no `CLAUDE.md` equivalent:

- Agentic GitHub context needs `gh` in local environments, but core CyClaw does not.
- `.github/workflows/environment.yml` is intentionally referenced by the conda workflow from `.github/workflows/python-package-conda.yml` — don't delete it as apparently-unused.

## Do Not

Covered by `CLAUDE.md` §3 (six invariants) and §7 (escalation "Always ask
first" / "Never" lists) — most concretely: don't weaken an invariant, don't
bind services to `0.0.0.0` casually, don't make `sync/`/`agentic/`/`guardrails/`
required for the core request path, don't introduce autonomous soul mutation,
don't commit generated scratch work/logs/caches/indexes/secrets/local paths.
