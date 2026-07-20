---
description: Orchestrate CyClaw's memory lifecycle — extract durable memories, consolidate them, and persist every pass to docs/memories/<date_time>.md. Use when asked to remember something, save/extract memory, run memory consolidation, or when memory should be captured before context is compacted or a session is archived/deleted. Wraps the memory-extraction and memory-consolidation skills.
---

Run a full memory pass: extract, consolidate, and persist to `docs/memories/`. $ARGUMENTS

Owns CyClaw's memory lifecycle end to end. Every extraction or consolidation pass is persisted as a timestamped markdown snapshot under `docs/memories/<YYYY-MM-DD_HHMMSS>.md`, with a maintained `INDEX.md` and a deduplicated `CONSOLIDATED.md` working set.

Two halves work together:

- **Deterministic driver** — `.claude/skills/memory-orchestrator/orchestrate.py` handles *mechanics*: file paths, the 12-hour timer, structural dedupe, the index, and Claude Code hook plumbing.
- **Semantic skills** — `memory-extraction` (what to remember from the recent conversation) and `memory-consolidation` (semantic merge of the working set) do the *reasoning*. This skill invokes them and routes their output through the driver.

All paths below are relative to the repo root (`<unit>/`).

## Trigger matrix

| Trigger | Action | How |
|---|---|---|
| Manual `/memory` (in session) | **extract only** | agent runs extraction → writes to `orchestrate.py path` |
| Manual `/memory-orchestrator` | **consolidate + extract** | `orchestrate.py auto` + agent extraction |
| Context about to compact | **consolidate + extract** | `PreCompact` hook (auto) |
| Session archived / deleted / ended | **consolidate** (deterministic) | `SessionEnd` hook (auto) |
| Every 12 hours | **consolidate + extract** | `orchestrate.py timer-check` |

## Run (agent path)

The driver is the entry point. From the repo root:

```bash
# Where should the next memory snapshot go? (ensures docs/memories/ exists)
python3 .claude/skills/memory-orchestrator/orchestrate.py path
```

**Extract only** (manual `/memory`) — do the reasoning with the `memory-extraction` skill over the recent messages, then write the result to the path the driver prints. Or pipe content straight in:

```bash
echo "## Workflow notes
- <durable note here> (high)" | python3 .claude/skills/memory-orchestrator/orchestrate.py extract
```

**Consolidate + extract** (manual full pass / 12h timer):

```bash
python3 .claude/skills/memory-orchestrator/orchestrate.py auto   # consolidate THEN extract
```

**Is consolidation due?** (the no-hook 12-hour timer):

```bash
python3 .claude/skills/memory-orchestrator/orchestrate.py timer-check   # prints DUE / NOT DUE
```

Run `timer-check` opportunistically (e.g. at session start or before a long task); if it prints `DUE`, run `auto`.

## Run (automatic, via hooks)

`.claude/settings.json` wires two events to the driver's `hook` subcommand, which reads the Claude Code hook JSON on stdin:

- **`PreCompact`** — runs the deterministic consolidation immediately, then emits `additionalContext` instructing the agent to run the `memory-extraction` skill *before* compaction proceeds.
- **`SessionEnd`** — runs the deterministic consolidation snapshot so on-disk memory is left tidy.

No manual step is needed; the hooks fire on the lifecycle events.

## Semantic guidance (extraction)

When performing the extraction reasoning (see the `memory-extraction` skill for the full contract): draw **only** from the recent messages, capture durable signal grouped by section, and skip one-off trivia. Sections:

- `## User preferences` — coding style, tool choices, conventions
- `## Project patterns` — architecture decisions, directory conventions
- `## Error corrections` — recurring mistakes and proven fixes
- `## Workflow notes` — deployment/test steps, environment quirks

Tag each entry with confidence `(high|medium|low)`. Write the snapshot to the path from `orchestrate.py path`.

## Semantic guidance (consolidation)

The driver's `consolidate` does a **structural** merge (dedupe identical lines, group by section). For a **semantic** merge — collapsing paraphrases, resolving contradictions, converting relative dates to absolute — run the `memory-consolidation` skill over `docs/memories/CONSOLIDATED.md` and the snapshots, then rewrite `CONSOLIDATED.md`.

## Files

```
docs/memories/
  <YYYY-MM-DD_HHMMSS>.md      # one snapshot per extraction
  CONSOLIDATED.md            # deduplicated working set
  INDEX.md                   # pointers + last-run timestamps
  .orchestrator-state.json   # last extraction / consolidation times (for the 12h timer)
```

## Gotchas

- **Hooks can't run an LLM turn.** A shell hook cannot, by itself, do the semantic extraction. `PreCompact` works around this by injecting `additionalContext` so the *agent* runs extraction before compaction. At `SessionEnd` the session is already terminating, so **only the deterministic consolidation runs** — the just-ended conversation is not LLM-extracted at that instant. For guaranteed semantic capture before a deliberate clear/archive, run `/memory` first.
- **`hook` stdout is JSON only.** All status text goes to stderr so Claude Code can parse the `PreCompact` `additionalContext`. Don't add `print()` to stdout in the hook path.
- **`path` stdout is one line** (the path) — safe to capture in a var.
- **Timer is timestamp-based, not a daemon.** `timer-check` compares against `last_consolidation` in `.orchestrator-state.json`; nothing runs on a real clock. Poll it; it never blocks.
- **`docs/memories/` is tracked in git** — snapshots are committed so they survive the ephemeral remote container. Commit them as part of normal work.

## Troubleshooting

- `PreCompact` injected nothing → confirm `.claude/settings.json` registers the hook and that the driver's stdout parsed as JSON: `echo '{"hook_event_name":"PreCompact"}' | python3 .claude/skills/memory-orchestrator/orchestrate.py hook 2>/dev/null | python3 -m json.tool`.
- `timer-check` always says `DUE` → no `last_consolidation` stamped yet; run `consolidate` or `auto` once.

## Notes

- Live memory lives ONLY in `docs/memories/`; `.claude/memory/` is legacy — never add there.
- Invoked automatically before context compaction, on session end, and on the 12h timer — manual invocation is for on-demand snapshots.
