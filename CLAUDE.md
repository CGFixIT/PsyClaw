# CLAUDE.md — CyClaw Operating Manual

This is the operating contract for every agent working in this repository. It is
written to be followed literally. Where a rule gives a number, use that number.
Where it says "never," there is no exception without explicit user approval.
Read it fully before acting. It **overrides** your default behavior.

If you do only one thing before editing: run
`python3 .claude/skills/invariant-guard/check_invariants.py` to learn the shape
of what must not break.

---

## 1. Read Me First

**What CyClaw is.** A Python 3.12 FastAPI RAG server (`gate.py`) fronting a
LangGraph security topology (`graph.py`), with hybrid ChromaDB + BM25 retrieval,
a local LLM via LM Studio, and triple-gated optional external fallbacks (Grok
and/or Claude, selected per-query via `online_provider`). It binds
**only** to `127.0.0.1:8787`. A separate retrieval-only MCP server
(`mcp_hybrid_server.py`) exposes search with no LLM path.

**Current project mode: FEATURE FREEZE (as of 2026-07-03).** CyClaw is a
polished portfolio artifact, not a product under active feature development.
Polish, hardening, docs, tests, and bug fixes **pass** the bar. New features
need explicit user justification first. Before proposing new code, apply the
operative test: *"Does this polish the portfolio signal or fix a real defect?"*
If it is a new capability, stop and ask. Full rationale:
`docs/memories/CONSOLIDATED.md`.

**Where truth lives.** In priority order:
1. **Code** — the running behavior. When docs and code disagree, code wins.
2. **`config.yaml`** — the single source of truth for every tunable. No
   hardcoded tunables anywhere else.
3. **`docs/THREAT_MODEL.md`** — the security scope: single-operator,
   loopback-bound, single-tenant. Not multi-tenant, not a sandbox for untrusted
   code. Read it before touching anything security-related.
4. This file and `.claude/rules/PROJECT_RULES.md` — the operating rules.
   `AGENTS.md` is the parallel guidance for other agents; keep them consistent.

---

## 2. The Map

### Request flow

```
HTTP POST /query   (or MCP tools/call: hybrid_search)
        │
        ▼
   gate.py  — TrustedHost check → rate limit (60/min per IP) → injection filter
              → soul init → graph invoke (wrapped in 330s timeout)
        │
        ▼
   graph.py  (LangGraph 9-node state machine)
   retrieve → route_by_score
              ├─ score ≥ min_score → guardrail_input (offline input rail; opt-in,
              │                       pass-through when guardrails.enabled=false)
              │                       ├─ blocked → audit_logger
              │                       └─ passed  → local_llm
              └─ score < min_score → user_gate
                                     ├─ confirmed + hybrid + selected provider usable
                                     │    → grok_fallback | claude_fallback
                                     └─ declined / offline / no key → offline_best_effort
              ↓ (all upstream nodes converge)
              audit_logger → END
        │
        ▼
   HybridRetriever — ChromaDB (semantic) + BM25Okapi (keyword) → RRF fusion (k=60)
```

`retrieve` is the unconditional first node. Routing is graph edges, never an LLM
decision.

### All HTTP routes (gate.py)

| Method | Route | Auth | Notes |
|---|---|---|---|
| GET | `/` | none | serves `static/terminal.html` |
| GET | `/static/*` | none | static mount |
| POST | `/query` | none | rate-limited, sanitized; 400/429/503/504/500 |
| GET | `/health` | none | `degraded` without LM Studio is NORMAL |
| GET | `/soul` | **API key** | rate-limited |
| POST | `/soul/propose` | **API key** | advisory scan, never writes |
| POST | `/soul/apply` | **API key** | enforced scan + atomic write; requires `reason` |
| POST | `/soul/reload` | **API key** | |
| POST | `/soul/restore` | **API key** | from `.bak` |
| GET | `/audit/summary` | **API key** | rate-limited; aggregates only, no raw queries |
| POST | `/ops/sync` | **API key** | rate-limited; subprocess shim |
| POST | `/ops/agentic` | **API key** | rate-limited; subprocess shim |
| POST | `/ops/fsconnect` | **API key** | rate-limited; subprocess shim |
| POST | `/ops/sqlconnect` | **API key** | rate-limited; subprocess shim |

