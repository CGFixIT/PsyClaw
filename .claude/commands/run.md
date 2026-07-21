---
description: Run the CyClaw smoke test suite against your current checkout. Starts the server if needed, executes all 29 smoke checks, and reports pass/fail with endpoint details.
---

Run the CyClaw smoke test against the local server. $ARGUMENTS

This is **Quick Mode** of the `CyClaw-Sandbox` skill — a fast check against
your current checkout, no clone/report/PR. For the full clone-first audit
(config validation, 5-query swarm test, triple-gate, invariants, terminal
REST surface, 3.12 runtime gate, dated report + draft PR), use
`/CyClaw-Sandbox` instead.

## Steps

1. Verify prerequisites exist: `index/chroma_db/`, `index/bm25.json`,
   `data/personality/soul.md`. If any are missing, stop and report what
   needs to be built first (`python -m retrieval.indexer`).
2. Execute the smoke script:
   ```bash
   bash .claude/skills/CyClaw-Sandbox/smoke.sh
   ```
3. Report results for all 29 checks (core API, `agentic/fsconnect`,
   `agentic/sqlconnect`, NeMo guardrails, opt-in PostgreSQL backends, full
   pytest suite) — see `.claude/skills/CyClaw-Sandbox/SKILL.md` §6b for the
   per-check breakdown.
4. Exit summary:
   - ✅ All checks passed — server is healthy
   - ❌ N checks failed — list each failure with the actual vs expected response

## Notes

- `status: degraded` in `/health` is **normal** without a live Ollama
  daemon running. `index_ready` and `graph_ready` are the meaningful fields.
- `needs_confirm: true` on `/query` is **correct** behavior when retrieval
  score is below `min_score` (0.028).
- `TELEMETRY KILL` messages on stdout are intentional.
- `GROK_API_KEY=dummy` is sufficient for all offline smoke checks.
- `soul.md` is backed up and restored by the smoke script — your personality
  file is never modified.
