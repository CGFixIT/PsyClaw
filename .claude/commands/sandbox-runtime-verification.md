---
description: Verify the entire CyClaw main branch runs in a Python 3.12 runtime — dependency install, retrieval index build, unit + integration tests, an emulated RAG query, a Windows-style API smoke "bomb", and an independent gate.py runtime check.
---

Run a full Python 3.12 runtime verification of the CyClaw main branch. $ARGUMENTS

## Steps

1. Provision a clean Python 3.12 environment and install dependencies in the documented order (torch CPU wheel, then `requirements.txt` + `constraints.txt`).
2. Build the retrieval index and run the full unit + integration test suite.
3. Exercise an emulated RAG query, a Windows-style API smoke "bomb", and an independent `gate.py` runtime check.
4. Emit a pass/fail report; this is one-shot and read-only — it does not modify application code or the real `data/personality/soul.md`.

Follow `.claude/skills/sandbox-runtime-verification/SKILL.md` and its driver `.claude/skills/sandbox-runtime-verification/verify.sh` for the full six-stage process.

## Notes

- Invokes `/run-cyclaw` as its primary test driver.
- A fresh container has no Python deps pre-installed — this skill installs them; don't assume `pytest` works before that step.
- `status: degraded` in `/health` without LM Studio is expected, not a failure.
