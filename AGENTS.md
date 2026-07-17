# AGENTS.md

Guidance for Codex and other AI coding agents working in CyClaw. Read this before editing, then consult the canonical docs linked here instead of duplicating them.

Canonical references:

- `README.md` for product overview and architecture.
- `CLAUDE.md` for the existing detailed agent operating contract.
- `.github/copilot-instructions.md` for Copilot and PR behavior.
- `docs/SETUP.md` for local setup details.
- `docs/THREAT_MODEL.md` and `.github/SECURITY.md` for security assumptions.
- `.codex/skills/fable-protocol/SKILL.md` for Codex session-start reasoning,
  verification discipline, findings-before-writes, security posture, and
  shipping-first prioritization.
- `.codex/README.md` for Codex routines, prompts, and checklists.
- `docs/memories/CONSOLIDATED.md` for the current consolidated memory/business
  and prioritization stance.

## Project Overview

CyClaw is an offline-first Python RAG server and retrieval-only MCP server. The core app is `gate.py` (FastAPI) calling `graph.py` (LangGraph security topology), with hybrid retrieval in `retrieval/` backed by ChromaDB and BM25. It is designed to bind to `127.0.0.1:8787`, use a local Ollama model, and allow Grok/Claude only through explicit hybrid-mode gates.

The main security invariants are RAG-first retrieval, graph topology as policy, triple-gated external fallback, audit convergence, human-gated soul changes, and module isolation. Treat these as design constraints, not implementation suggestions.

## Tech Stack Detected

- Python 3.12.
- FastAPI, Uvicorn, Pydantic.
- LangGraph, ChromaDB embedded `PersistentClient`, BM25, sentence-transformers.
- Local Ollama endpoint at `127.0.0.1:11434/v1`.
- Optional Grok/xAI fallback in hybrid mode only.
- Optional `sync/`, `agentic/`, and `guardrails/` layers.
- Packaging via `pyproject.toml`, legacy/CI `requirements.txt`, reproducibility `constraints.txt`, and uv where available.
- pytest, pytest-cov, Ruff, mypy config, Bandit config.
- Docker and Docker Compose.
- CI/security workflows for tests, lint, conda, CodeQL, Gitleaks, OSV, pip-audit, DevSkim, Defender, and Fortify.
- No `Makefile`, `justfile`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, or Gradle build file was found during setup inspection.

## Repository Layout

- `gate.py` - FastAPI app, auth, rate limit, telemetry kill block, static UI mount.
- `graph.py` - LangGraph policy topology and routing.
- `retrieval/` - indexing, embeddings, hybrid search, BM25/Chroma helpers.
- `llm/` - local Ollama, Grok, and Claude clients.
- `utils/` - sanitizer, logging, health, personality/soul, rate limiting, errors.
- `schemas/` - API models.
- `sync/` - optional out-of-band Dropbox/rclone sync.
- `agentic/` - optional out-of-band GitHub context, governed skills registry,
  filesystem connector, and read-only SQL connector.
- `guardrails/` - optional NeMo/offline guardrails layer.
- `static/` - browser terminal UI.
- `tests/` - pytest suite and smoke helpers.
- `.github/workflows/` - CI, lint, conda, CodeQL, and security workflows.
- `.claude/` - existing Claude project skills, commands, hooks, rules, and patterns.
- `.codex/skills/` - Codex repository skills and project workflows.
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
large docs or making edits.

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

