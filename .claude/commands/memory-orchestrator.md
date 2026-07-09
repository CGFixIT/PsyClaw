---
description: Orchestrate CyClaw's memory lifecycle — extract durable memories, consolidate them, and persist every pass to docs/memories/<date_time>.md. Wraps the memory-extraction and memory-consolidation skills.
---

Run a full memory pass: extract, consolidate, and persist to `docs/memories/`. $ARGUMENTS

## Steps

1. Get the target snapshot path from the deterministic driver: `python3 .claude/skills/memory-orchestrator/orchestrate.py path`.
2. Run memory extraction over the recent conversation (see `/memory-extraction` / `.claude/skills/memory-extraction/SKILL.md`).
3. Run consolidation over `docs/memories/` to merge paraphrases, resolve contradictions, and rewrite `CONSOLIDATED.md` (see `/memory-consolidation` / `.claude/skills/memory-consolidation/SKILL.md`).
4. Update `INDEX.md`.

Follow `.claude/skills/memory-orchestrator/SKILL.md` for the full lifecycle (driver + semantic passes).

## Notes

- Live memory lives ONLY in `docs/memories/`; `.claude/memory/` is legacy — never add there.
- Invoked automatically before context compaction, on session end, and on the 12h timer — manual invocation is for on-demand snapshots.
