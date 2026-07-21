# Dependency Currency — Bump Candidates Plan

**Status:** Planning only — no version bumps applied. Deferred from PR #596
(merged 2026-07-21), which added the `verify-deps` skill and ran its PyPI
currency + CVE sweep across every pinned package. This doc records that
sweep's findings for the packages with a real version gap and no known CVE,
so a future session can work through them deliberately instead of
re-researching from scratch.

**Re-derive this list before starting work** — PyPI moves continuously and
this data is a snapshot from 2026-07-21:

```bash
python3 .claude/skills/verify-deps/extract_pins.py   # current pins, all 4 install surfaces
# then, per package: WebFetch https://pypi.org/pypi/<package>/json -> info.version
```

Per `CLAUDE.md` §7 and `dep-guard`'s own Guardrails, **bumping a pinned
dependency is Medium-High risk** — this plan is a starting point for that
review, not a pre-approved change list. Nothing here should be bumped
without running the affected test suite afterward, and touching **all**
relevant pin files together (`pyproject.toml` + `constraints.txt` at minimum;
`requirements.txt` and `.github/workflows/environment.yml` too if the package
appears there — `verify-deps`'s `extract_pins.py` shows exactly which files
pin which package).

---

## Why these 12 and not the others

The full sweep covered all 26 pinned packages. Most either already matched
the latest release, or are a **documented, deliberate exception** that
should not be touched by this plan at all:

- `numpy` (1.26.4) — held below 2.x on purpose; 2.x removes `np.float_` and
  breaks chromadb/onnxruntime. `dep-guard` D2 enforces this. Not a candidate.
- `chromadb` (1.5.9) — already the latest release; the open CVE-2026-45829 is
  risk-accepted (embedded `PersistentClient` only, not the affected HTTP
  server mode) and there is no newer release that fixes it. Not a candidate.
- `pydantic`/`pydantic-core` lock-step — `pydantic` is already latest
  (2.13.4); `pydantic-core` has a newer release (2.47.0) but no `pydantic`
  release pairs with it yet per `constraints.txt`'s own comment. Bumping
  `pydantic-core` alone breaks the resolver (`dep-guard` D1). Not a candidate
  until a compatible `pydantic` release ships too — check `dep-guard`'s
  `_PYDANTIC_LOCKSTEP` constant against current PyPI when that happens.
- `torch` — pinned to a `+cpu` local-version build from the PyTorch CPU
  index, not generic PyPI; the sweep couldn't cleanly enumerate that index's
  versions the same way. Treat any torch bump as its own separate, careful
  pass (it touches the Dockerfile, CI torch-wheel cache key, and the
  documented CVE-2025-32434 minimum-safe-version rationale) — not folded into
  this plan.
- Everything else not listed below (`pyyaml`, `httpx`, `sentence-transformers`,
  `rank-bm25`, `nltk`, `pytest`, `pytest-asyncio`, `pytest-cov`, `bandit`,
  `deepagents`, `pydantic-settings`) was already at the latest release as of
  the sweep.

The 12 below have a real gap and no CVE was found affecting the currently
pinned version — the "safe to consider bumping" set, ranked from
lowest to highest blast radius.

---

## Tier 1 — dev-tool-only, zero runtime blast radius

These affect linting/type-checking only, never what ships or runs in
production. Still not risk-free: `dep-guard`'s own Gotchas note that a
`ruff`/`mypy` bump can silently change lint rules or type-check semantics
mid-CI, so each needs a real `ruff check`/`mypy` run afterward, not just a
version-number edit.

| Package | Pinned | Latest (2026-07-21) | Gap |
|---|---|---|---|
| `ruff` | 0.15.20 | 0.15.22 | patch |
| `mypy` | 2.1.0 | 2.3.0 | minor |

**Where pinned:** both in `pyproject.toml` (`[project.optional-dependencies].dev`
and `.full`), `constraints.txt`, and `.github/workflows/environment.yml`.

**Verification after bumping:** `ruff check --select E,F,I,B,C4,UP,S .` must
stay clean (or any new findings fixed in the same PR — don't silently widen
the diff by suppressing new rules). `mypy` is best-effort/not CI-enforced
per `CLAUDE.md` §4, so a `mypy` bump changing its output is lower-stakes, but
still worth a spot run against a few touched files.

---

## Tier 2 — runtime dependencies, patch/minor gap, straightforward

Real production dependencies, but the gap is small (patch or one minor
version) and nothing in the codebase does anything unusual with these
libraries that a routine bump would be likely to break.

| Package | Pinned | Latest (2026-07-21) | Gap |
|---|---|---|---|
| `langgraph` | 1.2.6 | 1.2.9 | patch |
| `langchain` | 1.3.11 | 1.3.14 | patch |
| `langchain-openai` | 1.3.3 | 1.3.5 | patch |
| `psycopg` | 3.2.13 | 3.3.4 | minor |
| `pgvector` | 0.4.2 | 0.5.0 | minor |

