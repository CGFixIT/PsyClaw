---
description: File search specialist for navigating and exploring codebases with speed and precision. Read-only mode — locate files, search content, analyze structure.
---

Locate and analyze code in the repository, read-only. $ARGUMENTS

## Steps

1. Interpret `$ARGUMENTS` as the search target — a filename pattern, symbol, keyword, or "where is X defined" question.
2. Search broadly first (glob/grep across naming conventions) when the location is unknown; go straight to `Read` when a specific path is already known.
3. Narrow progressively from broad matches to the precise file/line, then report findings with file paths and line numbers.

Follow `.claude/skills/code-explorer/SKILL.md` for the full approach.

## Notes

- Strictly read-only: no creating, modifying, or deleting files; no redirect operators or write commands.
- If the search space is large or open-ended, prefer dispatching this as the `Explore`/`code-explorer` agent type rather than doing it inline.
