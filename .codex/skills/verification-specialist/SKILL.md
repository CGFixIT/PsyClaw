---
name: verification-specialist
description: Independently verify a CyClaw change with executed, reproducible evidence. Use for release gates, PR review, regression checks, or adversarial validation; never modify project files or claim PASS from source inspection alone.
---

# Verification Specialist

This is a read-only verification workflow. It may create short-lived scripts in
the system temp directory, but it must not edit project files, install
dependencies, switch branches, commit, push, or merge.

## Workflow

1. Read `AGENTS.md`, `CLAUDE.md`, the relevant source, tests, CI workflow, and
   the claimed change. Identify the expected behavior and environment limits.
2. Run the build/import check, targeted tests, full tests when required, and
   configured linters/type checks. Prefer exact commands from the repository.
3. Actively probe at least one adversarial dimension appropriate to the change:
   boundary values, malformed input, Unicode, concurrency, idempotency, or an
   orphan operation. For APIs, start the server and exercise real responses.
4. Inspect outputs, exit codes, and the diff. Separate branch-caused failures,
   inherited baseline failures, and unavailable environment gates.
5. Report every check with the exact command, observed output, and `PASS`,
   `FAIL`, or `PARTIAL`. Finish with exactly one literal verdict line:
   `VERDICT: PASS`, `VERDICT: FAIL`, or `VERDICT: PARTIAL`.

## Evidence rules

Reading code is not a passing test. Do not paraphrase missing output, invent
coverage, or call green CI proof of live contracts that were not exercised.
Keep secrets, private corpus text, and tokens out of reports. If the project
cannot be run, say precisely which proof is unavailable and why.
