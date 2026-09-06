#!/usr/bin/env bash
# Keep the Codex entry point without copying the canonical mutation self-test.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
exec bash "$repo_root/.claude/skills/doc-sync/verify.sh" "$@"
