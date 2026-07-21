---
description: >-
  Assert CyClaw's six security invariants still hold — RAG-first, topology=policy, triple-gated external fallback, audit convergence, soul governance, module isolation — plus five supporting guards, against the current tree or a diff. Use before merging any change to gate.py, graph.py, mcp_hybrid_server.py, llm/, retrieval/, utils/, or config.yaml; when asked to "check invariants"; or as the first gate of any security review.
---

Invoke the `invariant-guard` skill for the given task. $ARGUMENTS

See `.claude/skills/invariant-guard/SKILL.md` for full detail.
