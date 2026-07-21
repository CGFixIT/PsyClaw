---
description: >-
  Detect and reconcile drift between CyClaw's code/config (the source of truth) and its documentation — CLAUDE.md, AGENTS.md, README, config comments, command docs, and skill tables. Runs a deterministic checker for the mechanical facts (skills list, entry points, config numbers, route table, pattern count, hook claims) and drives a manual pass for prose claims. Use after any architecture/config/skill change, before a release, or when asked to reconcile the docs.
---

Invoke the `doc-sync` skill for the given task. $ARGUMENTS

See `.claude/skills/doc-sync/SKILL.md` for full detail.

## Notes

- Never change code behavior just to make a stale doc "true."
- Run after any architecture/config/skill change, before a release, or at end-of-session per `CLAUDE.md` §10.
- Every `##` doc section must stay self-contained — the corpus is chunked and searched section-by-section.
