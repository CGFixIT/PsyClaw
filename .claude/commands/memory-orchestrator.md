---
description: >-
  Orchestrate CyClaw's memory lifecycle — extract durable memories, consolidate them, and persist every pass to docs/memories/<date_time>.md. Use when asked to remember something, save/extract memory, run memory consolidation, or when memory should be captured before context is compacted or a session is archived/deleted. Wraps the memory-extraction and memory-consolidation skills.
---

Invoke the `memory-orchestrator` skill for the given task. $ARGUMENTS

See `.claude/skills/memory-orchestrator/SKILL.md` for full detail.

## Notes

- Live memory lives ONLY in `docs/memories/`; `.claude/memory/` is legacy — never add there.
- Invoked automatically before context compaction, on session end, and on the 12h timer — manual invocation is for on-demand snapshots.
