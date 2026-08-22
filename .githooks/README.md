# Git hooks (branch naming, title prefix, fresh main)

Enforces **documented multi-vendor feature-branch prefixes** on commit and push,
**PR-template commit title prefixes** on every commit subject, plus
fresh-`origin/main` ancestry for feature-branch pushes.

Canonical list (must stay aligned with `utils.agent_identity.ALLOWED_BRANCH_PREFIXES`,
`CLAUDE.md` §5 / Kimi section, and `.github/PULL_REQUEST_TEMPLATE.md`):

| Prefix | Driver |
|--------|--------|
| `grok/<feature>` | Grok Build |
| `claude/<feature>` | Claude Code / agentic harness |
| `codex/<feature>` | Codex |
| `kimi/<feature>` | Kimi / Kimi Code |
| `agent/<feature>` | Generic / default agent identity |
| `CyClaw/<feature>-…` / `cyclaw/…` | CyClaw direct / MCP |

Also allowed: `main`, `master`, `develop`, `dependabot/*`, `renovate/*`, `release/*`, `hotfix/*`.

| Hook | When | Behavior |
|------|------|----------|
| `pre-commit` | every commit | refuses commit if current branch is off-convention |
| `commit-msg` | every commit | refuses subjects that are not `[prefix] - description` per the PR template Title section |

The `commit-msg` hook's allowlist is a closed set — `invariant`, `governance`,
`fsconnect`, `agentic`, `rag`, `harness`, `security`, `docs`, `infra`, `fix`,
`feat`. It also lets through `chore`, `build`, and `ci(deps)` subjects, and
skips the check entirely for merge/revert/fixup commits (`Merge`, `Revert`,
`fixup!`, `squash!`, `Amend!`). Anything else is rejected, so reach for the
closest listed prefix rather than inventing one.
| `pre-push` | every push | fetches `origin/main`, refuses off-convention head refs, and refuses non-default branches that do not contain current `origin/main` |

## PR body template (not a git hook)

Git cannot intercept PR bodies created via the GitHub UI, `gh pr create`, or
the GitHub connector API. For that layer:

1. **Always** fill [`.github/PULL_REQUEST_TEMPLATE.md`](../.github/PULL_REQUEST_TEMPLATE.md) fully when opening a PR (required for Grok Build + GitHub connector on cgfixit/CyClaw).
2. **Local check:** `scripts/check-pr-template.sh path/to/body.md` before create.
3. **CI:** blocking check via `.github/workflows/pr-template-check.yml` (same headers as `scripts/check-pr-template.sh`).

Example:

```bash
# draft body from template, edit, then gate
cp .github/PULL_REQUEST_TEMPLATE.md /tmp/pr-body.md
# ... fill sections ...
scripts/check-pr-template.sh /tmp/pr-body.md
gh pr create --title '[docs] - …' --body-file /tmp/pr-body.md
```

## Install (once per clone)

```bash
bash scripts/install-githooks.sh
# or: git config core.hooksPath .githooks && chmod +x .githooks/*
```

Verify:

```bash
git config core.hooksPath   # → .githooks
```

The pre-push gate deliberately does not rebase or force-push for you. Rebase,
inspect conflicts, rerun the relevant checks, and use force-with-lease only
with explicit approval. It also cannot infer multi-PR semantics: map shared
files and trial the chronological merge order as required by
`.codex/Codex_instructions.md`.

## Bypass (emergency only)

```bash
git commit --no-verify
git push --no-verify
```

Do not use bypass for routine feature work.
