---
description: >-
  Adversarially probe CyClaw's prompt-injection sanitizer with a jailbreak/injection corpus, surface bypasses, and close each one with a minimal high-signal banned_patterns rule plus a regression test — while holding the false-positive budget so legitimate queries still pass. Use when asked to redteam the sanitizer, harden injection defense, or after any change to utils/sanitizer.py or config.yaml banned_patterns.
---

Invoke the `injection-redteam` skill for the given task. $ARGUMENTS

See `.claude/skills/injection-redteam/SKILL.md` for full detail.
