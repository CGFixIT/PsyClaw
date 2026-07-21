---
name: cyclaw-run-cyclaw
description: Prepare, index, start, and verify the local CyClaw FastAPI RAG server. Use in CGFixIT/CyClaw when the user asks to install runtime dependencies, build the retrieval index, start or interact with the gateway, or run broader local runtime verification.
---

# Run CyClaw

Use current commands from `AGENTS.md`, `docs/SETUP.md`, and active CI. Ask for
approval before network installs or long-running server processes when the
active environment requires it.

## Setup

1. Confirm Python 3.12 and inspect the existing environment before installing.
2. Install CPU torch before the remaining dependencies. Prefer the current
   `pyproject.toml`/uv path; use `requirements.txt` only for the documented
   compatibility path.
3. Ensure `data/personality/soul.md` exists. Never overwrite or invent soul
   content when an existing file is missing; report the blocker unless the user
   explicitly authorizes a local scaffold.
4. Set `GROK_API_KEY` to a non-secret dummy value for offline checks in the
   active shell. Do not enable Grok or Claude in `config.yaml`.

## Index And Start

Build the configured index when it is missing or the corpus changed:

```bash
python -m retrieval.indexer
```

Start the loopback gateway:

```bash
python -m uvicorn gate:app --host 127.0.0.1 --port 8787
```

The installed `cyclaw-index` and `cyclaw-server` entry points are equivalent.
Do not bind to `0.0.0.0` without an explicit deployment request and security
review.

## Verify

Use `$cyclaw-command-run` for focused endpoint checks. Common broader checks:

```bash
python -m tests.ci_rag_smoke
python -m pytest tests/ -q --tb=short
```

Use targeted agentic, sync, guardrails, Postgres, or connector tests only when
that optional integration is in scope. Ordinary core verification must not
require Ollama, Grok, Claude, rclone, Postgres, or live GitHub credentials.

Report setup performed, server lifecycle, endpoint/test results, unavailable
optional services, and generated files left uncommitted. Stop the server when
the requested verification is complete unless the user asked to leave it
running.
