---
name: verification-specialist
description: Independently verify a supplied CyClaw change with executed checks, failure-path probes, and source-backed findings without modifying its implementation.
---

# Verify a CyClaw Change

Read the original request, diff, `AGENTS.md`, current test/workflow contracts,
and relevant callers. Record tested head/base and scope. Verification is
read-only with respect to implementation and Git history: do not fix code,
install dependencies, publish, or alter the operator's data as part of this skill.
Use isolated temporary outputs for probes and disable unnecessary caches.

## Select evidence by change

| Change | Evidence |
|---|---|
| Docs/skills | Frontmatter/UI metadata, local paths, referenced command syntax, maintained doc-sync, manual source comparison |
| Python behavior | Focused regression tests, error/boundary cases, touched lint; expand to full CI-equivalent tests for shared core/security changes |
| Graph/auth/egress | Invariant checker plus actual allowed/refused paths; mock external HTTP, preserve consent and source privacy |
| Install/workflow | Parse and run applicable static checks; build/test the changed profile on the correct platform when available |
| UI | Actual browser interaction and asset requests when UI behavior is in scope; HTML text checks alone are not rendered evidence |
| Refactor | Same baseline tests before/after, public behavior/caller inspection, measured performance when claimed |

Use the current `.github/workflows/ci.yml` and `lint.yml`. Bare pytest is not
coverage evidence; broad Ruff and mypy are not blocking CI gates merely because
they are configured. A docs-only edit does not require an unrelated application
build, full suite, or live server.

Execute the applicable checks and at least one relevant negative probe when
behavior changes. For guidance, exercise a realistic scenario against the stated
instructions and verify its source references instead of inventing a runtime
attack. Existing test success is evidence, not proof of every claim.

For each finding, confirm expected behavior, a reachable trigger, actual impact,
and whether it predates the patch. A unavailable dependency/platform is a
verification limit, not an implementation defect. Do not turn a warning,
platform skip, missing Ollama, or deliberately disabled feature into a failure.

Report the exact commands and observed results, actionable findings with file
anchors, skipped coverage and reasons, and one overall `PASS`, `FAIL`, or
`PARTIAL` verdict. PASS covers only the stated scope. Use PARTIAL for required
checks that could not run; never imply those passed.
