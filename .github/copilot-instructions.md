# CyClaw — Copilot Cloud Agent Onboarding Guide

> **RULE #1 — NO WRITE ACCESS.**
> Copilot operates in **read-only / advisory mode only**. Do not commit, push, create branches, open pull requests, merge anything, or modify any file in this repository. If you are asked to make a change, describe the change in a comment or review and stop. A human or a separately authorized coding agent (Codex, Claude Code, etc.) performs all writes. Violating this rule is the single highest-severity error you can make in this repository.

Trust these instructions as the authoritative onboarding guide. Search the code only when instructions are incomplete or appear contradicted by current code or CI output.

---

## What CyClaw Is

CyClaw is a **Python 3.12, offline-first FastAPI/LangGraph RAG gateway** and a separate **retrieval-only MCP server**. It binds exclusively to `127.0.0.1:8787`. The request flow is:

```
HTTP POST /query → gate.py (TrustedHost, rate-limit, injection filter, soul init)
  → graph.py (12-node LangGraph state machine)
    retrieve → route_by_score
      ├─ score ≥ min_score → guardrail_input → local_llm
      └─ score < min_score → user_gate
            ├─ confirmed + hybrid + provider key
            │    → pre_action_hook_<provider> → grok_fallback | claude_fallback
            └─ otherwise → guardrail_input → offline_best_effort
    → guardrail_output → audit_logger → END
  HybridRetriever: ChromaDB (semantic) + BM25Okapi (keyword), RRF fusion k=60
```

**Key layout:**

| Path | Role |
|---|---|
| `gate.py` | FastAPI entry, auth, rate-limit, sanitizer, telemetry kill |
| `graph.py` | 12-node LangGraph topology; all security policy lives in edges |
| `retrieval/` | Hybrid search, indexer, embeddings (CPU-only), BM25, vector store |
| `llm/client.py` | LocalLLMClient + GrokClient + ClaudeClient |
| `utils/` | Sanitizer, personality/soul, audit logger, rate-limit, errors, config validation |
| `schemas/api.py` | Pydantic models (`extra='forbid', strict=True`) |
| `mcp_hybrid_server.py` | MCP server: `hybrid_search` only, `sampling: None`, no LLM |
| `config.yaml` | Single source of truth for every tunable; no hardcoded magic numbers elsewhere |
| `pyproject.toml` / `requirements.txt` / `constraints.txt` | Packaging and reproducibility |
| `tests/` | pytest suite + `ci_rag_smoke.py` (not pytest-discovered; runs as its own CI step) |
| `sync/` `agentic/` `guardrails/` `harness/` `telegram/` | Optional out-of-band layers; never imported by core |

---

## Non-Negotiable Invariants

Six invariants are design constraints enforced by graph wiring, not runtime flags. Check with `python3 .claude/skills/invariant-guard/check_invariants.py`.

| # | Invariant | Violated if you… |
|---|---|---|
| I1 | **RAG-first** — `retrieve` is the unconditional graph entry point | Add any node/edge that answers before `retrieve` runs |
| I2 | **Topology = Policy** — routing via graph edges only, never LLM decisions or ad-hoc `if` | Add runtime branches outside `score_router`/`guardrail_router`/`user_gate_router` |
| I3 | **Triple-gated external fallback** — Grok/Claude require `mode=="hybrid"` AND `<provider>.enabled` AND `user_confirmed_online` | Route to `grok_fallback`/`claude_fallback` without all three |
| I4 | **Audit convergence** — all paths reach `audit_logger` before END | Add any path to END that skips `audit_logger` |
| I5 | **Soul governance** — soul mutation requires a non-empty human `reason` string and goes through `PersonalityManager.apply_evolution` atomically with injection scan | Write `soul.md` without `reason`, or bypass `PersonalityManager` |
| I6 | **Module isolation** — `gate.py`, `graph.py`, `mcp_hybrid_server.py` never import `agentic`/`sync`/`guardrails`/`harness`/`telegram`, and vice versa | `import agentic` (or similar) anywhere in the core three |

**Additional non-negotiables:** telemetry-kill env block in `gate.py` must precede all heavy imports (verified by AST in invariant guard); services bind to loopback only (`127.0.0.1`), never `0.0.0.0`; BM25 index stays JSON — no pickle (RCE risk); MCP server declares `sampling: None` and has no LLM call path; `data/personality/soul.md` must not be deleted or autonomously mutated.

---

## Setup and Build Commands

**Python 3.12 required.** No Makefile, no `package.json`, no Node tooling.

```bash
# 1. Pin pip
python -m pip install --upgrade "pip>=26.1.2"

# 2. Linux / Windows: CPU torch FIRST (order is mandatory)
pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML

# macOS Apple Silicon: plain torch (no +cpu suffix), stripped manifests — see setup-guide.md#macos-apple-silicon
# ci.yml macos-latest leg and macos/install-cyclaw.sh Darwin branch already do this

# 3. Prepare runtime directories (required before server or tests touching gateway)
mkdir -p data/personality index logs
[ -f data/personality/soul.md ] || printf '# Soul\n' > data/personality/soul.md
export GROK_API_KEY=dummy   # any non-empty value; server boots without a real key

# 4. Install package (needed for console scripts: cyclaw-server, cyclaw-index, etc.)
pip install -e .

# 5. Build retrieval index (required before /query returns hits; missing index = 503, not a crash)
python -m retrieval.indexer   # or: cyclaw-index

# 6. Run server
python gate.py                # or: cyclaw-server  — binds 127.0.0.1:8787
curl -s http://127.0.0.1:8787/health  # "degraded" without Ollama is NORMAL
```

Do **not** overwrite the committed real `data/personality/soul.md` in normal local work.

