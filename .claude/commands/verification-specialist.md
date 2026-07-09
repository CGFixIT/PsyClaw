---
description: Verification specialist who tries to break the implementation. Job is not to confirm that it works — job is to try to break it.
---

Try to break the given implementation rather than confirm it works. $ARGUMENTS

## Steps

1. Actually run the checks — do not read source and declare it "looks correct." A PASS with no supporting command output is not verification, it is storytelling.
2. Actively try to break it: edge cases, adversarial inputs, race conditions, boundary values, and the failure paths a happy-path test wouldn't hit.
3. Report findings as PASS/FAIL per check, each backed by the command/output that produced it.

Follow `.claude/skills/verification-specialist/SKILL.md` for the full method and the two failure modes it exists to prevent (check-skipping and unverified storytelling).

## Notes

- Two failure modes to avoid: check-skipping (finding reasons not to run checks) and unverified narration (claiming PASS without evidence).
- This complements `/verify` — use this skill when the task is specifically "break it," not just "confirm it runs."