The four `/ops/*` endpoints reach out-of-band subsystems ONLY through
`utils/ops_runner.py` (a `subprocess.run([...])` shim). They never import those
subsystems.

### Key modules

| Path | Role |
|---|---|
| `gate.py` | FastAPI entry, auth, rate limit, sanitizer, security headers, telemetry kill |
| `gate_ops.py` | The four `/ops/*` endpoints, registered onto gate.py's app with its auth/rate-limit/audit callables injected; never imports `sync`/`agentic` |
| `graph.py` | 9-node LangGraph topology; all security policy lives in the edges |
| `retrieval/hybrid_search.py` | RRF fusion (k=60) over ChromaDB + BM25 |
| `retrieval/indexer.py` | Corpus ingestion, chunk sanitization (`cyclaw-index`) |
| `retrieval/embeddings.py` | Local CPU embeddings; triple `lru_cache` |
| `retrieval/stemmer.py` | Porter stemmer + custom vocab; avoids NLTK punkt (CVE) |
| `retrieval/vector_store.py` | Pluggable reader/writer: embedded ChromaDB (default) or pgvector |
| `retrieval/clear_cache.py` | Dry-run-by-default embedding-cache cleaner (`cyclaw-clear-cache`) |
| `llm/client.py` | `LocalLLMClient` + `GrokClient` + `ClaudeClient`; shared bounded-retry `_post_with_retry` |
| `utils/sanitizer.py` | Injection filter; patterns in `config.yaml` |
| `utils/personality.py` | Soul versioning, SHA-256 drift detection, injection gate on write |
| `utils/personality_db.py` | Soul DB backend: SQLite default, Postgres via `CYCLAW_DB_URL` |
| `utils/logger.py` | Audit JSONL; SHA-256 query hashing, recursive PII redaction |
| `utils/ratelimit.py` | Per-IP rate limiting; in-memory / SQLite / Postgres |
| `utils/health.py` | `check_all()` behind `/health`; skips Grok/Claude probes when their key is unset |
| `utils/errors.py` | Typed exception hierarchy rooted at `RAGError` |
| `utils/config_validation.py` | Boot-time config validation; fails fast |
| `utils/ops_runner.py` | Subprocess shim behind the four `/ops/*` endpoints |
| `utils/guardrail_bridge.py` | Inversion shim: builds the `guardrail_input` node's callable, or `None` when disabled; the only module through which `graph.py` reaches `guardrails/` (never a direct import) |
| `schemas/api.py` | Pydantic models (`extra='forbid', strict=True`) |
| `metrics.py` | `audit.jsonl` analyzer (`cyclaw-metrics`) |
| `mcp_hybrid_server.py` | MCP server: `hybrid_search` only, no LLM, `sampling: None` |
| `sync/` | Out-of-band Dropbox corpus sync (`python -m sync.cli`) |
| `agentic/` | Out-of-band GitHub context + governed skills registry (`python -m agentic.cli`) |
| `agentic/fsconnect/` | Out-of-band local/SMB filesystem connector; POSIX-only security core |
| `agentic/sqlconnect/` | Out-of-band SQL connector; SELECT/WITH-only guard |
| `guardrails/` | Optional NeMo Guardrails; soft-imported, disabled by default. Phase 2 wires an offline input rail into `graph.py`'s `guardrail_input` node when `enabled: true`, via `utils/guardrail_bridge.py` — still opt-in, still never imported directly by `gate.py`/`graph.py` |

### Load-bearing numbers (all from `config.yaml`/`pyproject.toml` — do not invent)

