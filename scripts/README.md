# `scripts/` — repo development hygiene

Contributor-side tooling for this clone's git workflow, plus one operator
throughput probe. Nothing here is imported by gate.py / graph.py / llm at
runtime.

## Scripts

| Script | What it does |
|---|---|
| `install-githooks.sh` | Points `core.hooksPath` at the repo-managed `.githooks/` (pre-commit: branch-naming allowlist; pre-push: branch naming + fresh `origin/main` ancestry; commit-msg: `[prefix] - subject` title convention). Run once per clone. |
| `check-pr-template.sh` | Validates a PR body against `.github/PULL_REQUEST_TEMPLATE.md`'s required sections before you open the PR. Git hooks cannot intercept `gh pr create` bodies, so run this by hand (`gh pr view --json body -q .body \| scripts/check-pr-template.sh -`); CI runs the same headers as a blocking check via `.github/workflows/pr-template-check.yml`. Exit 0 = ok, 1 = missing sections, 2 = usage error (no input argument / `CYCLAW_PR_BODY_FILE` unset, or the named file does not exist). |
| `measure_local_llm_throughput.py` | Operator probe: hits loopback Ollama `POST /api/generate` and prints prefill/decode tok/s from the runner's own nanosecond counters. stdlib only. Not imported by gate/graph/llm. See `docs/! How-To-Guides/OLLAMA_SETUP.md`. |

## Related

- Branch-prefix allowlist source of truth: `utils/agent_identity.py`
- Branch and PR conventions: `CLAUDE.md` §5, `.github/PULL_REQUEST_TEMPLATE.md`
