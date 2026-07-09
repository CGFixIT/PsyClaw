---
description: Maintain a structured session notes file that preserves execution context for future continuation.
---

Update the structured session notes file with current execution context. $ARGUMENTS

## Steps

1. Locate the session notes file (`docs/SESSION_NOTES.md` or `.claude/session-notes/`, per `CLAUDE.md` §7).
2. Apply changes using the `Edit` tool exclusively — never rewrite the whole file.
3. The file has a rigid layout with section headings and italic description lines: never alter headings or the italic descriptions beneath them; only modify the actual content below each description within its section.
4. Stop after applying the edit.

Follow `.claude/skills/create-session-notes/SKILL.md` for the exact section layout.

## Notes

- Use this to record blockers per `CLAUDE.md` §7 (undefined behavior → `#cyclaw-dev`; security → private GitHub issue; config drift → `/sandbox-runtime-verification`).
- Session-scoped discoveries belong here or in `docs/memories/` (live), never in `.claude/memory/` (legacy).
