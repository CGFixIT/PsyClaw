---
description: >-
  Verification specialist who tries to break the implementation. Job is not to confirm that it works — job is to try to break it.
---

Invoke the `verification-specialist` skill for the given task. $ARGUMENTS

See `.claude/skills/verification-specialist/SKILL.md` for full detail.

## Notes

- Two failure modes to avoid: check-skipping (finding reasons not to run checks) and unverified narration (claiming PASS without evidence).
- This complements `/verify` — use this skill when the task is specifically "break it," not just "confirm it runs."
