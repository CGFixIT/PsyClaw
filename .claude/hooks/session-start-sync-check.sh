#!/usr/bin/env bash
# CyClaw SessionStart hook — git identity + local/remote sync guard.
#
# Purpose (proactive, NON-destructive):
#   1. Pin the commit identity to noreply@anthropic.com / Claude so commits are
#      not flagged "Unverified" by the stop-hook (recurring friction otherwise).
#      Both values are overridable via CYCLAW_AGENT_COMMIT_EMAIL /
#      CYCLAW_AGENT_COMMIT_NAME (the same knobs agentic/deepagent_github/
#      repo_workspace.py reads); the defaults preserve the pinned convention.
#   2. Fetch the default branch and REPORT divergence between local and remote.
#      It never resets, rebases, pushes, or deletes — it only informs, so a
#      human stays in control of how to reconcile.
#
# Exit code is always 0: this hook advises, it must never block a session.
set -uo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repo_root" || exit 0

# ── 1. Pin commit identity (repo-local, durable) ─────────────────────────────
git config --local user.email "${CYCLAW_AGENT_COMMIT_EMAIL:-noreply@anthropic.com}"
git config --local user.name  "${CYCLAW_AGENT_COMMIT_NAME:-Claude}"

# ── 2. Detect default branch (origin/HEAD, fallback main) ────────────────────
default_branch=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')
default_branch=${default_branch:-main}

# ── 3. Fetch the default branch (read-only) ──────────────────────────────────
git fetch --quiet origin "$default_branch" 2>/dev/null || {
  echo "[sync-check] Could not fetch origin/$default_branch (offline?). Skipping divergence report."
  exit 0
}

# ── 4. Report divergence WITHOUT changing anything ───────────────────────────
current=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
counts=$(git rev-list --left-right --count "origin/$default_branch...HEAD" 2>/dev/null) || exit 0
behind=$(echo "$counts" | awk '{print $1}')
ahead=$(echo "$counts" | awk '{print $2}')

echo "[sync-check] On '$current'. vs origin/$default_branch: ahead $ahead, behind $behind."

if [ "$current" = "$default_branch" ] && { [ "$ahead" -gt 0 ] || [ "$behind" -gt 0 ]; }; then
  echo "[sync-check] ⚠ Local $default_branch has diverged from origin/$default_branch."
  echo "[sync-check]   This hook will NOT auto-reconcile. To sync deliberately:"
  [ "$behind" -gt 0 ] && [ "$ahead" -eq 0 ] && \
    echo "[sync-check]     git merge --ff-only origin/$default_branch   # fast-forward, safe"
  [ "$ahead" -gt 0 ] && \
    echo "[sync-check]     review 'git log origin/$default_branch..HEAD' before discarding"
  echo "[sync-check]   Never 'git reset --hard' unsynced local commits without checking them first."
fi

exit 0
