#!/usr/bin/env bash
# Wire this clone to repo-managed hooks under .githooks/
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
chmod +x .githooks/pre-commit .githooks/pre-push .githooks/commit-msg
chmod +x scripts/check-pr-template.sh 2>/dev/null || true
git config core.hooksPath .githooks
echo "core.hooksPath=$(git config core.hooksPath)"
echo "Installed CyClaw git hooks:"
echo "  pre-commit  — branch naming allowlist"
echo "  pre-push    — branch naming + fresh origin/main ancestry"
echo "  commit-msg  — PR template title prefix [prefix] - subject"
echo "Also available: scripts/check-pr-template.sh (PR body sections before create)."
