---
name: cyclaw-run-cyclaw
description: Prepare, index, start, and verify the local CyClaw FastAPI RAG server. Use in CGFixIT/CyClaw when the user asks to install runtime dependencies, build the retrieval index, start or interact with the gateway, or run broader local runtime verification.
---

# Run CyClaw

Use current commands from `AGENTS.md`, `setup-guide.md`, and active CI. Ask for
approval before network installs or long-running server processes when the
active environment requires it.

## Setup

1. Confirm Python 3.12 and inspect the existing environment before installing.
2. Install CPU torch before the remaining dependencies. Prefer the current
   `pyproject.toml`/uv path; use `requirements.txt` only for the documented
   compatibility path.
3. Check `data/personality/soul.md` for governance/identity drift. CyClaw will
   default-initialize the documented file at startup if it is absent; never
   overwrite it or invent custom soul content without an explicit human reason.
4. Use dummy credentials only for isolated mock tests; do not overwrite the
   operator's environment for a requested live run. Hybrid and both providers
   already ship enabled. Routine verification uses mocked provider HTTP and
   `user_confirmed_online=false`, preserving shipped configuration.

## Index And Start

Build the configured index when it is missing or the corpus changed:

```bash
python -m retrieval.indexer
```

Start the loopback gateway:

```bash
python gate.py
```

`cyclaw-index` invokes the indexer; `cyclaw-server` invokes `gate.main`.
Direct `uvicorn gate:app` imports the ASGI application but skips `main()`'s
startup/bind checks, so do not call it equivalent to the canonical launcher.
For container-host models, follow `docs/DOCKER.md` and explicit `trusted_hosts`;
never silently loosen destination checks to make a smoke test pass.
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
