---
description: Run, start, build, smoke-test, or interact with the CyClaw FastAPI RAG server.
---

Start the CyClaw server (if needed) and run the smoke-test suite against it. $ARGUMENTS

## Steps

1. Run the driver: `.claude/skills/run-cyclaw/smoke.sh` — it starts `gate.py` if not already running (binds `127.0.0.1:8787`) and exercises the RAG pipeline end to end.
2. Confirm `/health` responds; `status: degraded` without a live LM Studio is normal, not a failure.
3. Report pass/fail per smoke check, with endpoint details for any failures.

Follow `.claude/skills/run-cyclaw/SKILL.md` for the full smoke-test breakdown.

## Notes

- LM Studio is an external dependency; without it `/query` degrades gracefully to `offline-best-effort` rather than failing.
- Also invoked by `/sandbox-runtime-verification` as its primary test driver.
- A fresh container has no Python deps installed — install first (see `CLAUDE.md` §8) before running this.
