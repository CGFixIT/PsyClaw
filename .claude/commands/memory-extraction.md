---
description: Extract durable memories from the recent conversation and persist them as a timestamped snapshot under docs/memories/.
---

Extract durable memories from this conversation and save them as a timestamped snapshot. $ARGUMENTS

## Steps

1. Get the exact target path from the orchestrator driver: `python3 .claude/skills/memory-orchestrator/orchestrate.py path`.
2. Examine the most recent messages in the conversation for durable facts, decisions (with rationale), and learnings worth keeping.
3. Persist them as a new timestamped snapshot file under `docs/memories/`.

Follow `.claude/skills/memory-extraction/SKILL.md` for the full extraction criteria.

## Notes

- This is the semantic half of the memory lifecycle; `memory-orchestrator` owns the mechanics (paths, timers, hooks).
- Do not write to `.claude/memory/` — that path is legacy.
