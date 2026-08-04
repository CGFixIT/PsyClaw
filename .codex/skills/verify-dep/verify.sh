#!/usr/bin/env bash
# Reuse the canonical mutation-tested dependency checkers; do not fork them.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

bash "$repo_root/.claude/skills/dep-guard/verify.sh"
bash "$repo_root/.claude/skills/verify-deps/verify.sh"
