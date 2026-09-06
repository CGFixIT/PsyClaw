---
name: cyclaw-command-run
description: Smoke-test an existing local CyClaw checkout or running server without performing setup. Use in CGFixIT/CyClaw when the user asks for a quick runtime check of health, query, static UI, soul auth, audit, or ops endpoints; use cyclaw-run-cyclaw when setup, indexing, or server startup is required.
---

# CyClaw Smoke Run

Use this for focused verification. Do not install dependencies, rebuild the
index, or create a soul file; route those tasks to `$cyclaw-run-cyclaw`.

## Workflow

1. Read `AGENTS.md` and identify the requested endpoint or behavior.
2. Resolve configured soul/index paths and report missing assets when relevant.
   A missing index is an expected degraded/readiness case, not a reason to
   rebuild it or skip an unrelated endpoint check.
3. Start with the narrowest repo-native check:

```bash
python -m tests.ci_rag_smoke
python -m pytest tests/test_terminal_contract.py -q
```

4. If a server is already running on `127.0.0.1:8787`, probe only the relevant
   endpoints. `/health` is public; `/soul`, `/audit/summary`, and `/ops/*`
   normally require `Authorization: Bearer <CYCLAW_API_KEY>`. Read the
   peer/proxy/origin conditions if `security.api_key_optional` is enabled.
5. For `/query`, keep `user_confirmed_online` false unless external-provider
   testing is authorized. When auth is enabled, provide a test session/device
   credential; API-key authorization is a separate mechanism. Same-origin
   checks apply regardless of auth. Local/container model access still depends
   on `models.local_llm.trusted_hosts`.

Expected signals:

- `/health` may be degraded without Ollama, but index and graph readiness
  should match the requested test.
- with optional-key bypass disabled, unauthorized soul/audit/ops calls return `401`.
- the terminal UI and `/static/terminal.html` return `200`.
- prompt-injection probes are rejected.

Report exact checks, status codes or test results, unavailable services, and
residual risk. Keep the server on loopback and do not commit generated data.
