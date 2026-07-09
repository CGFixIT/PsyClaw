---
description: Detect and reconcile drift between CyClaw's code/config (the source of truth) and its documentation — CLAUDE.md, AGENTS.md, README, config comments, command docs, and skill tables.
---

Detect and reconcile doc drift against the current code/config state. $ARGUMENTS

## Steps

1. Run the deterministic checker: `python3 .claude/skills/doc-sync/doc_sync.py` — it validates the mechanical facts (skills list, entry points, config numbers, route table, pattern count, hook claims).
2. For flagged drift, fix the **doc**, not the code — code is the source of truth. If the doc describes desired behavior the code lacks, flag it as a code decision for the user rather than editing the doc to look consistent.
3. Do a manual pass over prose claims the checker can't verify mechanically.

Follow `.claude/skills/doc-sync/SKILL.md` for the full process.

## Notes

- Never change code behavior just to make a stale doc "true."
- Run after any architecture/config/skill change, before a release, or at end-of-session per `CLAUDE.md` §10.
- Every `##` doc section must stay self-contained — the corpus is chunked and searched section-by-section.
