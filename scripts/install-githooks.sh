#!/usr/bin/env bash
# Wire this clone to repo-managed hooks under .githooks/
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
chmod +x .githooks/pre-commit .githooks/pre-push
git config core.hooksPath .githooks
echo "core.hooksPath=$(git config core.hooksPath)"
echo "Installed CyClaw git hooks (grok/<feature> branch naming)."
