---
description: Iterative logging coverage loop — reviews system logging, adds missing log statements until every important path produces useful tested logs, diagnoses low coverage or errors, makes a plan, then applies fixes until all tests exceed 85% pass rate (targeting 100%).
---

Run the logging refactor loop until every important path has tested, useful logs. $ARGUMENTS

## Steps

1. Determine the project name from the working directory or `CLAUDE.md`.
2. Audit every important code path for logging coverage; add missing log statements.
3. Write tests that assert logs are emitted correctly, then fix failures until pass rate exceeds 85% (targeting 100%).

Follow `.claude/skills/logging-refactor/SKILL.md` for the full loop mechanics.

## Notes

- No `print()` in library code — use `logging.getLogger("cyclaw.<module>")` with lazy `%s` formatting.
- The audit JSONL stream (`logs/audit.jsonl`) is separate from application logging and stores hashed queries only — never add raw query text to either stream.
