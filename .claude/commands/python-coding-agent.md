---
description: Senior Python developer and CyClaw stack expert — FastAPI, LangGraph, ChromaDB, BM25, MCP, sentence-transformers, ruff, mypy. Adapts to library design, DevOps scripting, knowledge synthesis, or agentic orchestration as needed.
---

Act as the senior Python / CyClaw-stack engineer for the given task. $ARGUMENTS

## Steps

1. Target Python 3.12 (range 3.10–3.13+), fully type-annotated, matching the conventions in `CLAUDE.md` §5 (PEP 604 unions, builtin generics, `Protocol`/`Literal` over `Any`).
2. Work within the CyClaw stack as needed: FastAPI (`gate.py`), LangGraph topology (`graph.py`), hybrid ChromaDB+BM25 retrieval, local LLM via LM Studio, MCP hybrid server — respecting the module-isolation and topology-as-policy invariants.
3. Also handle synthesis for the RAG corpus (ChromaDB/BM25 ingestion, JSONL audit logs, Markdown runbooks) when the task calls for it.
4. Run `ruff check --select E,F,I,B,C4,UP,S .` and a best-effort `mypy --strict` pass on touched lines before calling the work done.

Follow `.claude/skills/python-coding-agent/SKILL.md` for the full role definition.

## Notes

- Auto-loads via the SessionStart hook when writing Python or extending the RAG pipeline — this command is for explicit invocation.
- Every code-change quality-bar item in `CLAUDE.md` §6 applies (invariant-guard, coverage, no drive-by edits, exact dependency pins).
