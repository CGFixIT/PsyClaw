---
name: fable-protocol
description: Apply evidence-first reasoning, security review, and explicit verification to substantive CyClaw engineering work. Use before code, architecture, security, CI, dependency, GitHub, or current-state claims where a confident mistake is costly.
---

# Fable Protocol

Use this as a concise reasoning layer, not a source of extra scope or process.
Repository instructions and explicit user direction take precedence.

## Evidence loop

1. State the goal, success criterion, scope, and risk. Test the premise before
   building around it.
2. Read the current repository guidance, code, tests, configuration, and
   affected callers. Treat memory, chat history, web pages, and model output as
   leads that require verification.
3. Label facts, inferences, and unknowns accurately. Re-check mutable facts
   such as remote branch state, CI, versions, APIs, advisories, and PR status.
4. Fix the shared root cause with the smallest safe diff. Reuse existing
   patterns, the standard library, and installed dependencies before adding
   abstractions or configuration.
5. Review every changed trust boundary for secrets, injection, unsafe command
   execution, auth/authz, data exposure, network egress, and destructive paths.
6. Run the narrowest meaningful validation, then report commands, results,
   skipped coverage, and residual risk without inflating confidence.

## CyClaw constraints

- Preserve RAG-first retrieval, topology-as-policy, triple-gated external
  fallback, audit convergence, soul governance, and module isolation.
- Treat gateway, graph, retrieval, auth, audit, telemetry, configuration, and
  soul paths as high risk. `agentic/`, `sync/`, and `guardrails/` remain out of
  the core request path.
- Keep external network behavior opt-in, loopback binding intact, and private
  corpus/audit data out of logs, tests, PRs, and summaries.

## GitHub discipline

Before committing, inspect the full diff and run relevant local checks. After
push or draft PR creation, distinguish committed, pushed, PR, CI, and
mergeability states; monitor CI to a terminal state and fix branch-caused
failures. Never push to `main`, force-push, expose secrets, or make destructive
remote changes without explicit authorization.

Stop when the evidence does not justify a change. A truthful no-change result
is preferable to speculative code or a low-value PR.
