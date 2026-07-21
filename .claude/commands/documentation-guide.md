---
description: >-
  Documentation agent that produces, revises, and organizes written documentation enabling the next reader to accomplish their goal on the first attempt.
---

Invoke the `documentation-guide` skill for the given task. $ARGUMENTS

See `.claude/skills/documentation-guide/SKILL.md` for full detail.

## Notes

- `config.yaml` owns the numbers — cite it, don't copy-and-drift a value into prose.
- Every `##` section must be self-contained (no pronoun references to earlier sections) since the corpus is chunked and searched section-by-section.
- Run `/doc-sync` after any doc change that could drift from code.
