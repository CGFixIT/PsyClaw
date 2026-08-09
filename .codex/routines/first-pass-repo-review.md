# First-Pass Repo Review

## When To Use

Use this when starting unfamiliar work in CyClaw, reviewing a new area, or validating setup before edits.

## Inputs To Establish

- Target task, issue, PR, or subsystem.
- Whether the user wants code changes or only a report.
- Any time or verification constraints.

Infer these from the request and repository first; ask only when a material
irreversible choice remains.

## Workflow

1. Read `AGENTS.md` and `$fable-protocol` first.
2. Read `CLAUDE.md` in full for substantive work; inspect the relevant parts of
   `README.md` and `.github/copilot-instructions.md`.
3. Inspect the root listing, relevant package manifests, CI workflows, and docs.
4. Identify the owning subsystem: core gateway, graph, retrieval, utils, sync, agentic, guardrails, docs, or CI.
5. Check for existing tests and previous audit notes before proposing changes.
6. Check `.codex/skills/`, `.codex/routines/`, and the map in `AGENTS.md` for
   a more specific workflow before acting.
7. Summarize what already exists, what is missing, and the smallest safe next step.

## Verification Checklist

- Confirmed Python/package/test/lint commands from repo files.
- Found the canonical docs for the subsystem.
- Checked whether a project-specific Codex skill or routine applies.
- Identified security invariants that could be affected.
- No edits made unless the user asked for implementation.

## Expected Final Response

- What was inspected.
- Key findings and risks.
- Recommended next action.
- Commands run, if any, with outcomes.
