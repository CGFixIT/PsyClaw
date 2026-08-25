---
name: Bug report
about: Something in CyClaw behaves incorrectly
title: ''
labels: bug
assignees: ''

---

<!--
SECURITY: do not use this template for a vulnerability. A GitHub issue is
public the moment you file it. Report privately instead — see SECURITY.md.

Never paste API keys, tokens, soul.md contents, or raw query text. The audit
log stores SHA-256 query hashes precisely so that raw text never has to leave
your machine; keep it that way in bug reports too.
-->

**What happened**
A clear description of the incorrect behavior, and what you expected instead.

**Reproduction**
Steps, with the exact command or request you ran:

```
# e.g. python gate.py, or:
# curl -s -X POST http://127.0.0.1:8787/query -H 'Content-Type: application/json' -d '{"query":"..."}'
```

**Which surface**
<!-- Delete the ones that don't apply. -->
- [ ] Gateway `/query` or another HTTP route (`gate.py`, port 8787)
- [ ] Soul Console / terminal UI (`static/terminal.html`)
- [ ] Harness console (`harness/`, port 8790)
- [ ] MCP server (`mcp_hybrid_server.py`)
- [ ] Retrieval / indexing (`retrieval/`, `python -m retrieval.indexer`)
- [ ] Out-of-band subsystem (`agentic/`, `sync/`, `telegram/`, `opentweet/`)
- [ ] Install / packaging / CI

**Diagnostics**

```
# Health (redact anything host-specific you'd rather not share):
# curl -s http://127.0.0.1:8787/health
```

- `status: degraded` with no Ollama running is NORMAL — say whether Ollama is up.
- Index built? (`python -m retrieval.indexer` run at least once?) A missing index
  is fail-soft and returns 503 `INDEX_NOT_FOUND` by design.
- Relevant `logs/audit.jsonl` event names (event names only — never raw query text).

**Environment**
- OS + version:
- Python version (`python --version`) — the project requires >=3.12,<3.13:
- CyClaw version or commit SHA:
- Install path used: `pip install -e .` / requirements.txt / conda / Docker
- Local model + Ollama version, if the LLM path is involved:

**Config**
Anything changed from the shipped `config.yaml` defaults? (Do not paste secrets —
naming the keys you changed is enough.)

**Additional context**
Anything else that helps — a hypothesis, when it started, whether it's intermittent.
