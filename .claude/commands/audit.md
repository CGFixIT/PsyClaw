---
description: Run metrics.py against audit.jsonl to produce a session summary — query counts, node distribution, score stats, and any anomalies.
---

Run the CyClaw audit log analyzer. $ARGUMENTS

## Steps

1. Check the audit log exists:
   ```bash
   test -f logs/audit.jsonl && echo "EXISTS" || echo "MISSING"
   ```
   If missing: report that no audit log exists yet — start the server and make at least one query to generate it.

2. Run the metrics analyzer:
   ```bash
   GROK_API_KEY=dummy python3 -m metrics
   # or via entry point:
   cyclaw-metrics
   ```

3. If `$ARGUMENTS` contains a date or time filter (e.g. "today", "last hour", "2026-06-20"),
   filter AFTER the run, not before it. `metrics.py` takes no arguments and never reads
   stdin -- `print_metrics()` resolves the log itself from `cfg["logging"]["audit_file"]` --
   so piping into it is a silent no-op that re-reads the whole file. Scope the window by
   grepping the raw log directly:
   ```bash
   grep -c '"2026-06-20"' logs/audit.jsonl
   ```

4. Report the following from the output:
   - Total queries processed
   - Graph node distribution (which paths were exercised)
   - Retrieval score statistics (min, max, mean)
   - Injection attempts blocked
   - Any anomalies or error entries

5. Flag if:
   - Any raw query text appears in the log (PII/privacy violation — should be SHA-256 hashed)
   - Error rate exceeds 10%
   - Any entries with `grok_fallback` node were triggered (means hybrid mode was active)

## Notes

- Audit log is append-only JSONL at `logs/audit.jsonl`
- Query text is SHA-256 hashed — raw queries are never stored by design
- `GROK_API_KEY` must be set even for offline metrics runs
