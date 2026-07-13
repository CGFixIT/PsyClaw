---
name: refactor
description: Refactor current CyClaw code while preserving behavior. Use when working in CGFixIT/CyClaw and the user asks to simplify architecture, remove duplication, improve module boundaries, or optimize a measured hot path without changing public behavior.
---

# Refactor

Use `.codex/routines/refactor.md` as the base workflow, then apply these
CyClaw-specific constraints.

## Workflow

1. Read `AGENTS.md`, the affected flow, every caller of the code to change, and
   the nearest tests.
2. Establish a behavior baseline with the smallest relevant test or smoke check.
3. Identify one concrete problem: duplicated logic, confused ownership, an
   unsafe boundary, or a measured bottleneck.
4. Reuse an existing helper or delete unnecessary code before adding a new
   abstraction.
5. Make one focused, behavior-preserving change.
6. Run the same baseline check and the narrowest lint/static check that covers
   the diff.
7. Review the diff for behavior drift, optional-layer imports, and security
   invariant changes.
8. Repeat only when the user requested an iterative refactor and another
   evidence-backed step remains.

Measure performance only when performance is in scope. Compare identical
conditions and report the actual command and result; do not impose a generic
latency target on endpoints or imports.

## Guardrails

- Preserve RAG-first retrieval, graph-enforced routing, external-provider
  gates, audit convergence, and human-reason soul governance.
- Keep `agentic/`, `sync/`, and `guardrails/` optional and out of the core
  request path.
- Do not mix dependency, CI, formatting, and behavior changes into the refactor.
- Do not commit each iteration or publish anything unless the user requested it.
- Stop if the change requires new behavior; reclassify it as a bugfix or feature.

Report the changed files, behavior-preservation evidence, checks run, and any
remaining unverified path.
