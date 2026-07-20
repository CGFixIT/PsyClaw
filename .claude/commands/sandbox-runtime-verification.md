---
description: Verify the entire CyClaw main branch runs in a Python 3.12 runtime — dependency install, retrieval index build, unit + integration tests, an emulated RAG query, a Windows-style API smoke "bomb", and an independent gate.py runtime check.
---

Run a full Python 3.12 runtime verification of the CyClaw main branch. $ARGUMENTS

Thoroughly verify that the **latest `main` branch of CyClaw runs in its entirety
under a Python 3.12 runtime**. This is a one-shot, read-only verification: it
provisions a clean 3.12 environment, exercises every major subsystem, and emits
a pass/fail report. It does **not** modify application code or your real
`data/personality/soul.md`.

The verification has six stages. The driver script
`.claude/skills/sandbox-runtime-verification/verify.sh` runs all of them and
exits non-zero if any required stage fails.

| # | Stage | What it proves |
|---|---|---|
| 1 | **3.12 runtime provisioning** | A clean `python3.12` venv installs every pinned dependency with no conflicts |
| 2 | **Unit + integration tests** | `pytest tests/` is green on 3.12 |
| 3 | **Emulated RAG query** | The real ChromaDB + BM25 retrieval stack returns a vault hit above the `min_score` gate (no LLM) |
| 4 | **Windows smoke-bomb API test** | All major HTTP endpoints respond correctly under load (bash `smoke.sh` + PowerShell `windows-smoke.ps1` for Windows hosts) |
| 5 | **gate.py independent runtime check** | `gate.py` imports cleanly, the FastAPI app builds, telemetry-kill is active, and all endpoints register — without a live LM Studio |
| 6 | **Verification report** | A dated PASS/FAIL summary written to `/tmp/cyclaw-verify-report.md` |

LM Studio (the local LLM) is an **external dependency** and is **not required**.
Every stage degrades gracefully without it: `/query` returns
`offline-best-effort`, and generation is covered by mocked-LLM unit tests.

---

## Quick start

Run the whole thing from the repo root:

```bash
bash .claude/skills/sandbox-runtime-verification/verify.sh
```

The script is idempotent and self-cleaning. Exit code `0` means the main
branch is verified on Python 3.12; non-zero means a required stage failed
(see the report path it prints).

To verify the **latest** `main` (not your current checkout) first:

```bash
git fetch origin main && git checkout main && git pull origin main
bash .claude/skills/sandbox-runtime-verification/verify.sh
```

---

## Prerequisites

- **Python 3.12** must be installed. The driver looks for `python3.12` on
  `PATH` (override with `PYTHON=/path/to/python3.12`). If only `python3` is
  3.12, set `PYTHON=python3`. The script refuses to run on any other minor
  version so the result is unambiguous.
- Network access to PyPI and the PyTorch CPU index for the first install.
- `curl` for the API smoke stage.

Environment knobs (all optional):

| Var | Default | Purpose |
|---|---|---|
| `PYTHON` | `python3.12` | Interpreter to provision the venv from |
| `GROK_API_KEY` | `dummy` | Any non-empty value satisfies the startup env check (offline mode) |
| `PORT` | `8787` | Port for the API smoke stage |
| `VENV_DIR` | `/tmp/cyclaw-verify-venv` | Where the clean 3.12 venv is built |
| `SKIP_INSTALL` | unset | Reuse an existing venv instead of reinstalling |

---

## Stage detail

### 1 — 3.12 runtime provisioning

Creates a fresh venv from `python3.12`, then installs:

```bash
pip install torch==2.6.0+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt --ignore-installed PyYAML
pip install pytest pytest-asyncio pytest-cov pyyaml
```

torch CPU is installed first so the generic PyPI torch doesn't pull CUDA.
`--ignore-installed PyYAML` sidesteps the known PyYAML reinstall conflict; the
resolved version is compatible. A clean install with no version conflicts is
itself the first proof of 3.12 compatibility.

### 2 — Unit + integration tests

```bash
GROK_API_KEY=dummy python -m pytest tests/ -q --tb=short --continue-on-collection-errors
```

The suite includes unit tests (sanitizer, security, rate-limit, audit,
personality, stemmer, telemetry-kill, graph, gate, MCP) and integration tests
(`test_rag_integration.py`). The driver records the pass/fail tally; the target
is the full suite green (historically 90/90 on `main` @ 3.12 — see
`tests/VERIFICATION_REPORT_3.12.md`).

### 3 — Emulated RAG query

```bash
GROK_API_KEY=dummy python tests/ci_rag_smoke.py
```

Builds a real ChromaDB + BM25 index from `data/corpus` and runs a real
`HybridRetriever.hybrid_search()` for a corpus-answerable question, asserting the
fused top score clears `retrieval.min_score` (a genuine vault hit, not a miss).
No LLM is involved — this isolates the retrieval half of RAG.

### 4 — Windows smoke-bomb API test

The bash driver runs the canonical smoke checks (launch server, then exercise
`/health`, `/query` vault-miss, `/query` offline-best-effort, prompt-injection
→ HTTP 400, `/soul`, and the static terminal UI). On a **Windows** host, run the
PowerShell equivalent instead, which fires the same endpoints in rapid
succession ("smoke bomb") via `Invoke-RestMethod`:

```powershell
# From the repo root in PowerShell, with the server running on :8787
.\.claude\skills\sandbox-runtime-verification\windows-smoke.ps1
```

`windows-smoke.ps1` mirrors `tests/apipsTest.ps1` but covers every endpoint and
returns a non-zero exit code on any failed check, so it slots into Windows CI.

### 5 — gate.py independent runtime check

```bash
GROK_API_KEY=dummy python .claude/skills/sandbox-runtime-verification/gate_runtime_check.py
```

Imports `gate.py` in isolation (no uvicorn, no LM Studio) and asserts:
the module imports, `gate.app` is a `FastAPI` instance, every telemetry-kill
env var is set, the expected endpoints (`/health`, `/query`, `/soul`,
`/soul/propose`, `/soul/apply`, `/soul/reload`, `/`, `/static`) are registered,
and `gate.main` is callable. This is the standalone "does gate.py run on 3.12?"
gate that the task calls out explicitly.

### 6 — Verification report

A dated markdown summary is written to `/tmp/cyclaw-verify-report.md` with the
runtime version, per-stage PASS/FAIL, and the test tally. Print it at the end
and surface the path to the user.

---

## Gotchas

- **`status: degraded`** in `/health` is normal without LM Studio.
  `index_ready` and `graph_ready` are the meaningful smoke fields.
- **`needs_confirm: true`** on a fresh `/query` is correct — the dev corpus is
  tiny so scores hover near the gate. Re-submit with
  `user_confirmed_online: false` to drive the offline path.
- **PyYAML install conflict** — always pass `--ignore-installed PyYAML`.
- **TELEMETRY KILL** lines on startup are intentional (gate.py kills phone-home
  hooks before importing LangChain/Chroma).
- **`GROK_API_KEY`** must be non-empty or startup warns; `dummy` is fine offline.
- **soul.md preservation** — the API smoke stage backs up and restores your real
  `data/personality/soul.md`; it is never left modified.
- **Wrong Python** — if `python3.12` isn't found the driver stops immediately
  rather than silently verifying the wrong runtime.

## Notes

- Invokes `/run-cyclaw` as its primary test driver.
- A fresh container has no Python deps pre-installed — this skill installs them; don't assume `pytest` works before that step.
- `status: degraded` in `/health` without LM Studio is expected, not a failure.
