---
description: Iterative test coverage and quality loop — adds tests under tests/ until coverage reaches 100%, diagnoses and fixes failing tests, and continues until all test results exceed 85% pass rate (targeting 100%).
---

Run the tests refactor loop until the suite is green and coverage is maximized. $ARGUMENTS

## Steps

1. Determine the project name from the working directory or `CLAUDE.md`.
2. Add tests under `tests/` iteratively until coverage climbs toward 100%.
3. Diagnose every failure and apply fixes until pass rate exceeds 85% (targeting 100%).

Follow `.claude/skills/tests-refactor/SKILL.md` for the full loop mechanics.

## Notes

- Use `tests/conftest.py` fixtures; new tests must start no live service and stay deterministic.
- Coverage gate is 80% `fail_under` in `pyproject.toml` — do not lower it to make the loop exit early.
