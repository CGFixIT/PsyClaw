---
description: Adversarially probe CyClaw's prompt-injection sanitizer with a jailbreak/injection corpus, surface bypasses, and close each one with a minimal high-signal banned_patterns rule plus a regression test — while holding the false-positive budget.
---

Redteam the sanitizer against a jailbreak/injection corpus and close any bypasses found. $ARGUMENTS

## Steps

1. Run the adversarial probe corpus against `utils/sanitizer.py` / `config.yaml`'s `policy.prompt_filter.banned_patterns` via `python3 .claude/skills/injection-redteam/redteam.py`.
2. For each confirmed bypass, add the minimal high-signal pattern needed to close it, plus a regression test.
3. Re-run the full probe corpus to confirm the false-positive budget still holds — legitimate queries must keep passing.

Follow `.claude/skills/injection-redteam/SKILL.md` for the full loop and pattern-authoring guidance.

## Notes

- **High risk tier:** editing `banned_patterns` requires stopping to ask first per `CLAUDE.md` §7 — this skill proposes patterns, it does not get to unilaterally ship them.
- Deleting a documented banned-pattern phrase fails `TestShippedConfigContract`; only additions are safe without review.
- The sanitizer `lru_cache`s by config path — restart the process to pick up new patterns.
