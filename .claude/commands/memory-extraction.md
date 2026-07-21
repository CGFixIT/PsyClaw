---
description: Extract durable memories from the recent conversation and persist them as a timestamped snapshot under docs/memories/. Use when asked to remember something, save a memory, or capture learnings. Invoked by the memory-orchestrator on manual /memory, before context compaction, and on the 12h timer.
---

Extract durable memories from this conversation and save them as a timestamped snapshot. $ARGUMENTS

Act as a memory extraction subagent. Examine the most recent ~N messages in the conversation and persist useful memories as a **timestamped snapshot** in the CyClaw memory directory, `docs/memories/`.

This skill is the *semantic* half of the memory lifecycle; the `memory-orchestrator` skill owns the *mechanics* (paths, timers, hooks). Get the exact target path from the driver:

```bash
python3 .claude/skills/memory-orchestrator/orchestrate.py path
```

Write your snapshot to that path (`docs/memories/<YYYY-MM-DD_HHMMSS>.md`). If you write the file directly, also run `orchestrate.py consolidate` (or `auto`) so `INDEX.md` and the timer state reflect it.

## Constraints

- Available tools: Read, Grep, Glob, read-only Bash, and Edit/Write restricted to the memory directory (`docs/memories/`) only. The `rm` command is not permitted.
- You have a limited turn budget. Use an efficient two-turn strategy:
  - **Turn 1** — Issue all Read calls in parallel to gather existing memory state (`docs/memories/CONSOLIDATED.md`, recent snapshots, `INDEX.md`)
  - **Turn 2** — Write the new snapshot
- You MUST draw exclusively from the last ~N messages. Do not investigate further — no grepping source files, no reading application code, no verifying claims.
- If the user explicitly requests something be remembered, persist it immediately.
- If the user explicitly requests something be forgotten, locate the relevant entry in `docs/memories/` and remove it.

## What to Capture

Keep memories general and durable. Suitable categories (use these as `## ` headings in the snapshot):

- **User preferences** — coding style, tool choices, naming conventions, communication preferences
- **Project patterns** — architectural decisions, directory conventions, dependency choices
- **Error corrections** — recurring mistakes and their proven fixes
- **Workflow notes** — deployment steps, testing procedures, environment quirks

## Organization

- Group memories semantically by topic, not by the order they appeared.
- When information overlaps with an existing memory in `CONSOLIDATED.md`, prefer recording the *delta* — the consolidation pass will merge it.
- When stored information is contradicted by newer evidence, note the correction so consolidation can replace the outdated version.
- Before writing, skim the existing working set so the snapshot adds signal rather than duplicating it.

## Format

Each memory entry should contain:

- **Statement** — The fact or preference being recorded
- **Evidence** — Brief supporting context from the conversation
- **Confidence** — high / medium / low

## Notes

- This is the semantic half of the memory lifecycle; `memory-orchestrator` owns the mechanics (paths, timers, hooks).
- Do not write to `.claude/memory/` — that path is legacy.
