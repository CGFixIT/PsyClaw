---
name: invariant-guard
description: Validate CyClaw's six security invariants and run the maintained mutation-tested checker. Use after changes to graph, gateway, retrieval, auth, telemetry, sanitizer, audit, soul, or module-isolation paths, and before publishing security-sensitive work.
---

# Invariant Guard

The checker is static and dependency-light. It is a gate, not a substitute for
reading the affected flow or testing the endpoint.

## Workflow

1. Read the diff and trace callers across the trust boundary. Confirm the
   intended behavior still preserves RAG-first retrieval, topology-as-policy,
   triple-gated external fallback, audit convergence, soul governance, and
   optional-module isolation.
2. Run:

   ```text
   python .claude/skills/invariant-guard/check_invariants.py
   ```

3. Run the self-test when validating the guard itself:

   ```text
   bash .claude/skills/invariant-guard/verify.sh
   ```

   On Windows without Bash, run the Python checker and inspect the script
   manually; do not claim the mutation self-test ran.
4. Add targeted behavioral tests for the changed path and expand to the CI
   suite for shared routing, auth, retrieval, config, or security changes.
5. Treat any new failure as blocking. Do not loosen an invariant or delete an
   assertion to obtain a green result.

## Boundary

This skill does not mutate `data/personality/soul.md`, config values, graph
edges, or checker rules. It reports failures for a human decision and keeps
the PR draft until the full evidence is available.