| Value | Setting | Note |
|---|---|---|
| `127.0.0.1:8787` | `api.host`/`api.port` | loopback only, never a public interface |
| `0.028` | `retrieval.min_score` | **RRF scale**, not cosine. Fused scores rarely exceed ~0.1 |
| `60` | `retrieval.rrf_k` | RRF fusion constant |
| `330` | `api.graph_timeout_sec` | must exceed `local_llm.timeout_sec` (300) |
| `300` / `3000` | `local_llm.timeout_sec` / `max_tokens` | |
| `8000` | `personality.soul_max_chars` | soul is capped |
| `4000` | `retrieval.max_context_tokens` | prompt context budget |
| `512` / `50` | `indexing.chunk_size` / `chunk_overlap` | overlap must stay `< chunk_size` |
| `60` per `60`s | `api.rate_limit` | per-IP |
| `33` | `banned_patterns` length | **documentary count**; the *phrases* are contractual (see §4) |
| `80` | `coverage fail_under` | in `pyproject.toml`, not `ci.yml` |
| `qwen2.5-7b-instruct` | `local_llm.model` | LM Studio |
| `grok-4.3` | `grok.model` | disabled by default |
| `claude-sonnet-5` | `claude.model` | disabled by default; second external fallback (PR #441) |

---

## 3. The Six Invariants

These define CyClaw. Five are enforced by graph topology; the sixth by import
structure. They are not prompts or runtime checks — they are wiring.
`python3 .claude/skills/invariant-guard/check_invariants.py` verifies all six
statically; run it after any change to the core files.

| # | Invariant | Enforced in | Locked by test | You violate it if you… |
|---|---|---|---|---|
| I1 | **RAG-first** — `retrieve` is the unconditional entry; no LLM call precedes retrieval | `graph.py` `set_entry_point("retrieve")` | `test_graph` | add a node/edge that answers before `retrieve` runs |
| I2 | **Topology = policy** — routing is graph edges only, never an LLM or ad-hoc `if` | `graph.py` `score_router`/`guardrail_router`/`user_gate_router` | `test_graph` | add a runtime branch that decides routing outside the three routers |
| I3 | **Triple-gated external fallback** — a call to Grok or Claude needs `mode=="hybrid"` AND `<provider>.enabled` AND `user_confirmed_online`, all three, for whichever provider is selected (`online_provider`) | `gate.py` construction + `graph.py` `user_gate_router` | `test_graph`, `test_gate` | route to `grok_fallback`/`claude_fallback` without all three conditions for that provider |
| I4 | **Audit convergence** — all eight upstream paths reach `audit_logger` before END | `graph.py` edges | `test_graph` | add a node with a path to END that skips `audit_logger` |
| I5 | **Soul governance** — soul mutation requires a human `reason` string; writes are atomic | `utils/personality.py` `apply_evolution` | `test_personality` | write `soul.md` without a non-empty `reason`, or bypass `PersonalityManager` |
| I6 | **Module isolation** — `gate.py`/`graph.py`/`mcp_hybrid_server.py` never import `agentic`/`sync`/`guardrails`, and those never import the core three | import graph | `test_agentic_isolation` (AST, both directions) | `import agentic` (etc.) anywhere in the core three to "reuse" something |

Supporting guards (also checked by `invariant-guard`): telemetry-kill precedes
heavy imports in `gate.py`; unset `CYCLAW_API_KEY` fails auth **closed** (401);
the sanitizer contract phrases stay caught; BM25 stays JSON (pickle = RCE); MCP
declares `sampling: None`.

**Before touching `gate.py`, `soul.md` handling, or the scanner, read
`INVARIANTS.md`** (repo root). It records which of these guarantees are enforced by
code vs. by convention (e.g. two of the three external-provider gates live in
`gate.py` construction, not the graph; the soul injection scan is write-path-only),
and names the test that pins each. `tests/test_due_diligence_invariants.py` is the
regression harness.

---

## 4. Mistakes You Will Make Here (and the rule that prevents each)

These are real traps in *this* codebase, verified against the code. Each pairs a
mistake a capable-but-unfamiliar agent makes with the rule that prevents it.

### Environment & install
- **Trap:** `pip install -r requirements.txt` fails or pulls a CUDA torch.
  **Rule:** install `torch==2.12.1+cpu` from the PyTorch CPU index **first**,
  then `pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML`.
- **Trap:** running `pytest` in a fresh container — no deps are installed.
  **Rule:** a freshly-cloned container has NO Python deps. Install first
  (`/run-cyclaw` or `/sandbox-runtime-verification`) before any test/run step.
- **Trap:** assuming the server refuses to boot without `GROK_API_KEY`.
  **Rule:** `security.require_env` is **decorative** — no code reads it. The
  server boots fine; Grok just reports unavailable. Tests only need
  `GROK_API_KEY=dummy` (any non-empty value).
- **Trap:** treating `status: degraded` in `/health` or `TELEMETRY KILL` on
  startup as errors. **Rule:** both are normal (no LM Studio; intentional env
  blocking).

### Boot semantics
- **Trap:** adding a hard failure when `data/personality/soul.md` is missing.
  **Rule:** it **self-heals** — `PersonalityManager._load_soul` writes a default
  and records a version. Do not add a boot crash. (There is no `soul_hash`
  constant; the baseline is the newest `soul_versions` DB row.)
- **Trap:** expecting the server to build the index on first run.
  **Rule:** a missing index is **fail-soft** — `/query` returns 503
  `INDEX_NOT_FOUND`. Build it explicitly: `python -m retrieval.indexer`.
- **Trap:** assuming no `CYCLAW_API_KEY` means soul endpoints are open.
  **Rule:** unset key = **fail closed (401)**, not open. Uses
  `hmac.compare_digest`.
- **Trap:** reordering imports in `gate.py` "to tidy them."
  **Rule:** the `_TELEMETRY_KILL` env block MUST stay above the heavy imports
  (`graph`, `retrieval`, `langchain`, `chromadb`). Setting the env after they
  load lets telemetry escape. `test_telemetry_kill` locks this.

### Retrieval & config
- **Trap:** "fixing" `min_score: 0.028` upward toward a cosine-like 0.5.
  **Rule:** it is on the **RRF scale**; fused scores rarely exceed ~0.1. Raising
  it routes every query to the user gate. Leave it unless retuning retrieval
  deliberately.
- **Trap:** unifying the test mock's `min_score` (0.75) with production (0.028).
  **Rule:** they are intentionally different and both load-bearing. The mock
  high/low scores straddle 0.75; production RRF scores straddle 0.028.
- **Trap:** setting a config key to change the embedding cache size.
  **Rule:** the size is fixed at import via `lru_cache`; only the env var
  `CYCLAW_EMBED_CACHE_SIZE` changes it.
- **Trap:** editing `config.yaml` in a running process and expecting new
  patterns. **Rule:** the sanitizer `lru_cache`s by config path. Re-run the
  process. (`enabled: true` + zero patterns silently degrades to length-only.)

### Testing
- **Trap:** changing conftest `test_config` to a shallow `.copy()`.
  **Rule:** it MUST stay a deepcopy — a shallow copy leaks mutations across
  tests (order-dependent flakes). `test_conftest_fixtures` guards it.
- **Trap:** `import gate` at a test module's top level.
  **Rule:** importing gate triggers full app init (FastAPI + ChromaDB +
  retriever). Use a subprocess (`test_telemetry_kill`) or module-level patching
  (`test_gate`).
- **Trap:** running `pytest` locally, seeing green, assuming coverage passed.
  **Rule:** bare `pytest` runs **no** coverage (`addopts` has no `--cov`). The
  80% gate is `fail_under` in `pyproject.toml`, applied only with the CI-style
  explicit `--cov=` flags. New **source modules** need a `--cov=` flag in
  `ci.yml` AND an entry in `[tool.coverage.run] source`; new **test files**
  auto-discover.
- **Trap:** renaming `ci_rag_smoke.py` to `test_ci_rag_smoke.py`.
  **Rule:** it is deliberately NOT `test_*`-named so pytest ignores it; it runs
  as a separate CI step. Renaming double-runs it and drags ChromaDB into the
  unit lane.
- **Trap:** a fresh `MockGrokClient` to simulate "no API key."
  **Rule:** it defaults `available=True`. Pass `available=False` for that path.
- **Trap:** trimming `banned_patterns` and assuming a count test catches it.
  **Rule:** no test asserts `== 33`. But `TestShippedConfigContract` runs
  specific **phrases** against the real config — deleting a documented phrase
  fails tests. Adding patterns is safe; removing coverage is not.
- **Trap:** adding a state-changing POST route without touching the console
  contract. **Rule:** `test_terminal_contract` extracts routes from
  `terminal.html`; new POST endpoints must be added to its `_POST_PATHS`.
- **Trap:** assuming `mypy --strict --python-version 3.12 .` runs clean, or is
  a CI gate. **Rule:** it is neither. `ci.yml`/`lint.yml` run `ruff` only —
  mypy is not wired into any CI workflow. The bare repo-root invocation errors
  out immediately on `utils/errors.py` ("Source file found twice under
  different module names") because `utils/` has no `__init__.py`; add
  `--explicit-package-bases` to get past discovery, and even then the tree
  carries pre-existing untyped legacy code and missing third-party stubs (found
  during Phase 2 verification, 2026-07-09). Treat it as a best-effort check
  scoped to the lines you actually wrote, not a pass/fail gate on the whole
  repo or even a whole file you only partly touched.

### Code conventions
- **Trap:** `raise Exception(...)` or a bare `except:`.
  **Rule:** raise a typed error from `utils/errors.py` (rooted at `RAGError`
  with `.code`/`.message`/`.details`). Out-of-band subsystems use their own
  subtrees. (`RcloneTimeoutError` deliberately bypasses its parent `__init__` to
  keep its sub-code — do not "simplify" it.)
- **Trap:** changing an error `code` string or the `"{code}: {message}"` stamp
  format. **Rule:** these are asserted verbatim in `test_graph`. Also: success
  paths must NOT emit an `error` key (it would clobber an upstream error).
- **Trap:** returning generic `sys.exit(1)` from a CLI.
  **Rule:** exit codes are an API. Agentic: `0` ok · `2` failed · `3` env/config
  · `4` write refused. Sync uses `10` = corpus-changed → reindex. `clear_cache`:
  `0`/`2`/`3`. Match them.
- **Trap:** "optimizing" the BM25 store to pickle.
  **Rule:** BM25 stays JSON (`index/bm25.json`). Pickle is an RCE vector;
  `test_security` guards it.
- **Trap:** logging raw query text "for debugging."
  **Rule:** the audit log stores SHA-256 hashes only; raw text is never
  persisted. `test_gate` enforces it.
- **Trap:** "adding the missing `check_input`" to the MCP server.
  **Rule:** the MCP path deliberately does NOT sanitize — there is no LLM behind
  it (`sampling: None`). This is a documented non-goal, not a gap.
- **Trap:** re-reading `config.yaml` inside a module, or using cwd-relative
  paths. **Rule:** config is loaded once and passed down as a `cfg` dict. Paths
  anchor to `_BASE_DIR`/`_REPO_ROOT`, never cwd (Windows double-click breaks
  cwd assumptions).

### Dependencies
- **Trap:** bumping `pydantic-core` alone, or adding `[standard]` to the uvicorn
  constraint. **Rule:** `pydantic` and `pydantic-core` are lock-step
  (2.13.4 ↔ 2.46.4); the `uvicorn` constraint carries no extras (pip ≥26.1.2
  rejects extras in a constraints file).
- **Trap:** letting numpy float to 2.x. **Rule:** numpy is pinned `<2`
  (dependabot ignores `numpy>=2.0.0`) — numpy 2 removes `np.float_` and breaks
  chromadb/onnxruntime.
- **Trap:** "fixing" the chromadb CVE. **Rule:** it is risk-accepted,
  embedded-`PersistentClient`-only per the threat model. Do not switch to the
  HTTP client or file a fix PR.

### Git & PR
- **Trap:** pushing to `main` via the GitHub MCP while a feature branch + open PR
  exist. **Rule:** never — it creates add/add rebase conflicts. Branch → draft
  PR → human merges.
- **Trap:** force-pushing after a rebase without asking.
  **Rule:** a force-push (`--force-with-lease`) needs explicit user sign-off
  first (the session runtime blocks it otherwise).
- **Trap:** two branches editing the same shared file (`ci.yml`, `config.yaml`,
  manifests, `CLAUDE.md`) cut from the same base. **Rule:** trial-merge the pair
  locally before opening PRs; a careless conflict resolution drops one side.
- **Trap:** diagnosing a child PR's red CI as the PR's fault.
  **Rule:** a red `main` poisons every branch cut from it. Check `main`'s own
  state first.

---

## 5. Conventions

### Code
- **Python 3.12** (`requires-python >=3.12`). Fully type-annotated; PEP 604
  unions (`str | None`), builtin generics, `TypedDict`/`Protocol`/`Literal` over
  `Any`. `from __future__ import annotations` in new modules.
- **Lint:** `ruff check --select E,F,I,B,C4,UP,S .` (line-length 120, `E501`
  ignored, `.claude` excluded) — this IS CI-enforced. **Types:**
  `mypy --strict --python-version 3.12 --explicit-package-bases` as a
  best-effort discipline on the lines you write — not CI-enforced, and the
  repo does not pass it clean end-to-end today (see the §4 Testing trap).
- **No `print`** in library code — use `logging.getLogger("cyclaw.<module>")`
  with lazy `%s` formatting. Audit is a separate JSONL stream.
- **No `shell=True`** with user input; always `subprocess.run([...], list-form)`.
- **No secrets in code** — env vars only. **No TODO/FIXME comments** — this repo
  has none; encode intent in explanatory comments instead (match the density and
  "why, with PR reference" style of the surrounding code).
- Data-modifying scripts default to a safe dry-run (`--apply` to act).

### Commits, branches, PRs
- **Conventional commits:** `feat:`/`fix:`/`perf:`/`chore:`/`test:`/`docs:`/`ci:`
  (`feat(scope):` when scoped).
- **Branches:** short-lived `claude/<topic>` (or `codex/<topic>`), deleted after
  merge. Develop on the assigned feature branch; never on `main`.
- **PRs are draft.** One reviewable concern each. Body = **What / Why / Risk to
  monitor**. A human decides when to merge.

### Docs
- Dated audit/report docs go in `docs/audits/`. Live memory lives ONLY in
  `docs/memories/` (`.claude/memory/` is legacy — do not add there).
- Every `##` section must be self-contained (no pronoun references to earlier
  sections) — the corpus is chunked and searched section-by-section.
- Don't duplicate another doc's authority; link to it. `config.yaml` owns the
  numbers — cite, don't copy-and-drift.

---

## 6. Quality Bar per Deliverable (checkable criteria, not adjectives)

A deliverable is done only when its box is fully checked.

**Code change**
- [ ] `ruff check --select E,F,I,B,C4,UP,S .` clean (CI-enforced)
- [ ] `mypy --strict --python-version 3.12 --explicit-package-bases` clean on
      the lines you actually wrote (best-effort; not CI-enforced, and the repo
      does not pass it clean end-to-end — see §4 Testing trap)
- [ ] `GROK_API_KEY=dummy pytest tests/ -q --tb=short` green
- [ ] CI-style coverage run ≥ 80% and the gate not lowered
- [ ] no new dependency without an exact pin in `pyproject.toml` AND
      `constraints.txt`
- [ ] the diff touches only files named in the task (no drive-by edits)
- [ ] each of the six invariants is untouched, OR the change is argued
      explicitly in the PR body and `invariant-guard` re-run
- [ ] `python3 .claude/skills/invariant-guard/check_invariants.py` exits 0

**Test**
- [ ] uses `tests/conftest.py` fixtures; starts no live service
- [ ] asserts behavior/contract, not incidental implementation detail
- [ ] deterministic — no real `sleep` racing a timeout, no network, no clock
- [ ] discovered by pytest without editing `ci.yml`

**Pull request**
- [ ] draft; conventional-commit title; body has What / Why / Risk to monitor
- [ ] one concern; diff reviewable in one sitting
- [ ] if it shares a file with another open PR, a local trial-merge was verified
- [ ] CI green, or each failure explained against `main`'s own state

**Documentation**
- [ ] every number sourced from `config.yaml`/`pyproject.toml` (none invented)
- [ ] `##` sections self-contained; dated if it is a report
- [ ] `python3 .claude/skills/doc-sync/doc_sync.py` shows no new drift it caused

**Skill**
- [ ] YAML frontmatter (`name`, `description`); numbered steps with exact commands
- [ ] a Guardrails section restating the relevant invariants
- [ ] a Gotchas section
- [ ] a self-contained `verify.sh` if it ships an executable (CI auto-runs it,
      non-blocking) — must exit 0 when healthy and skip cleanly without deps

---

## 7. When Uncertain — Escalation Rules

Classify every action before doing it.

| Tier | Concrete triggers | Required action |
|---|---|---|
| **Low** | Local, reversible, narrow: format, add a test, fix a doc typo, read code, run a documented command | Proceed. Standard checks. |
| **Medium** | Shared code path, recoverable: edit a module's logic, add a dependency, change a fixture, refactor within one subsystem | Proceed; expand tests; state the rollback path in the PR body |
| **High** | Irreversible or broad: destructive command, force-push, `soul.md` mutation, editing a graph edge / auth / sanitizer pattern, weakening any test, a new runtime dependency, anything touching a security invariant | **Stop and ask first** |

When unsure which tier, choose the higher one.

**Always ask first (High):** deleting/overwriting files you didn't create;
force-push; `git push` to `main`; mutating `soul.md`; changing graph edges,
`banned_patterns`, or auth; loosening a test or the coverage gate; adding a
runtime dependency; wiring a new hook in `settings.json`.

**Never ask (just do it):** running the documented test/lint/run commands;
reading anything; adding tests; fixing doc typos; `ruff format`; running any of
the `.claude/skills/*/check_*.py` or `verify.sh` checkers.

**Mid-task ambiguity with two reasonable readings:** state your assumption,
proceed on the **smallest reversible** interpretation, and flag it in the PR
body — do not stall. Use `AskUser` only when the readings diverge on something
irreversible or a matter of user taste.

**Blocked:** record it in `docs/SESSION_NOTES.md` (or `.claude/session-notes/`)
and escalate — `#cyclaw-dev` for undefined behavior, a private GitHub security
issue for security concerns, `/sandbox-runtime-verification` for suspected
config drift.

Do NOT: re-ask a question already answered; ask the user to reveal a secret;
change code behavior to make a stale doc "true" (fix the doc, or flag the
behavior gap).

---

## 8. Commands That Work

Skills reference this section; keep it as the single canonical copy.

```bash
# Install (order matters — torch CPU FIRST)
pip install torch==2.12.1+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML

# Build the retrieval index (required before /query returns hits)
python -m retrieval.indexer            # or: cyclaw-index

# Tests (GROK_API_KEY must be any non-empty value)
GROK_API_KEY=dummy pytest tests/ -q --tb=short
GROK_API_KEY=dummy pytest tests/test_graph.py -q --tb=short   # single file
GROK_API_KEY=dummy pytest tests/test_agentic_*.py -q          # agentic only
GROK_API_KEY=dummy python tests/ci_rag_smoke.py               # real-index RAG smoke

# Lint / types (ruff is CI-enforced; mypy is a best-effort local check only —
# see the §4 Testing trap for why the bare "mypy ... ." invocation errors out)
ruff check --select E,F,I,B,C4,UP,S .
mypy --strict --python-version 3.12 --explicit-package-bases <touched files>

# Run the server + probe health
python gate.py            # or: cyclaw-server   (binds 127.0.0.1:8787)
curl -s http://127.0.0.1:8787/health

# Run the retrieval-only MCP server (stdio; no LLM path)
python mcp_hybrid_server.py                    # or: cyclaw-mcp

# Metrics / cache
python -m metrics                              # or: cyclaw-metrics
python -m retrieval.clear_cache                # dry-run; add --apply to delete

# Invariant / doc / retrieval / sanitizer health (skills)
python3 .claude/skills/invariant-guard/check_invariants.py
python3 .claude/skills/doc-sync/doc_sync.py
python3 .claude/skills/index-doctor/doctor.py --rebuild
python3 .claude/skills/injection-redteam/redteam.py
```

CI target is Python 3.12 (ubuntu + windows matrix). Coverage sources:
`gate`, `graph`, `mcp_hybrid_server`, `metrics`, `llm`, `retrieval`, `utils`,
`sync`, `agentic`, `guardrails`. `tests/conftest.py` mocks all external deps —
no live services required. The full test-file list is discoverable in `tests/`
(~60 files, auto-collected by pytest).

---

## 9. Skills

Skills live at `.claude/skills/<name>/SKILL.md`. When a skill is not present in
the local sandbox, **check GitHub main before declaring it absent** (via
`mcp__github__get_file_contents` on `.claude/skills/<name>/SKILL.md`).

### CyClaw-specific security & health skills (built on the invariants)

| Skill | Type | Purpose | Runs pre-install? |
|---|---|---|---|
| `/invariant-guard` | check | Static-assert the six invariants + guards against a diff | Yes (stdlib) |
| `/injection-redteam` | loop | Adversarial probe corpus vs the sanitizer; close bypasses | Needs venv |
| `/index-doctor` | check | Rebuild + validate ChromaDB/BM25/RRF; probe retrieval health | Needs venv |
| `/doc-sync` | check | Detect code↔docs drift; reconcile the docs | Needs PyYAML |

### Operational & workflow skills

| Skill | Type | Purpose |
|---|---|---|
| `/run-cyclaw` | task | Smoke-test the FastAPI server |
| `/CyClaw-Optimize` | task | Scan main for optimizations; open focused draft PRs |
| `/CyClaw-Sandbox` | task | Clone main, mock LM Studio, full audit, dated report + PR |
| `/sandbox-runtime-verification` | task | Full Python 3.12 runtime gate |
| `/architecture-refactor` `/speed-refactor` `/tests-refactor` `/logging-refactor` | loop | Iterative refactor loops |
| `/wrap-up` | task | End-of-session checklist (ship / remember / improve / publish) |
| `/create-session-notes` | task | Maintain `SESSION_NOTES.md` |
| `/ponytail` | mode | Lazy-senior-dev mode: YAGNI, stdlib-first, minimal abstraction |

### Agent skills

`/solution-architect`, `/verification-specialist`, `/code-explorer`,
`/general-purpose`, `/documentation-guide`, `/next-action-suggestion`,
`/conversation-summary`, `/session-title`, `/tool-summary`, and the memory
skills (`/memory-extraction`, `/memory-consolidation`, `/memory-orchestrator`).
`/python-coding-agent` auto-loads via the SessionStart hook.

### Cross-repo behavioral skill

`/fable-protocol` — reasoning-discipline and epistemic-calibration layer (mark
speculation, verify stale knowledge, security lens on every generated artifact,
anti-sycophancy, Sonnet 5 vs Opus 4.8 routing). Scoped to the user, not to
CyClaw: it is registered both at `.claude/skills/fable-protocol/SKILL.md` in
this repo and at the user-level `~/.claude/skills/fable-protocol/SKILL.md`, so
it activates in any repository, not only here. It does not encode CyClaw
architecture and carries no authority over the six invariants in §3 — those
still govern. See the skill file for the full protocol.

---

## 10. Session Protocol

**Start.** The SessionStart hook injects the Python-coding-agent persona and (via
`session-start-sync-check.sh`, if wired) pins the git identity and reports
local↔remote divergence without mutating. If it did not run, set the identity
yourself before any commit:

```bash
git config user.email noreply@anthropic.com
git config user.name Claude
```

A stop hook, applied by the **session runtime** (not wired in repo
`settings.json`), rejects commits whose committer email is not
`noreply@anthropic.com` and blocks `--force-with-lease` without explicit
authorization.

**During.** Track compact memory: Goal, Constraints, Decisions (with one-line
rationale), Open questions, Verification state. Prefer file-backed facts over
inference. Expire stale assumptions when new evidence appears.

**End.** Run `/wrap-up`. Durable project conventions → this file or
`.claude/rules/`. Session-scoped discoveries → `docs/memories/` (live) via the
memory skills. Reconcile this file against the current code state — run
`/doc-sync` — before you consider the session done.

---

## Reference

- Behavioral patterns: `.claude/patterns/01`–`09` (reference explicitly; not
  auto-loaded).
- Utility prompts: `.claude/utility-prompts/` (coordinator, next-action,
  session-title, tool-summary).
- Multi-agent work: you (coordinator) own synthesis and final correctness;
  workers gather evidence and produce artifacts, they do not make architectural
  decisions. Dispatch read-only research in parallel; serialize write-heavy work
  per file set; give each worker a fully self-contained prompt. Full protocol:
  `.claude/patterns/08-multi-agent-coordination.md`.
- Threat model & security scope: `docs/THREAT_MODEL.md`.
- Agentic layer governance: `docs/agentic/AGENTIC_README.md`,
  `docs/agentic/SKILLS_REGISTRY_GOVERNANCE.md`.
