---
name: cyclaw-command-audit
description: Analyze CyClaw audit evidence without exposing private query content. Use in CGFixIT/CyClaw when the user asks to summarize logs/audit.jsonl, run cyclaw-metrics or metrics.py, inspect retrieval or model usage, or identify malformed and privacy-risk audit records.
---

# CyClaw Audit Logs

Use the repository's metrics code. It reads the audit file configured at
`logging.audit_file`; it does not read JSONL from standard input.

## Workflow

1. Read `AGENTS.md` and inspect `metrics.py` plus `utils/logger.py` when the
   requested claim depends on audit semantics.
2. Resolve `logging.audit_file` from `config.yaml` and confirm the file exists.
3. Run the full configured summary:

```bash
python -m metrics
```

The installed `cyclaw-metrics` entry point is equivalent.

4. For a date prefix, aggregate in memory without printing raw events:

```bash
python -c "from metrics import compute_metrics, iter_events; print(compute_metrics(e for e in iter_events('logs/audit.jsonl') if e.get('timestamp', '').startswith('2026-06-20')))"
```

Adjust the path to match `config.yaml`. Treat the date as data controlled by
the operator; do not interpolate untrusted shell input.

## Report

Include total and RAG-query counts, event breakdown, score min/mean/max,
retrieval modes, model usage, online escalations, and audit-integrity counters.
Flag malformed records, plaintext queries, unexpected external-model activity,
and material error or refusal spikes.

Do not paste raw queries, corpus content, API keys, or complete audit records.
Never commit files under `logs/`.
