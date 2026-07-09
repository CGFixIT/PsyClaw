---
description: Assert CyClaw's six security invariants still hold — RAG-first, topology=policy, triple-gated external fallback, audit convergence, soul governance, module isolation — plus five supporting guards, against the current tree or a diff.
---

Check that the six security invariants (and supporting guards) still hold. $ARGUMENTS

## Steps

1. Run the static checker: `python3 .claude/skills/invariant-guard/check_invariants.py`.
2. It statically asserts: I1 RAG-first, I2 topology=policy, I3 triple-gated external fallback, I4 audit convergence, I5 soul governance, I6 module isolation — plus telemetry-kill ordering, fail-closed auth, sanitizer contract phrases, BM25-as-JSON, and MCP `sampling: None`.
3. For anything the checker can't see (semantic intent behind a diff), read the diff by hand against the invariant table in `CLAUDE.md` §3.
4. Report PASS/FAIL per invariant; do not silently wave through a failure.

Follow `.claude/skills/invariant-guard/SKILL.md` for the full breakdown of each check.

## Notes

- Run before merging any change to `gate.py`, `graph.py`, `mcp_hybrid_server.py`, `llm/`, `retrieval/`, `utils/`, or `config.yaml`.
- This skill checks invariants only — it does not review style, performance, or test coverage.
- If an invariant must change, that requires explicit user approval and an argued case in the PR body, not a silent pass.
