---
description: >-
  Statically validate CyClaw's config.yaml contract — the relational,
  value-safety, and threat-model invariants that boot-time validation and
  invariant-guard do not cover (graph_timeout > llm_timeout,
  chunk_overlap < chunk_size, the soul/context budget, loopback-only host,
  RRF-scale min_score, safe shipped posture). Use before merging any change
  to config.yaml, when asked to "check config" or "validate config", and as
  a cheap pre-boot gate in CI or a fresh clone.
---

Invoke the `config-guard` skill for the given task. $ARGUMENTS

See `.claude/skills/config-guard/SKILL.md` for full detail.

## Notes

- Add `--strict` to lock the shipped defaults (fails on any deviation from
  the documented safe posture, not just relational violations).
- Needs PyYAML; everything else is stdlib.
