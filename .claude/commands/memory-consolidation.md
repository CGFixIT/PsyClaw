---
description: Run a semantic "dream" consolidation over docs/memories/ — merge paraphrases, resolve contradictions, prune stale entries, and rewrite the CONSOLIDATED.md working set.
---

Consolidate `docs/memories/` into a clean, non-redundant `CONSOLIDATED.md`. $ARGUMENTS

## Steps

1. Orient: list `docs/memories/` (snapshots, `CONSOLIDATED.md`, `INDEX.md`) and read the current `INDEX.md`/`CONSOLIDATED.md`.
2. Run the driver's structural dedupe: `python3 .claude/skills/memory-orchestrator/orchestrate.py consolidate` (merges identical lines, groups by section).
3. Do the semantic pass the driver can't: collapse paraphrases, resolve contradictions, prune stale entries, and rewrite `CONSOLIDATED.md`.

Follow `.claude/skills/memory-consolidation/SKILL.md` for the full process.

## Notes

- This goes beyond the orchestrator driver's structural dedupe — the reasoning pass is the point of this skill.
- Invoked by `memory-orchestrator` before context compaction, on session end, and on the 12h timer.
