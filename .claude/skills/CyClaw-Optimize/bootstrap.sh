#!/usr/bin/env bash
# bootstrap.sh — CyClaw-Optimize harness.
#
# Deterministic setup + scan-seed for the CyClaw-Optimize workflow. It does the
# boring, scriptable parts so the agent can spend its budget on the parts only an
# agent can do: reading code for optimization opportunities and authoring PRs.
#
# What it does:
#   1. Pins the git identity the stop hook requires.
#   2. Ensures `main` is fetched and that we are on a fresh working branch cut
#      from origin/main (creates one if missing; never force-resets an existing
#      branch that already has work).
#   3. Prints a repo inventory to seed the time-boxed scan (dirs, file counts,
#      LOC, largest files, dependency manifests, CI workflows, recent commits).
#
# It deliberately does NOT: call the GitHub API (PR dedup is an MCP step the
# agent runs), edit any code, or commit. Read-only except for git config + an
# optional branch create.
#
# Usage:
#   bash .claude/skills/CyClaw-Optimize/bootstrap.sh [work-branch-name]
#
# If a branch name is given and it does not exist, it is created from
# origin/main. With no argument the script only reports the current branch.

set -euo pipefail

WORK_BRANCH="${1:-}"

hr() { printf '%s\n' "------------------------------------------------------------"; }

# ---------------------------------------------------------------------------
# 1. Git identity (driver-agnostic CyClaw Agent defaults; overridable via
#    CYCLAW_AGENT_COMMIT_EMAIL / CYCLAW_AGENT_COMMIT_NAME — see utils/agent_identity.py)
# ---------------------------------------------------------------------------
git config user.email "${CYCLAW_AGENT_COMMIT_EMAIL:-cyclaw-agent@users.noreply.github.com}"
git config user.name "${CYCLAW_AGENT_COMMIT_NAME:-CyClaw Agent}"
hr
echo "git identity: $(git config user.name) <$(git config user.email)>"

# ---------------------------------------------------------------------------
# 2. Fetch main + position on a working branch cut from origin/main
# ---------------------------------------------------------------------------
echo "fetching origin/main ..."
git fetch origin main --quiet || echo "WARN: fetch failed (offline?) — using local refs"

if [ -n "$WORK_BRANCH" ]; then
  if git show-ref --verify --quiet "refs/heads/${WORK_BRANCH}"; then
    echo "branch '${WORK_BRANCH}' already exists — checking it out (no reset)"
    git checkout "$WORK_BRANCH"
  else
    echo "creating '${WORK_BRANCH}' from origin/main"
    git checkout -b "$WORK_BRANCH" origin/main
  fi
fi
hr
echo "current branch: $(git rev-parse --abbrev-ref HEAD)"
echo "merge-base with origin/main: $(git merge-base HEAD origin/main 2>/dev/null || echo '?')"
echo "commits ahead of origin/main: $(git rev-list --count origin/main..HEAD 2>/dev/null || echo '?')"

# ---------------------------------------------------------------------------
# 3. Repo inventory — seeds the time-boxed scan
# ---------------------------------------------------------------------------
hr
echo "REPO INVENTORY (scan seed)"
hr

echo "## File counts by area"
for d in gate.py graph.py mcp_hybrid_server.py metrics.py \
         agentic sync retrieval utils schemas llm \
         tests .github .claude config.yaml requirements.txt pyproject.toml; do
  if [ -d "$d" ]; then
    n=$(find "$d" -type f 2>/dev/null | wc -l | tr -d ' ')
    printf '  %-22s %s files\n' "$d/" "$n"
  elif [ -f "$d" ]; then
    l=$(wc -l < "$d" | tr -d ' ')
    printf '  %-22s %s lines\n' "$d" "$l"
  fi
done

echo
echo "## Largest Python files (LOC) — god-module / refactor candidates"
find . -name '*.py' -not -path './.git/*' -not -path './*/node_modules/*' \
  -exec wc -l {} + 2>/dev/null | sort -rn | sed -n '2,16p'

echo
echo "## CI workflows (license/secret-free enhancement candidates)"
ls -1 .github/workflows/ 2>/dev/null | sed 's/^/  /'

echo
echo "## Dependency manifests"
for f in requirements.txt constraints.txt pyproject.toml .osv-scanner.toml; do
  [ -f "$f" ] && printf '  %s (%s lines)\n' "$f" "$(wc -l < "$f" | tr -d ' ')"
done

echo
echo "## Grok / external-LLM touchpoints (outdated-API audit)"
grep -rln -e 'grok' -e 'x.ai' -e 'api.x.ai' \
  --include='*.py' --include='*.yaml' --include='*.yml' --include='*.md' . \
  2>/dev/null | grep -v '/.git/' | sed 's/^/  /' | head -20

echo
echo "## Recent commits on main (avoid re-doing landed work)"
git log origin/main --oneline -n 12 2>/dev/null | sed 's/^/  /'

hr
echo "Bootstrap complete. Next: dispatch the 4-minute scan subagent (see SKILL.md)."
echo "Then run the PR-dedup MCP step BEFORE selecting focus areas:"
echo "  mcp__github__list_pull_requests(owner=CGFixIT, repo=CyClaw, state=open)"
