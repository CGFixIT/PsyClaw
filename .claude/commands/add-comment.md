---
description: Scan the codebase for lines or sections that lack comments and would confuse a newcomer, then add human-readable, ELI5-toned but technically accurate comments explaining WHY the code does what it does. Comment-only — never changes logic.
---

Add newcomer-friendly, ELI5-toned comments explaining WHY the code does what it does. $ARGUMENTS

## Steps

1. Scan the target file(s) (or `$ARGUMENTS` if a path is given) for lines/sections that would make a newcomer stop and ask "wait, why does this do that?"
2. For each, add a short comment above it in plain English — warm, accurate, never condescending — explaining the *why*, not restating the *what*.
3. Leave already-clear code alone; do not touch logic, names, or formatting.
4. Confirm the diff is comment-only (`git diff` shows no non-comment line changes).

## Notes

- Comment-only — never changes logic, renames, or reformats. If the diff touches anything else, back it out.
- CyClaw is in FEATURE FREEZE — this skill exists because readability polish passes the bar even in freeze; new capabilities do not.
- Match the existing comment density and "why, with PR reference" style already used in the surrounding code — no TODO/FIXME comments (repo convention).
