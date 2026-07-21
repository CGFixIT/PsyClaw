---
description: >-
  Run a semantic "dream" consolidation over docs/memories/ — merge paraphrases, resolve contradictions, prune stale entries, and rewrite the CONSOLIDATED.md working set. Use when asked to consolidate, deduplicate, or clean up memory. Invoked by the memory-orchestrator before context compaction, on session end, and on the 12h timer.
---

Invoke the `memory-consolidation` skill for the given task. $ARGUMENTS

See `.claude/skills/memory-consolidation/SKILL.md` for full detail.

## Notes

- This goes beyond the orchestrator driver's structural dedupe — the reasoning pass is the point of this skill.
- Invoked by `memory-orchestrator` before context compaction, on session end, and on the 12h timer.
