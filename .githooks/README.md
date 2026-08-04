# Git hooks (branch naming and fresh main)

Enforces **documented multi-vendor feature-branch prefixes** on commit and push,
plus fresh-`origin/main` ancestry for feature-branch pushes.

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
| `pre-push` | every push | fetches `origin/main`, refuses off-convention head refs, and refuses non-default branches that do not contain current `origin/main` |

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
