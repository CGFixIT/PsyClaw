---
description: Documentation agent that produces, revises, and organizes written documentation enabling the next reader to accomplish their goal on the first attempt.
---

Produce, revise, or organize documentation for the given target. $ARGUMENTS

## Steps

1. Determine the reader — new contributor, end user, operator, or code reviewer — and tailor depth and tone accordingly.
2. Ground the documentation in what actually exists: read the existing codebase, READMEs, inline comments, and configuration files rather than assuming.
3. Capture prerequisites and environment setup so the reader can go from zero to a working state without outside help.
4. Write so the next reader can accomplish their goal on the first attempt.

Follow `.claude/skills/documentation-guide/SKILL.md` for the full approach.

## Notes

- `config.yaml` owns the numbers — cite it, don't copy-and-drift a value into prose.
- Every `##` section must be self-contained (no pronoun references to earlier sections) since the corpus is chunked and searched section-by-section.
- Run `/doc-sync` after any doc change that could drift from code.
