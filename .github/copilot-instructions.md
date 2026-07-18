# Copilot instructions for CyClaw

Trust this file first and only search the repo when these instructions are incomplete or proven wrong.

## What this repo is
- CyClaw is a **Python 3.12** FastAPI RAG server plus a retrieval-only MCP server.
- Core runtime is **offline-first**: FastAPI in `gate.py`, LangGraph policy topology in `graph.py`, hybrid retrieval in `retrieval/`, local LLM via LM Studio, optional Grok fallback only in hybrid mode.
- Repo is mostly Python with small static HTML and shell/PowerShell helpers. Default branch is `main`.
- Main security invariants: retrieval always runs first; graph edges enforce policy; Grok requires hybrid mode + enabled config + per-query confirmation; all paths audit-log; soul changes are explicit human-gated operations.

## High-value files / layout
- `gate.py` — FastAPI app, telemetry kill block, auth/rate-limit, `/query`, `/health`, `/soul*`, static UI mount.
- `graph.py` — 7-node LangGraph state machine; change routing/security behavior here, not in prompts.
- `retrieval/indexer.py` — builds ChromaDB + BM25 indexes from `data/corpus`.
- `retrieval/hybrid_search.py`, `retrieval/embeddings.py`, `retrieval/stemmer.py` — retrieval implementation.
- `llm/client.py` — LM Studio + Grok clients.
- `mcp_hybrid_server.py` — retrieval-only MCP stdio server; **no LLM path**.
- `utils/` — sanitizer, logger, personality, health, errors, rate limiting.
- `sync/` — optional Dropbox/rclone corpus sync; intentionally out-of-band and not imported by `gate.py`/`graph.py`.
- `config.yaml` — single source of truth for ports, model endpoints, retrieval thresholds, prompt-filter patterns, privacy rules, sync settings.
- `tests/` — pytest suite plus `ci_rag_smoke.py` and `apipsTest.ps1`.
- `.github/workflows/` — CI/lint/security gates. Match them locally before proposing changes.

## Environment and bootstrap
Always use **Python 3.12**. CI uses 3.12 on Ubuntu and Windows.

