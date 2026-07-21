---
description: Maintain a structured session notes file that preserves execution context for future continuation.
---

Invoke the `create-session-notes` skill and update the structured session notes file with current execution context. $ARGUMENTS

See `.claude/skills/create-session-notes/SKILL.md` for the fixed section layout, content guidelines, and the file location (`docs/SESSION_NOTES.md` or `.claude/session-notes/`, per `CLAUDE.md` §7).

## Notes

- Use this to record blockers per `CLAUDE.md` §7 (undefined behavior → `#cyclaw-dev`; security → private GitHub issue; config drift → `/sandbox-runtime-verification`).
- Session-scoped discoveries belong here or in `docs/memories/` (live), never in `.claude/memory/` (legacy).