Preferred local setup from `.github/copilot-instructions.md`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade "pip>=26.1.2"
pip install torch==2.12.1+cpu --index-url https://download.pytorch.org/whl/cpu
uv pip install -r pyproject.toml --constraint constraints.txt
```

Legacy/CI-compatible fallback:

```bash
python -m pip install --upgrade "pip>=26.1.2"
pip install torch==2.12.1+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt -c constraints.txt
```

Runtime prep required before app/tests that touch the gateway:

```bash
mkdir -p data/personality index logs
[ -f data/personality/soul.md ] || printf '# Soul\n' > data/personality/soul.md
export GROK_API_KEY=dummy
```

Windows PowerShell setup is documented in `docs/SETUP.md`. Keep the same torch-first rule there too.

## Build And Run Commands

Build the retrieval index when `index/` is missing or `data/corpus/` changes:

```bash
python -m retrieval.indexer
```

Run the FastAPI gateway:

```bash
uvicorn gate:app --host 127.0.0.1 --port 8787
```

Or, after installing the package entry points:

```bash
cyclaw-server
```

Run the retrieval-only MCP server:

```bash
python mcp_hybrid_server.py
# or
cyclaw-mcp
```

Container build/run exists through `Dockerfile` and `docker-compose.yml`; no explicit documented build target was found. Conventional commands such as `docker build -t cyclaw .` and `docker compose up --build` need verification in the target environment.

## Test Commands

Fast retrieval smoke used by CI:

```bash
python -m tests.ci_rag_smoke
```

General pytest run:

```bash
pytest tests/ -q --tb=short
```

Main CI parity uses a long explicit pytest file list in `.github/workflows/ci.yml`; use that workflow as the exact source of truth for release-risk changes.

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

PR lint workflow command:

```bash
ruff check --select E,F,I,B,C4,UP,S .
```

Configured but not found as a CI command during setup inspection:

```bash
mypy .
bandit -r gate.py graph.py retrieval utils llm sync agentic guardrails
```

No markdown formatter or markdown lint command was found in repo config.

## Safe Development Workflow

1. Read `CLAUDE.md`, this file, and the relevant subsystem docs before editing.
2. Identify the smallest subsystem that owns the change.
3. Keep diffs narrow; do not mix setup, dependency, CI, and runtime behavior in one change unless requested.
4. Prepare the hermetic runtime directories before gateway tests.
5. Run Ruff and the most targeted pytest/smoke command that exercises the change.
6. For security/routing/retrieval changes, expand to the CI-equivalent command from `.github/workflows/ci.yml`.
7. Report exactly what ran, what failed, and what remains unverified.

## Coding Conventions

- Preserve Python 3.12 compatibility.
- Keep `gate.py`, `graph.py`, and `mcp_hybrid_server.py` isolated from optional `sync/` and `agentic/` imports.
- Maintain typed exceptions from `utils.errors`; avoid broad bare `Exception` handling in production code.
- Keep telemetry-kill environment handling before SDK/model imports.
- Do not change routing policy by prompt text; policy belongs in graph edges and explicit guards.
- Keep loopback-only binding unless the maintainer explicitly requests a deployment change.

## Testing Expectations

- Add or update tests with behavior changes.
- Prefer targeted tests first, then broader CI parity for cross-cutting changes.
- Do not rely on Ollama, rclone, real GitHub tokens, or real databases in ordinary unit tests unless the test is explicitly scoped to that integration.
- Use dummy non-secret env values in tests, such as `GROK_API_KEY=dummy`.
- Preserve committed `data/personality/soul.md` unless the task is explicitly about soul content or tests isolate and restore it.

## Dependency Management Rules

- Prefer `pyproject.toml` plus uv for new local installs.
- Keep `requirements.txt` only as the legacy/CI compatibility path.
- Keep `constraints.txt` aligned with direct pins and critical transitives.
- Install CPU-only torch first: `torch==2.12.1+cpu` from the PyTorch CPU index.
- Do not casually remove the documented ChromaDB CVE exception; it is accepted only for embedded, local/offline `PersistentClient` use and documented in security workflows.
- Do not add dependencies unless the task needs them and the relevant manifests, constraints, Docker, and CI install paths are kept consistent.

## Security And Secrets Rules

- Never commit real secrets, tokens, local `.env` files, `rclone.conf`, logs, indexes, caches, or generated coverage artifacts.
- Treat `data/corpus/` as potentially private user knowledge; do not expose content in summaries unless necessary and requested.
- Keep external model/network behavior opt-in and human-gated.
- Preserve audit convergence and PII redaction behavior.
- Use `.github/SECURITY.md` for vulnerability handling and `docs/THREAT_MODEL.md` for deployment assumptions.

## GitHub, Codex, And PR Permissions

- GitHub repository metadata exposed through the connector reported `admin`, `maintain`, `pull`, `push`, and `triage` access during first setup.
- During first setup, the connector's Git contents APIs returned `403 Resource not accessible by integration` for direct file writes. Repository files cannot grant GitHub App installation permissions; update the GitHub App/connector installation outside the repo if Codex needs contents-write through the connector.
- Local `gh` availability is environment-specific and may be a non-GitHub-CLI shim. If using repo-local agentic GitHub flows, verify the actual binary first with `Get-Command gh` / `gh --version`, then authenticate and confirm with `gh auth status`. Prefer connector/API metadata when `gh` is missing, unauthenticated, or not the official GitHub CLI.
- PR conversation comments require pull request/issues write permission. The connector exposes PR comment/review tools; if they return 403, update the app installation permissions outside the repo.
- `.github/workflows/claude.yml` already grants `contents: write`, `pull-requests: write`, `issues: write`, and `id-token: write` for the existing Claude PR-comment workflow.

## Git Workflow Expectations

- Prefer feature branches and PRs for changes that touch multiple files unless the maintainer explicitly asks for direct `main`.
- Never force-push without explicit human approval.
- Do not edit open PR branches unless the user clearly asked or the repo's documented CI-failure policy applies.
- Keep commit messages direct and scoped, for example `docs: add Codex onboarding guide`.

## PR And Review Expectations

- Summarize behavior impact, security impact, commands run, and unverified areas.
- For PR reviews, lead with findings and file/line references; keep summaries secondary.
- For dependency or CI changes, compare `pyproject.toml`, `requirements.txt`, `constraints.txt`, and `Dockerfile` for drift.
- Leave PRs open unless the maintainer explicitly asks to merge or close them.

## Known Gotchas

- Always install CPU torch before requirements to avoid CUDA wheel resolution.
- `requirements.txt` is deprecated for local dev but still used by CI compatibility paths.
- `uv pip install -r pyproject.toml --constraint constraints.txt` requires uv to be installed.
- `data/personality/soul.md`, `index/`, and `logs/` are expected by many runtime paths.
- `/soul/*` endpoints fail closed unless `CYCLAW_API_KEY` is set.
- The server must stay on `127.0.0.1:8787` unless explicitly changed.
- `sync/` runtime needs external `rclone`; tests should mock it.
- Agentic GitHub context needs `gh` in local environments, but core CyClaw does not.
- `.github/workflows/environment.yml` is intentionally referenced by the conda workflow from `.github/workflows/python-package-conda.yml`.

## Do Not

- Do not weaken the six security invariants.
- Do not bind services to `0.0.0.0` casually.
- Do not introduce autonomous soul/personality mutation.
- Do not make optional `sync/`, `agentic/`, or `guardrails/` required for the core request path.
- Do not rewrite existing Claude/Copilot instructions when adding Codex guidance.
- Do not invent commands; mark unknowns as needs verification.
- Do not commit generated scratch work, logs, caches, indexes, secrets, or local machine paths.
