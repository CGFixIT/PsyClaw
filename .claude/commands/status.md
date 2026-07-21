---
description: Full environment and server status check — validates prerequisites, config, index, soul file, and live server health in one pass.
---

Run a full CyClaw environment status check.

## Steps

### 1. Python Environment
```bash
python3 --version          # Must be 3.12.x
pip show fastapi uvicorn chromadb langchain-core | grep -E 'Name|Version'
```
Flag if Python is not 3.12 or any core package is missing.

### 2. Required Files
Check each path exists:
```bash
for f in data/personality/soul.md index/chroma_db index/bm25.json config.yaml requirements.txt; do
  test -e $f && echo "OK: $f" || echo "MISSING: $f"
done
```

### 3. Configuration
```bash
python3 -c "
import yaml
cfg = yaml.safe_load(open('config.yaml'))
print('mode:', cfg['app']['mode'])
print('host:', cfg['api']['host'])
print('port:', cfg['api']['port'])
print('top_k:', cfg['retrieval']['top_k'])
print('min_score:', cfg['retrieval']['min_score'])
print('grok_enabled:', cfg['models']['grok'].get('enabled', False))
"
```
Flag if `host` is not `127.0.0.1` (loopback requirement) or if `grok_enabled=true` unexpectedly.

### 4. Environment Variables
```bash
echo "GROK_API_KEY: $([ -n "$GROK_API_KEY" ] && echo SET || echo MISSING)"
echo "CYCLAW_MODE: ${CYCLAW_MODE:-not set (config.yaml value used)}"
```

### 5. Server Health (if running)
```bash
curl -s --connect-timeout 2 http://127.0.0.1:8787/health | python3 -m json.tool 2>/dev/null \
  || echo "Server not running"
```
If running, report: `status`, `index_ready`, `graph_ready`, `mode`.

### 6. Telemetry Kill Verification
```bash
python3 -c "
import os
kill_vars = ['LANGCHAIN_TRACING_V2','LANGCHAIN_API_KEY','CHROMA_TELEMETRY','ANONYMIZED_TELEMETRY','OTEL_EXPORTER_OTLP_ENDPOINT']
for v in kill_vars:
    print(f'{v}: {os.environ.get(v, "not set")}')
"
```

## Output Format

```
=== CyClaw Environment Status ===
Python:        3.12.x  ✅
Soul file:     EXISTS  ✅
ChromaDB:      EXISTS  ✅
BM25 index:    EXISTS  ✅
Config mode:   offline ✅
Server:        RUNNING / NOT RUNNING
Health status: healthy / degraded (normal without a live Ollama daemon)
```

List any ❌ failures with remediation steps.
