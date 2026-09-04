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
   `metrics.py` itself takes no arguments and never reads stdin -- piping a `grep` into it
   is a silent no-op, and counting matching lines with `grep -c` only gives a count, not the
   node/score/injection/error breakdown step 4 asks for. Filter the parsed events with
   `iter_events()` and aggregate the result with `compute_metrics()` (both in `metrics.py`) --
   timestamps are ISO 8601 (`utils/logger.py`), so a string-prefix match on the date is exact:
   ```bash
   python3 -c "
   from metrics import iter_events, compute_metrics
   import json
   events = [e for e in iter_events('logs/audit.jsonl') if e.get('timestamp', '').startswith('2026-06-20')]
   print(json.dumps(compute_metrics(events), indent=2))
   "
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
