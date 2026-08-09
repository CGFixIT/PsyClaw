# Feature Routine

## When To Use

Use this for new behavior, an endpoint, CLI capability, workflow enhancement, or user-visible functionality.

## Inputs To Establish

- User story or desired behavior.
- Target interface: API, MCP, CLI, UI, sync, agentic, guardrails, CI, or docs.
- Security boundaries and rollout expectations.

Infer these from the request and repository first; ask only when a material
irreversible choice remains.

## Workflow

1. Confirm the user explicitly requested net-new behavior; CyClaw is otherwise
   in feature freeze, so route maintenance work to the appropriate routine.
2. Read `AGENTS.md`, `$fable-protocol`, then inspect the owning subsystem.
3. Check existing patterns before designing anything new.
4. Verify whether the feature touches CyClaw invariants, optional layers, or secrets.
5. Prefer a narrow implementation that composes with current modules.
6. Add tests for behavior, validation, and failure modes.
7. Update docs only where users or future agents need the change.
8. Run targeted tests, Ruff, and broader CI parity for cross-cutting work.
9. Keep optional layers optional; do not make `sync/`, `agentic/`, or
   `guardrails/` required by the core gateway unless explicitly requested.

## Verification Checklist

- Existing patterns reused where practical.
- Core gateway/graph/MCP isolation preserved.
- New config defaults are safe and offline-first.
- Tests cover success and refusal/error behavior.
- Docs mention any new setup or runtime command.

## Expected Final Response

- What feature was added.
- How it fits the existing architecture.
- Tests/checks run.
- Approval-limited checks or external services not exercised.
- Any deployment or security notes.
