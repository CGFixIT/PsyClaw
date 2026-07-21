---
name: cyclaw-command-run
description: Smoke-test an existing local CyClaw checkout or running server without performing setup. Use in CGFixIT/CyClaw when the user asks for a quick runtime check of health, query, static UI, soul auth, audit, or ops endpoints; use cyclaw-run-cyclaw when setup, indexing, or server startup is required.
---

# CyClaw Smoke Run

Use this for focused verification. Do not install dependencies, rebuild the
index, or create a soul file; route those tasks to `$cyclaw-run-cyclaw`.

## Workflow

1. Read `AGENTS.md` and identify the requested endpoint or behavior.
2. Confirm `data/personality/soul.md`, `index/chroma_db`, and
   `index/bm25.json` exist when the check needs the gateway.
3. Start with the narrowest repo-native check:

```bash
python -m tests.ci_rag_smoke
python -m pytest tests/test_terminal_contract.py -q
```

4. If a server is already running on `127.0.0.1:8787`, probe only the relevant
   endpoints. `/health` is public; `/soul`, `/audit/summary`, and `/ops/*`
   require `Authorization: Bearer <CYCLAW_API_KEY>`.
5. For `/query`, keep `user_confirmed_online` false unless the user explicitly
   asks to exercise an enabled external provider.

Expected signals:

- `/health` may be degraded without Ollama, but index and graph readiness
  should match the requested test.
- unauthorized soul, audit, and ops requests fail closed with `401`.
- the terminal UI and `/static/terminal.html` return `200`.
- prompt-injection probes are rejected.

Report exact checks, status codes or test results, unavailable services, and
residual risk. Keep the server on loopback and do not commit generated data.
