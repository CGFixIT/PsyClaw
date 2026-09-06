---
name: cyclaw-command-status
description: Run a read-only CyClaw environment and readiness check. Use in CGFixIT/CyClaw when the user asks for Python, dependency, configuration, index, soul, telemetry-kill, optional-service, or live health status without changing local state.
---

# CyClaw Status

Do not install, start, rebuild, or mutate anything during this workflow.

## Workflow

1. Read `AGENTS.md` and current `config.yaml`.
2. Check Python and core imports:

```bash
python --version
python -c "from utils.telemetry_kill import apply_telemetry_kill; apply_telemetry_kill(); import fastapi, uvicorn, chromadb, langchain_core; print('core imports OK')"
```

3. Check the configured runtime paths. Current defaults are
   `data/personality/soul.md`, `index/chroma_db`, and `index/bm25.json`, but
   configuration is authoritative.
4. Parse and report current safe-state values:

```bash
python -c "import yaml; c=yaml.safe_load(open('config.yaml', encoding='utf-8')); print({'mode': c['app']['mode'], 'host': c['api']['host'], 'port': c['api']['port'], 'top_k_semantic': c['retrieval']['top_k_semantic'], 'top_k_keyword': c['retrieval']['top_k_keyword'], 'min_score': c['retrieval']['min_score'], 'grok_enabled': c['models']['grok'].get('enabled', False), 'claude_enabled': c['models']['claude'].get('enabled', False), 'auth_enabled': c.get('auth', {}).get('enabled', False), 'memory_enabled': c.get('memory', {}).get('enabled', False), 'agentic_enabled': c.get('agentic', {}).get('enabled', False), 'telegram_enabled': c.get('telegram', {}).get('enabled', False)})"
```

5. Report only whether secret-bearing environment variables are set; never
   print their values.
6. If the gateway is already running, probe `http://127.0.0.1:8787/health` with
   a two-second connection timeout.
7. Verify the telemetry contract from `gate.py` or run the focused test when
   dependencies are available:

```bash
python -m pytest tests/test_telemetry_kill.py -q
```

Flag a non-3.12 Python, failed imports, missing configured runtime paths,
a bind/endpoint inconsistent with the operator's configured deployment, failed
telemetry checks, or health readiness inconsistent with the local environment.
Hybrid and both providers enabled are shipped defaults, not drift by themselves;
confirmation still gates external answers. Inspect `trusted_hosts`,
`security.api_key_optional`, and auth separately before evaluating trust.

Report checks that were skipped because the server or optional dependencies
were unavailable. Do not treat a stopped Ollama, Postgres, rclone, or GitHub
client as a core failure unless that integration was requested.