**Where pinned:** `langgraph` in all 4 surfaces; `langchain`/`langchain-openai`
in `pyproject.toml` (`agentic-deepagents` extra) + `constraints.txt` only
(not in the default install path — `agentic.deepagent_github` lazy-imports
them); `psycopg`/`pgvector` in `pyproject.toml` (`postgres`/`pgvector`
extras) + `constraints.txt` only. Note `psycopg-binary` is pinned separately
in `constraints.txt` (constraints files can't carry the `[binary]` extra) —
bump both together.

**Verification after bumping:**
- `langgraph`: `GROK_API_KEY=dummy pytest tests/test_graph.py tests/test_due_diligence_invariants.py -q`, then `python3 .claude/skills/invariant-guard/check_invariants.py` (I1-I4 depend on `graph.py`'s actual LangGraph wiring behaving the way the checker expects — a `langgraph` bump that changes edge/routing semantics would be exactly the kind of regression the checker exists to catch).
- `langchain`/`langchain-openai`: `pytest tests/test_agentic_harness_phase679.py tests/test_agentic_deepagent_optional.py -q` (the `deepagents-harness` CI job's own test target).
- `psycopg`/`pgvector`: `pytest tests/test_personality_postgres.py tests/test_ratelimit_postgres.py tests/test_pgvector_store.py -q` against a live Postgres/pgvector service (the `postgres-backend` CI job's own test target — needs the service container, not runnable standalone without one).

---

## Tier 3 — needs explicit compatibility check before bumping

| Package | Pinned | Latest (2026-07-21) | Gap |
|---|---|---|---|
| `fastapi` | 0.138.0 | 0.139.2 | minor |
| `uvicorn` | 0.49.0 | 0.51.0 | minor |
| `langchain-core` | 1.4.8 | 1.5.0 | minor |
| `websockets` | 15.0.1 | 16.1.1 | **major** |

- **`fastapi`/`uvicorn`** are the actual HTTP server stack `gate.py` runs on.
  A minor-version bump is usually safe for FastAPI/Starlette/Uvicorn, but
  check the FastAPI changelog for the 0.138→0.139 range for any breaking
  change before bumping, then run the full `tests/test_gate.py`,
  `tests/test_gate_ops.py`, and `tests/test_terminal_contract.py` suites —
  the terminal contract test in particular pins exact route/behavior
  expectations that a framework bump could silently shift.
- **`langchain-core`** — same reasoning as the langgraph/langchain bumps
  above, but note `pyproject.toml`'s comment for `chromadb`'s CVE mentions
  `langchain-core` is a base dependency (not an optional extra like
  `langchain`), so this one affects the *default* install, not just the
  agentic/deepagents opt-in path. Run the full test suite, not just the
  agentic-scoped subset.
- **`websockets`** is the one genuine outlier: `constraints.txt`'s own
  comment says it's pinned direct specifically because
  `langgraph-sdk imports websockets.asyncio at graph import time; keep this
  direct so legacy/no-deps paths cannot strand the app on websockets 12.x.`
  A **major** version bump (15.x → 16.x) is exactly the kind of change that
  comment is warning about — confirm the currently-pinned `langgraph`
  version (or whatever it's bumped to alongside this, per Tier 2 above)
  actually supports `websockets` 16.x before touching this pin. If in doubt,
  bump `langgraph` first, re-check, and treat `websockets` as its own
  follow-up rather than bundling it with the Tier 2/3 batch.

---

## Suggested execution shape for the follow-up PR(s)

1. **Tier 1** (`ruff`, `mypy`) — one small PR, `ruff check` clean, done.
2. **Tier 2** (`langgraph`, `langchain`, `langchain-openai`, `psycopg`,
   `pgvector`) — one PR, run each package's listed verification command,
   confirm `dep-guard` and `invariant-guard` still pass clean after.
3. **Tier 3** (`fastapi`, `uvicorn`, `langchain-core`) — one PR, full test
   suite, changelog-checked first.
4. **`websockets`** — only after confirming `langgraph`/`langgraph-sdk`
   compatibility with 16.x; likely folds into or follows the Tier 2 PR
   rather than standing alone.

For every PR in this sequence: update `pyproject.toml` AND `constraints.txt`
together (never one without the other — that's exactly what `dep-guard`'s D6
check exists to catch), update `environment.yml` if the package is pinned
there too, run `python3 .claude/skills/dep-guard/check_deps.py` and
`python3 .claude/skills/verify-deps/extract_pins.py` before committing, and
call out the specific version-to-version bump in the PR body per
`CLAUDE.md`'s Medium-High risk tier ("Proceed; expand tests; state the
rollback path in the PR body").