---

## Validation Commands

```bash
# Invariant guard (run after any change to core files; must exit 0)
python3 .claude/skills/invariant-guard/check_invariants.py

# Lint (CI-enforced via lint.yml)
ruff check --select E,F,I,B,C4,UP,S .

# Full test suite (GROK_API_KEY must be non-empty)
GROK_API_KEY=dummy pytest tests/ -q --tb=short

# RAG smoke (intentionally NOT named test_*.py — do not rename it)
GROK_API_KEY=dummy python -m tests.ci_rag_smoke

# Agentic targeted tests
GROK_API_KEY=dummy pytest tests/test_agentic_*.py -q

# Postgres tests (require a live Postgres/pgvector service — skip in standard env)
pytest tests/test_personality_postgres.py tests/test_ratelimit_postgres.py tests/test_pgvector_store.py -q --tb=short

# Types — best-effort only, NOT CI-enforced; bare "mypy ." errors due to utils/ lacking __init__.py
mypy --strict --python-version 3.12 --explicit-package-bases "<touched files only>"

# CI-style coverage (see .github/workflows/ci.yml for exact --cov= flags and 80% fail_under gate)
```

Coverage `fail_under = 80` is configured in `pyproject.toml` and enforced by CI with explicit `--cov=` flags — bare `pytest` without those flags does not enforce it.

---

## CI / Workflow Map

| Workflow | Role |
|---|---|
| `ci.yml` | **Main blocking matrix** (Ubuntu + Windows + macOS): install gate, RAG smoke, pytest coverage, invariant guard, optional harness/Postgres/smoke steps |
| `lint.yml` | Ruff repo-wide + changed-file flake8/WPS |
| `python-package-conda.yml` | Conda environment CI |
| `codeql.yml` | GitHub CodeQL security scanning |
| `gitleaks.yml` | Secret scanning |
| `osv-scanner.yml` | OSV dependency vulnerability scan |
| `pip-audit.yml` | pip-audit dependency audit |
| `semgrep.yml` | Semgrep SAST |
| `devskim.yml` | DevSkim security linting |
| `defender-for-devops.yml` | Microsoft Defender for DevOps |
| `fortify.yml` | Fortify SAST |
| `copilot-setup-steps.yml` | Copilot environment bootstrap |

Workflow files are actionlint/zizmor checked; third-party actions are SHA-pinned.

---

## Common Traps

- **No Makefile, no `package.json`.** No Node, no `npm`/`yarn`/`pnpm`, no `just`, no `make`.
- **Torch must be installed first** (Linux/Windows). Installing `requirements.txt` before `torch+cpu` pulls the wrong wheel.
- **Ollama is never assumed to be running.** `status: degraded` in `/health` is normal when Ollama is absent; tests mock it.
- **Missing index is fail-soft** (503 `INDEX_NOT_FOUND`), not a crash. Build it explicitly with `python -m retrieval.indexer`.
- **Console scripts** (`cyclaw-server`, `cyclaw-index`, `cyclaw-metrics`, `cyclaw-mcp`, `cyclaw-clear-cache`) only work after `pip install -e .`.
- **`ci_rag_smoke.py` must not be renamed** to `test_ci_rag_smoke.py`. It is deliberately excluded from pytest auto-discovery and runs as its own CI step.
- **Do not edit `constraints.txt` manually.** Regenerate from `pyproject.toml` if dependency work is needed. `pydantic` and `pydantic-core` are lock-step; `numpy` is pinned `<2`; `uvicorn` constraint carries no extras.
- **Do not use bare `mypy .`** — it fails immediately due to `utils/` having no `__init__.py`. Scope to touched files with `--explicit-package-bases`.
- **Do not bind services to `0.0.0.0`.** Loopback only (`127.0.0.1:8787`).
- **No `TODO`/`FIXME` comments.** Encode intent in explanatory comments matching surrounding style.
- **Raise typed errors** from `utils/errors.py` (rooted at `RAGError`). No bare `raise Exception(...)` or `except:`.
- **Use `subprocess.run([...], list-form)`** — never `shell=True` with user input.
- **Do not commit** caches, indexes, logs, `.env` files, secrets, or local path artifacts.
- **`min_score: 0.028`** is on the RRF fusion scale (scores rarely exceed ~0.1). Do not "fix" it toward cosine-like 0.5.
- **`security.require_env`** in `config.yaml` is decorative — no code reads it. The server boots without `GROK_API_KEY`; Grok just reports unavailable.

---

## Development Workflow (for authorized coding agents, not Copilot)

Included here so Copilot can accurately advise on process when asked.

1. Branch from current `origin/main`; use `agent/<topic>`, `claude/<topic>`, `codex/<topic>`, or another documented namespace (see `CLAUDE.md` §5).
2. Make narrow, targeted changes. Touch only files named in the task.
3. Add or update tests; ensure they use `tests/conftest.py` fixtures and start no live service.
4. Run `python3 .claude/skills/invariant-guard/check_invariants.py` — must exit 0.
5. Run `ruff check --select E,F,I,B,C4,UP,S .` — must be clean.
6. Run `GROK_API_KEY=dummy pytest tests/ -q --tb=short` — must be green.
7. Use conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `ci:`, `test:`).
8. Open a **draft PR**; body = What / Why / Risk to monitor. One concern per PR.
9. Never push to `main` directly.
10. Report skipped checks honestly (e.g. Postgres tests skipped — no live service).

**Canonical operating contract:** `CLAUDE.md` (invariants, module map, load-bearing numbers, traps, conventions, escalation tiers). `AGENTS.md` is the parallel Codex-oriented layer. When in doubt, read `CLAUDE.md` §3-7 before acting.