## Core Behaviors
- When a PR is opened, wait 5 minutes then assess the changes made in the context of looking for potential negative or unexpected consequences. You are also to look for notable project changes from a given open PR compared to main branch.
- After you have a clear understanding of the PR goal, its changes, and a holistic view of the impact, comment on the Pull Request beginning with a 1 word title description (e.g. "Warning: Merge may Break Functionality if main branch", or "Warning: May Violate Security Posture as Stated in Readme", or "Information: This PR adds external agentic capabilities to CyClaw. Carefully verify code changes before merge.", etc. that is all. leave the PR and or merge request open; this is just a comment informing me of outcome and other issues I may have missed (like potential conflicts in merge to main branch)
- If an open PR is failing any given CI check for longer than 72 hours and has failed with the same error more than 3 times consecutively in the prior checks, first report the repeated failure with the affected workflow, run IDs, and likely owner. Only edit the PR branch, rebase it, or restart CI after clear human approval or when you were explicitly invoked on that PR/comment. If approved, analyze the logs, reproduce the failure where practical, make the smallest branch-owned fix, rerun relevant checks, and leave a detailed comment describing the issue, change, verification, and any remaining risk.
- Use Claude Haiku llm when assessing code, Use an advanced model like Claude Opus 4.6  When attenpting to fix ci files if they fail.
- Use GitHub MCP tools for all GitHub API operations (comments, PR management, etc.); use the official GitHub CLI only as a fallback when MCP tools are unavailable or insufficient, after verifying the actual `gh` binary and authentication state.
- When assessing open PRs, briefly scan the following 4 files to ensure they are all consistent in what common install dependencies they reference: `requirements.txt`, `constraints.txt`, `pyproject.toml`, `Dockerfile`. If there is an inconsistency cross-file, leave a comment describing in detail the inconsistency/conflict and a detailed suggested next steps to resolve.
- After concluding any approved changes to workflow files associated with CI checks, report which open PRs may need a rebase or CI restart. Do not rebase all open PRs or push empty CI-trigger commits without clear human approval for those branches.

Forbidden Behaviors:
- Never make any changes or edits to a branch on an open PR without clear human approval or the ci checks already failing as described earlier
- Never edit the `.github/copilot-instructions.md` file or delete it without explicit and clear human approval
- if there are more than 5 PR's open or you are not explicitly requested from the pr or referenced via comment, **never** auto-assign yourself in the scenario described in core behaviors when 72 hours have passed and repeated ci check failures.

## CI Restart Workarounds

When CI checks on a PR are stuck, queued indefinitely, or need to be re-triggered without a real code change, use one of these two approaches:

### Scenario 1: CI checks never started or are permanently queued
Push an empty commit to the PR branch. This signals GitHub Actions to schedule a new run without altering any code:
```bash
git commit --allow-empty -m "ci: trigger CI restart"
git push origin <branch-name>
```
Use this when checks show as "Queued" or "Waiting" and do not progress after several minutes.

### Scenario 2: CI checks ran but need to be re-requested after a fix
Use the GitHub MCP tool (preferred) or `gh` CLI to re-request reviews / re-run failed jobs on the PR:
```bash
# Re-run all failed jobs for a workflow run (get run_id from Actions UI or MCP):
gh run rerun <run-id> --failed --repo CGFixIT/CyClaw
```
Or via GitHub MCP: call the workflow re-run API on the specific failed run ID.
Use this when checks already completed (passed or failed) and you want to re-trigger them after a fix — without pushing a new commit.

**When to use which:**
- Checks never started → use Scenario 1 (empty commit).
- Checks ran and failed → push a real fix commit, then use `gh run rerun --failed` (Scenario 2) if GitHub doesn't auto-trigger.
- Checks are queued across multiple open PRs after a CI file change → use Scenario 1 on each PR branch to unblock them in parallel.

Recommended clean setup:
```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade "pip>=26.1.2"
pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu
# Preferred (uv + pyproject.toml):
uv pip install -r pyproject.toml --constraint constraints.txt
# Legacy / CI-compat pip fallback:
pip install -r requirements.txt -c constraints.txt
```
Important:
- **Always install CPU-only torch first**. Otherwise Linux may try to pull the huge CUDA wheel.
- `requirements.txt` is now **deprecated**; prefer `uv pip install -r pyproject.toml --constraint constraints.txt` for local development.
- CI install gate runs `pip install -r requirements.txt -c constraints.txt pytest pytest-cov`; `constraints.txt` pins the full dependency set for reproducible CI runs (see PR #343).
- If PyYAML reinstall conflicts in your environment, repo docs note `pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML` as a fallback.
- Optional Postgres support exists via `psycopg[binary]`, but default behavior is local-file based.
- A `Dockerfile` exists for production deployments (Python 3.12-slim-bookworm, non-root `cyclaw` user, uv install preferred, exposes port 8000).

## Required local prep before running app/tests that touch runtime
Some directories/files are assumed to exist.
```bash
mkdir -p data/personality index logs
printf '# Soul\n' > data/personality/soul.md
export GROK_API_KEY=dummy
```
Notes:
- `GROK_API_KEY` can be any non-empty string in offline mode.
- `gate.py` fails closed for `/soul/*` mutations unless `CYCLAW_API_KEY` is set.
- Server binds to **`127.0.0.1:8787` only**; do not change to `0.0.0.0` unless explicitly asked.

## Build / run
### Build retrieval index
Run this before starting the server if `index/` is missing or corpus changed.
```bash
python -m retrieval.indexer
```
Requirements/preconditions:
- `data/corpus/` must contain `.md` or `.txt` files.
- `data/personality/soul.md` must exist.

Expected outputs:
- ChromaDB at `index/chroma_db`
- BM25 JSON at `index/bm25.json`

If startup says `Index not built`, run the indexer. If Chroma collection format is broken/stale, delete `index/` and rebuild.

### Run FastAPI server
```bash
uvicorn gate:app --host 127.0.0.1 --port 8787
```
or use the declared entry point:
```bash
cyclaw-server
```
Open `/` for the terminal UI, `/health` for readiness.

### Run MCP server
```bash
python mcp_hybrid_server.py
```
or:
```bash
cyclaw-mcp
```
This is retrieval-only by design; do not add sampling/LLM behavior casually.

## Lint / tests / validation
### Fast lint gate (matches PR lint workflow)
```bash
ruff check --select E,F,I,B,C4,S .
```
CI lint only enforces this narrowed rule set, even though `pyproject.toml` has broader Ruff config.

### Main CI-equivalent validation sequence
Run in this order:
```bash
python -m tests.ci_rag_smoke
pytest \
  tests/test_sanitizer.py \
  tests/test_security.py \
  tests/test_rate_limit.py \
  tests/test_audit.py \
  tests/test_client.py \
  tests/test_personality.py \
  tests/test_conftest_fixtures.py \
  tests/test_stemmer.py \
  tests/test_hybrid_search.py \
  tests/test_indexer.py \
  tests/test_embeddings.py \
  tests/test_graph.py \
  tests/test_gate.py \
  tests/test_telemetry_kill.py \
  tests/test_health.py \
  tests/test_mcp_server.py \
  tests/test_metrics.py \
  tests/test_sync_config.py \
  tests/test_sync_filters.py \
  tests/test_sync_runner.py \
  tests/test_sync_cli.py \
  tests/test_sync_scheduler.py \
  tests/test_sync_selftest.py \
  -q --tb=short
```
Why this order:
- It mirrors `ci.yml` and catches index/retrieval regressions before unit tests.
- CI also collects coverage and requires `fail_under = 80` in `pyproject.toml`.

Useful targeted checks:
```bash
bash .claude/skills/run-cyclaw/smoke.sh
python -m pytest tests/ -q
powershell -File tests/apipsTest.ps1   # Windows/manual live-server smoke
```

## CI / workflow facts that matter for PRs
- `.github/workflows/ci.yml`: main test gate on `main`, `cc`, and `feature/CyClaw-Agent`, Python 3.12, Ubuntu + Windows, 30 min timeout. Installs `torch==2.13.0+cpu`, then `pip install -r requirements.txt -c constraints.txt pytest pytest-cov`, prepares hermetic dirs, runs `tests.ci_rag_smoke`, then the explicit pytest file list with per-module `--cov` flags. Also includes non-blocking `discover-skills` + `verify-skills` jobs that run `.claude/skills/*/verify.sh` and `smoke.sh` in parallel (`continue-on-error: true`; does not gate merges).
- `.github/workflows/lint.yml`: PR lint gate for `main`/`cc`; runs only Ruff with `--select E,F,I,B,C4,S`.
- Security workflows exist for CodeQL, OSV, pip-audit, Gitleaks, DevSkim, Defender, Fortify. Avoid introducing secrets, vulnerable deps, telemetry, or unsafe network exposure.
- `pip-audit.yml` and `.osv-scanner.toml` intentionally ignore CVE-2026-45829 (ChromaDB Critical pre-auth RCE, no upstream patch) because CyClaw uses embedded `PersistentClient` (local/offline air-gapped only, no `HttpClient`, no `trust_remote_code`); do not "fix" that policy casually.

## Common gotchas / proven workarounds
- **Do not skip torch-first install**.
- **Do not assume LM Studio is running**. Tests and smoke paths are designed to pass structural flows without it; `/health` may be `degraded` without LM Studio and that can be acceptable if `index_ready` and `graph_ready` are true.
- **Do not edit `constraints.txt` manually** except as documented; regenerate from `pyproject.toml` if dependency work requires it.
- No `package.json` is present on current `main`; do not rely on Node tooling for validation unless a future change adds and documents it.
- `sync/` depends on external `rclone`; tests mock this, but runtime sync features may fail on machines without `rclone` installed.
- Telemetry kill env vars are intentionally set very early in `gate.py`; preserve that ordering before SDK imports.
- `data/personality/soul.md` is authoritative; avoid accidental mutation in tests/scripts.

## Change strategy
- Prefer minimal diffs in the correct subsystem.
- For policy/routing/security behavior, inspect `graph.py` and `gate.py` first.
- For retrieval quality/index errors, inspect `retrieval/` first.
- For soul/versioning/audit behavior, inspect `utils/personality.py` and `utils/logger.py`.
- Before finishing, run at least Ruff + the most relevant pytest targets, and use the full CI-equivalent sequence for risky or cross-cutting changes.
