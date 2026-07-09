---
description: Compose a brief label describing what recent tool calls accomplished. Use when asked to summarize tools used, describe recent actions, or produce a compact activity label for the UI or logs.
---

Compose a brief label describing what the recent tool calls accomplished. $ARGUMENTS

## Steps

1. Review the recent tool calls in this session.
2. Compose a single-line label in past tense: verb + the most distinctive noun from the operation.
3. Keep it around 30 characters — treat it like a git commit subject; strip articles, connectors, and location context first when trimming for length.

## Notes

- This label truncates in the UI at roughly 30 characters — err short over descriptive.
- No trailing punctuation.
