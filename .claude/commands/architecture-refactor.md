---
description: Iterative architecture refactor loop — refactors code, live-tests after each significant step, runs autoreview, commits, and tracks progress in /tmp/refactor-{projectname}.md.
---

Run the architecture refactor loop until structure is clean and coherent. $ARGUMENTS

## Steps

1. Determine the project name from the working directory or `CLAUDE.md`.
2. Refactor iteratively: after each significant step, live-test the system, run autoreview, and commit.
3. Track all progress in `/tmp/refactor-{projectname}.md` so the loop is resumable.

Follow `.claude/skills/architecture-refactor/SKILL.md` for the full loop mechanics and exit criteria.

## Notes

- Never refactor across the six invariants (`CLAUDE.md` §3) without arguing the change explicitly and re-running `invariant-guard`.
- This is a loop skill — it keeps going until the codebase is clean, not a one-shot edit.
