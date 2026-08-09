# Security Review Routine

## When To Use

Use this for auth, secrets, telemetry, network exposure, LangGraph routing, retrieval trust boundaries, dependency CVEs, or agentic/sync/guardrails changes.

## Inputs To Establish

- Threat model or change scope.
- Deployment mode: local, Docker, hybrid, sync enabled, agentic enabled, Postgres enabled.
- Whether a written report or code fix is expected.

Infer these from the request and repository first; ask only when a material
security-policy or irreversible deployment choice remains.

## Workflow

1. Read `AGENTS.md`, `$fable-protocol`, `docs/THREAT_MODEL.md`, and
   `.github/SECURITY.md`.
2. Identify which CyClaw invariants are in scope.
3. Inspect configuration defaults and env var handling.
4. Check for secrets, unsafe logging, network exposure, trust boundary crossings, and optional-layer imports into core paths.
5. For dependency changes, review security workflows and documented exceptions.
6. Use Codex Security skills when the user asks for a full repository, diff, or
   finding-specific security workflow and those skills are available.
7. Recommend minimal mitigations before broad redesigns.

## Verification Checklist

- RAG-first and audit convergence preserved.
- External calls remain opt-in and human-gated.
- No new secrets or local paths committed.
- Loopback binding preserved unless explicitly approved.
- Existing CVE exceptions are understood before changing them.

## Expected Final Response

- Security findings by severity.
- Affected files/components.
- Suggested fixes and verification commands.
- Residual risk.
- Whether a deeper Codex Security scan is warranted.
