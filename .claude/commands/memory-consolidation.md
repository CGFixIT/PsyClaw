---
description: Run a semantic "dream" consolidation over docs/memories/ — merge paraphrases, resolve contradictions, prune stale entries, and rewrite the CONSOLIDATED.md working set. Use when asked to consolidate, deduplicate, or clean up memory. Invoked by the memory-orchestrator before context compaction, on session end, and on the 12h timer.
---

Consolidate `docs/memories/` into a clean, non-redundant `CONSOLIDATED.md`. $ARGUMENTS

Run a "dream" consolidation pass over the CyClaw memory directory, `docs/memories/`, to produce a clean, non-redundant working set in `docs/memories/CONSOLIDATED.md`.

This is the *semantic* consolidation that goes beyond the orchestrator driver's structural dedupe. The driver (`orchestrate.py consolidate`) already merges identical lines and groups by section; your job is the reasoning the driver can't do — collapsing paraphrases, resolving contradictions, and pruning.

## Phase 1 — Orient

- List `docs/memories/` (snapshots, `CONSOLIDATED.md`, `INDEX.md`).
- Read `INDEX.md` and `CONSOLIDATED.md`.
- Skim recent timestamped snapshots to understand current memory state.

## Phase 2 — Gather Recent Signal

- Pull new information worth persisting from the newest snapshots.
- Identify drifted memories that contradict the current codebase state.
- Search snapshots narrowly (grep with targeted queries) for overlooked details.

## Phase 3 — Consolidate

- Rewrite `docs/memories/CONSOLIDATED.md` by merging new signal into existing entries — collapse near-duplicates and paraphrases the structural pass left behind.
- Convert any relative dates ("yesterday", "last week") to absolute dates.
- Delete facts contradicted by fresher evidence.

## Phase 4 — Prune and Index

- Run `python3 .claude/skills/memory-orchestrator/orchestrate.py consolidate` afterward so `INDEX.md` and the timer state refresh, or update `INDEX.md` by hand.
- Remove stale or dangling pointers.
- Shorten verbose entries without losing essential meaning.
- Resolve any remaining contradictions between entries.

## Constraints

- Prefer fewer, stronger memories over many weak ones.
- Merge overlapping entries by evidence strength and recency.
- Promote durable patterns and constraints; demote one-off observations.
- Edit only files under `docs/memories/`.

## Format

Return a brief report listing what was consolidated, what was updated, and what was pruned.

## Notes

- This goes beyond the orchestrator driver's structural dedupe — the reasoning pass is the point of this skill.
- Invoked by `memory-orchestrator` before context compaction, on session end, and on the 12h timer.
