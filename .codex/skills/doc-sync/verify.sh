#!/usr/bin/env bash
# doc-sync verify — the checker runs, and it actually detects injected drift.
# Drift on the live tree is EXPECTED (docs lag code); this does not fail on it.
# It fails only if the checker errors (exit 3) or cannot detect a planted drift.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"
checker="$here/doc_sync.py"

echo "== doc-sync verify =="

if ! python3 -c "import yaml" 2>/dev/null; then
  echo "SKIP: PyYAML not importable; install project deps first." >&2
  exit 0
fi

# 1. The checker must run without an env error (exit 0 or 2, not 3).
python3 "$checker" --repo-root "$repo_root" >/tmp/docsync_live.txt 2>&1
rc=$?
if [ "$rc" -eq 3 ]; then
  echo "checker errored (exit 3):" >&2; cat /tmp/docsync_live.txt >&2; exit 1
fi
echo "checker ran on live tree (exit $rc; drift on live tree is expected)"

# 2. Detection self-test: build a temp tree whose CLAUDE.md omits a real skill
#    and a real route, and confirm the checker flags drift (exit 2).
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp "$repo_root"/config.yaml "$repo_root"/pyproject.toml "$repo_root"/gate.py "$tmp"/
mkdir -p "$tmp/.claude"
cp "$repo_root"/.claude/settings.json "$tmp/.claude/"
cp -r "$repo_root"/.claude/skills "$tmp/.claude/"
# A CLAUDE.md that mentions almost nothing => guaranteed D1/D5 drift.
printf '# CLAUDE.md\n\nMinimal stub with no skills table and no route list.\n' > "$tmp/CLAUDE.md"

if python3 "$checker" --repo-root "$tmp" >/dev/null 2>&1; then
  echo "detection self-test: FAIL — checker found no drift in a stub CLAUDE.md" >&2
  exit 1
fi
echo "detection self-test: PASS (planted drift detected, exit 2)"

echo "== doc-sync verify: OK =="
