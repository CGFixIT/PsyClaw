---
description: >-
  Scan the codebase for lines or sections that lack comments and would confuse a newcomer, then add human-readable, ELI5-toned but technically accurate comments explaining WHY the code does what it does. Comment-only — never changes logic. Use when asked to improve code readability, add comments, explain confusing code, or make CyClaw friendlier for a newcomer to read.
---

Invoke the `add-comment` skill for the given task. $ARGUMENTS

See `.claude/skills/add-comment/SKILL.md` for full detail.

## Notes

- Comment-only — never changes logic, renames, or reformats. If the diff touches anything else, back it out.
- CyClaw is in FEATURE FREEZE — this exists because readability polish passes the bar even in freeze; new capabilities do not.
- Match the existing comment density and "why, with PR reference" style already used in the surrounding code — no TODO/FIXME comments (repo convention).
