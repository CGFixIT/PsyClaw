---
description: Maintain a structured session notes file that preserves execution context for future continuation.
---

Update the structured session notes file with current execution context. $ARGUMENTS

## Constraints

- Apply changes to the session notes file using the `Edit` tool exclusively, then stop.
- The notes file follows a rigid layout with section headings and italic description lines — never alter headings or the italic descriptions beneath them.
- Only modify the actual content below each italic description within its section.
- Issue all `Edit` tool calls in parallel within a single message.

## Sections

The notes file contains these fixed sections:

- **Session Title** — a short label for this work session
- **Current State** — where things stand right now (always update this — it is vital for continuity)
- **Task Specification** — what was asked for and acceptance criteria
- **Files and Functions** — which files and functions were touched or are relevant
- **Workflow** — steps taken, commands run, order of operations
- **Errors & Corrections** — problems hit and how they were resolved
- **Codebase and System Documentation** — architectural notes, environment details, system behavior discovered
- **Learnings** — insights gained that apply beyond this session
- **Key Results** — concrete outcomes produced
- **Worklog** — chronological record of significant actions

## Content Guidelines

- Write thorough, information-dense entries — record file paths, function names, error messages, exact commands, and outputs.
- Do not mention the act of note-taking within the notes themselves.
- It is fine to leave a section untouched when there is nothing meaningful to add — do not insert placeholder text.
- Keep each individual section under roughly 2000 tokens — compress aggressively if nearing that boundary.

## Usage

When invoked, update the session notes file (`docs/SESSION_NOTES.md` or `.claude/session-notes/`, per `CLAUDE.md` §7). If the file does not exist, create it with all section headings and italic descriptions intact. Then apply updates to the relevant sections using parallel `Edit` tool calls.

## Notes

- Use this to record blockers per `CLAUDE.md` §7 (undefined behavior → `#cyclaw-dev`; security → private GitHub issue; config drift → `/sandbox-runtime-verification`).
- Session-scoped discoveries belong here or in `docs/memories/` (live), never in `.claude/memory/` (legacy).
