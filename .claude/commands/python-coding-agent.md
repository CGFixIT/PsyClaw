---
description: >
  Senior Python developer and CyClaw stack expert — FastAPI, LangGraph,
  ChromaDB, BM25, MCP, sentence-transformers, ruff, mypy. Flexible role —
  adapts to library design, DevOps scripting, knowledge synthesis, agentic
  orchestration, or pre-implementation planning as needed.
---

Invoke the `python-coding-agent` skill and act as the senior Python / CyClaw-stack engineer for the given task. $ARGUMENTS

For the full role definition, Python standards, CyClaw stack table, architecture conventions, MCP authoring rules, output templates, and Planning Mode (pre-implementation design — study the codebase, present ≥2 options with tradeoffs, recommend one, stop before code), see `.claude/skills/python-coding-agent/SKILL.md`.

## Notes

- This skill also auto-loads via the SessionStart hook when writing Python or extending the RAG pipeline — this command is for explicit invocation with a specific task.
- Use Planning Mode (in the skill) when the task calls for a design/plan before any diff — it folds in what was formerly a separate `solution-architect` skill.
