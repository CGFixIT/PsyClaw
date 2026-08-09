# Bugfix Routine

## When To Use

Use this for a reported bug, failing test, runtime error, or CI failure with a likely code/config cause.

## Inputs To Establish

- Error message, failing command, PR/check URL, or reproduction steps.
- Expected behavior.
- Whether the fix should be local only, branch/PR, or direct patch.

Infer these from the request and repository first; ask only when a material
irreversible choice remains.

## Workflow

1. Read `AGENTS.md`, `$fable-protocol`, and the relevant subsystem docs.
2. Reproduce or inspect the failure before editing.
3. Trace ownership to the smallest file set.
4. Add or adjust a focused test when behavior changes.
5. Make the minimal fix.
6. Run the targeted test first, then Ruff or broader CI parity when risk warrants it.
7. Respect Codex sandbox and approval rules for dependency installs, network,
   server processes, GitHub operations, and git ref writes.
8. Document residual risk and unverified paths.

## Verification Checklist

- Root cause identified, not just symptom patched.
- Fix is scoped to the affected subsystem.
- Relevant test or smoke check ran.
- Ruff ran for Python changes when dependencies/tooling are available.
- No secrets, logs, caches, indexes, or local paths committed.

## Expected Final Response

- Root cause.
- Files changed.
- Commands run and results.
- Any approval-limited or unavailable checks.
- Remaining uncertainty or follow-up.
