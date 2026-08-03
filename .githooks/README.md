# Git hooks (branch naming)

Enforces **`grok/<feature>`** for feature branches on commit and push.

| Hook | When | Behavior |
|------|------|----------|
| `pre-commit` | every commit | refuses commit if current branch is off-convention |
| `pre-push` | every push | refuses push of any off-convention head ref |

**Allowlist:** `main`, `master`, `develop`, `grok/*`, `claude/*` (agentic harness), `dependabot/*`, `renovate/*`, `release/*`, `hotfix/*`.

## Install (once per clone)

```bash
bash scripts/install-githooks.sh
# or: git config core.hooksPath .githooks && chmod +x .githooks/*
```

Verify:

```bash
git config core.hooksPath   # → .githooks
```

## Bypass (emergency only)

```bash
git commit --no-verify
git push --no-verify
```

Do not use bypass for routine feature work.
