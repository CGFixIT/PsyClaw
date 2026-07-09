---
description: Methodically scan the CyClaw main branch for code, CI, security, financial-risk, and maintainability optimization opportunities, then open focused, reviewable pull requests for each.
---

Run a full optimization sweep of CyClaw main and open one draft PR per finding. $ARGUMENTS

## Steps

1. Scan `main` for optimization opportunities across code quality, CI hygiene, security posture, financial/oversight risk, and maintainability — reading with the CyClaw architecture in mind (`gate.py`, `graph.py`, hybrid retrieval, triple-gated fallback, `agentic/`/`sync/` isolation).
2. For each distinct opportunity, cut a short-lived `claude/<topic>` branch, make the smallest reviewable change, and open a **draft** PR with What/Why/Risk to monitor.
3. Never bundle unrelated concerns into one PR — one concern per PR, per repo convention.

Follow `.claude/skills/CyClaw-Optimize/SKILL.md` for the full process, persona, and PR checklist.

## Notes

- Feature freeze applies (`CLAUDE.md` §1) — the operative test is "does this polish the portfolio signal or fix a real defect?" New capabilities need explicit user justification first.
- Never push directly to `main`; never force-push without sign-off.
- Re-run `python3 .claude/skills/invariant-guard/check_invariants.py` before opening any PR that touches core files.
