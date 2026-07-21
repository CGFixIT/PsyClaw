# Regression Risk Checklist

- Does this touch `gate.py`, `graph.py`, `retrieval/`, `utils/personality.py`, `utils/logger.py`, or `mcp_hybrid_server.py`?
- Could it weaken RAG-first retrieval, topology-as-policy routing, triple-gated external fallback, audit convergence, or soul governance?
- Could it change default network exposure, telemetry behavior, API auth, or secret handling?
- Could it require Ollama, rclone, `gh`, Postgres, or internet access in paths that were previously offline/local?
- Could dependency changes drift across `pyproject.toml`, `requirements.txt`, `constraints.txt`, Docker, or CI?
- Could tests accidentally mutate committed `data/personality/soul.md` or depend on private corpus data?
- Is the verification scope proportional to the blast radius?
