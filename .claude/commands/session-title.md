---
description: Generate a concise title for this session. Use when asked to title the session, name the conversation, or produce a session label for notes or memory.
---

Generate a concise title for this session.

## Steps

1. Identify the primary topic or objective of the session.
2. Produce a 3–7 word title in sentence case (capitalize only the first word and proper nouns).
3. Return a JSON object with a single `"title"` field.

## Notes

- No trailing punctuation, no quotes inside the title string itself.
- Prefer the concrete deliverable over the general subject area (e.g. "Add skill command files" over "CyClaw skills work").
