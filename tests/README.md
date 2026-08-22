# `tests/` — the CyClaw test suite

Pytest suite for the whole repository (183 `test_*.py` files, auto-collected
via `testpaths = ["tests"]` in `pyproject.toml`). Everything external is mocked
in `conftest.py` — no live Ollama, no network, no real ChromaDB service. A
fresh clone has **no** Python deps installed; install first (see `CLAUDE.md`
§4 "Environment & install") before running anything here.

## Running

```bash
GROK_API_KEY=dummy pytest tests/ -q --tb=short          # full suite
GROK_API_KEY=dummy pytest tests/test_graph.py -q        # one file
GROK_API_KEY=dummy python tests/ci_rag_smoke.py         # real-index RAG smoke
```

`GROK_API_KEY` must be any non-empty value — the config layer treats an empty
env var as "key unset" and several construction paths assert on it. No real
key is contacted.

Python 3.12 is required (`requires-python >=3.12,<3.13`). On sandboxes where
bare `python3` is 3.11, the suite fails with ~142 misleading errors from a
3.12-only stdlib parameter — build a 3.12 venv and invoke pytest through it
(full recipe in `CLAUDE.md` §4 "Environment & install").

The optional groundedness evaluator is a separate live-spend command and is not
part of required CI:

```bash
CYCLAW_EVAL_LIVE=1 python tests/judge_eval.py
```

It also requires `ANTHROPIC_API_KEY` and a running loopback local LLM. Missing
either gate returns exit 2. It uses only the tracked synthetic corpus under
`tests/fixtures/groundedness/`; reports and its isolated index stay ignored
under `logs/evals/`. Exit 0 means the approved rubric passed, exit 1 means a
complete run missed a threshold, and exit 2 means the run was refused or could
not complete.

## Coverage

Bare `pytest` runs **no** coverage — the 80% gate (`fail_under` in
`pyproject.toml` `[tool.coverage.report]`) applies only when CI's explicit
`--cov=` flags are passed. A new **source module** needs a `--cov=` flag in
`.github/workflows/ci.yml` AND an entry in `[tool.coverage.run] source`; new
test files are auto-discovered and need neither.

## Layout and special files

| Path | What it is |
|---|---|
| `conftest.py` | Shared fixtures; mocks every external dependency. `test_config` is a **deepcopy** on purpose — a shallow copy leaks mutations across tests (`test_conftest_fixtures` guards this). |
| `fixtures/github_coding_repo/` | Canned repo used by the agentic real-repo-loop tests. |
| `test_harness*.py` | Out-of-band coding console: `/goal`, `/loop`, `/skills`, `/tools`, `/memory`, allowlist-only `/web`, auth, HTML contract, I6. |
| `ci_rag_smoke.py` | Deliberately NOT `test_*`-named so pytest ignores it; runs as a separate CI step against a real index. Renaming it double-runs it and drags ChromaDB into the unit lane. |
| `judge_eval.py` | Default-off 24-case groundedness evaluator. Builds an isolated real Chroma/BM25 index from tracked synthetic fixtures and sends public-safe evaluation data to Claude only after both live gates pass. |
| `TEST_SUITE_AUDIT.md`, `VERIFICATION_REPORT_3.12.md` | Point-in-time audit reports, kept beside the suite they audited. |
| `apipsTest.ps1`, `cmd2index.bat` | Windows-side manual helpers; not collected by pytest. |

## Conventions that bite

- Never `import gate` at a test module's top level — it triggers full app init
  (FastAPI + ChromaDB + retriever). Use a subprocess (`test_telemetry_kill`)
  or module-level patching (`test_gate`).
- The conftest mock retriever's `min_score` (0.75) is intentionally different
  from production (0.028, RRF scale) — both are load-bearing; do not unify.
- `MockGrokClient` defaults `available=True`; pass `available=False` to
  simulate a missing API key.
- POSIX-only modules (`pty`, `termios`) must be imported behind an
  `os.name != "nt"` guard — a top-level import aborts collection on Windows
  before any `skipif` marker can run.
- Tests must be deterministic: no live services, no network, no real `sleep`
  racing a timeout.

## Related

- Commands and environment setup: `CLAUDE.md` §8
- Invariant regression harness: `tests/test_due_diligence_invariants.py` and
  repo-root `INVARIANTS.md`
