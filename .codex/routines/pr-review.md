# PR Review Routine

## When To Use

Use this for reviewing a pull request, local diff, or proposed patch.

## Inputs To Establish

- PR number/URL or diff range.
- Review focus: correctness, security, CI, tests, dependency drift, or docs.
- Whether to leave a GitHub comment or report in chat.

Infer these from the request and repository first; ask only when a material
external-action choice remains.

## Workflow

1. Read `AGENTS.md` and `$fable-protocol`.
2. Inspect changed files and understand the PR goal.
3. For dependency/CI changes, compare `pyproject.toml`, `requirements.txt`,
   `constraints.txt`, `environment.yml`, and `Dockerfile`.
4. Look first for bugs, regressions, security issues, missing tests, and CI gaps.
5. Verify claims against code and tests; avoid style-only findings unless they block maintainability.
6. Leave a GitHub comment only when the user explicitly requested it. Then use
   the connector PR comment/review tools when permissions allow; if they return
   permission errors, report the blocker rather than retrying with unrelated
   tools. Otherwise report findings in chat.

## Verification Checklist

- Findings are actionable and grounded in file/line references where possible.
- Security invariants considered.
- Tests and CI impact considered.
- No unrequested edits made during review-only work.

## Expected Final Response

- Findings first, ordered by severity.
- Open questions or assumptions.
- Brief summary of reviewed scope.
- Tests/checks inspected, run, or explicitly not run.
