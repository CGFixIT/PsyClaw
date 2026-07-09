---
description: Activate lazy-senior-dev mode — enforces YAGNI, stdlib-first, and minimal-abstraction constraints. Args: (none) full mode | checklist | review
---

Activate ponytail mode (lazy-senior-dev discipline). $ARGUMENTS

## Steps

1. Read `$ARGUMENTS` and dispatch:
   - *(none)* → Full Mode: apply all seven rules going forward for the rest of the session.
   - `checklist` → print the 7-item pre-commit checklist only, then stop.
   - `review` → apply the seven rules retroactively to review already-written code.
   - unrecognized argument → fall back to Full Mode.

Follow `.claude/skills/ponytail/SKILL.md` for the full seven-rule definition.

## Notes

- Core bias: YAGNI, stdlib-first, minimal abstraction — resist speculative generality and unnecessary dependencies.
- Still subordinate to `CLAUDE.md`'s Feature Freeze and the six invariants — "lazy" means minimal, not careless about security-relevant code.
