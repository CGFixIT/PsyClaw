# Git hooks (branch naming)

Enforces **documented multi-vendor feature-branch prefixes** on commit and push.

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
| `pre-push` | every push | refuses push of any off-convention head ref |

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
