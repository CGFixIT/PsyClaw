# `scripts/` — repo development hygiene

Contributor-side tooling for this clone's git workflow. Nothing here runs at
CyClaw runtime or is imported by any Python module.

## Scripts

| Script | What it does |
|---|---|
| `install-githooks.sh` | Points `core.hooksPath` at the repo-managed `.githooks/` (pre-commit: branch-naming allowlist; pre-push: branch naming + fresh `origin/main` ancestry; commit-msg: `[prefix] - subject` title convention). Run once per clone. |
| `check-pr-template.sh` | Validates a PR body against `.github/PULL_REQUEST_TEMPLATE.md`'s required sections before you open the PR. Git hooks cannot intercept `gh pr create` bodies, so run this by hand (`gh pr view --json body -q .body \| scripts/check-pr-template.sh -`); CI runs the same headers as a blocking check via `.github/workflows/pr-template-check.yml`. Exit 0 = ok, 1 = missing sections. |

## Related

- Branch-prefix allowlist source of truth: `utils/agent_identity.py`
- Branch and PR conventions: `CLAUDE.md` §5, `.github/PULL_REQUEST_TEMPLATE.md`
