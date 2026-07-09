---
description: Clone origin/main to a clean local sandbox, install all dependencies, spin up a mock LM Studio, then run a comprehensive audit and produce a dated report plus a draft PR against main.
---

Run the full CyClaw sandbox audit: clone, install, mock LM Studio, audit every subsystem, report. $ARGUMENTS

## Steps

1. Clone `origin/main` into a clean sandbox and install dependencies (torch CPU wheel first, then `requirements.txt` with `constraints.txt`).
2. Spin up a mock LM Studio (QWEN-7B-Instruct cached offline, Grok=No, Claude=No).
3. Run the full audit: config validation, `gate.py`/`graph.py` standalone checks, full unit+integration tests, `terminal.html` endpoint emulation (including the "describe CyClaw in one sentence" vault-hit probe), `metrics.py` output, and a per-subsystem review (`utils/`, `tests/`, `sync/`, `agentic/`, `.claude/`, `.github/`).
4. Write a dated `Local_Sandbox_Complete_Audit` report under `docs/` and open a draft PR against `main`.

Follow `.claude/skills/CyClaw-Sandbox/SKILL.md` for the full process.

## Notes

- Read-only against the real repo state — operates on a clone, not the working tree.
- `status: degraded` without live LM Studio is expected and normal.
- Draft PR only; a human decides when to merge.
