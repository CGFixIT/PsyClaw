---
description: >-
  Extract durable memories from the recent conversation and persist them as a timestamped snapshot under docs/memories/. Use when asked to remember something, save a memory, or capture learnings. Invoked by the memory-orchestrator on manual /memory, before context compaction, and on the 12h timer.
---

Invoke the `memory-extraction` skill for the given task. $ARGUMENTS

See `.claude/skills/memory-extraction/SKILL.md` for full detail.

## Notes

- This is the semantic half of the memory lifecycle; `memory-orchestrator` owns the mechanics (paths, timers, hooks).
- Do not write to `.claude/memory/` — that path is legacy.
