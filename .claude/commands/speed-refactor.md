---
description: Iterative speed optimization loop — continuously optimizes code for performance, measures page-load across every page under repeatable test conditions after each change, and continues until every page and module loads or runs in under 50 ms.
---

Run the speed refactor loop until every page and module runs under 50 ms. $ARGUMENTS

## Steps

1. Determine the project name from the working directory or `CLAUDE.md`.
2. Optimize iteratively: after each significant change, measure page-load/runtime performance across every page under identical, repeatable conditions.
3. Continue until every page and code module independently runs or loads in under 50 ms.

Follow `.claude/skills/speed-refactor/SKILL.md` for the full loop mechanics and measurement methodology.

## Notes

- Measurements must be repeatable — no cherry-picked runs.
- This is a loop skill; it keeps going until the 50 ms bar is met everywhere, not a single pass.
