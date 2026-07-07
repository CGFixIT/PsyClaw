#!/usr/bin/env bash
# invariant-guard verify — clean-tree pass + mutation self-test.
# Stdlib-only; safe to run before any pip install. Exit 0 = healthy.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"
checker="$here/check_invariants.py"

echo "== invariant-guard verify =="

# 1. Clean tree must pass (exit 0).
if python3 "$checker" --repo-root "$repo_root"; then
  echo "clean tree: PASS"
else
  echo "clean tree: FAIL — an invariant is violated on the current tree" >&2
  exit 1
fi

# 2. Mutation self-test: the checker must FAIL (exit 2) on a broken tree.
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

cp "$repo_root"/gate.py "$repo_root"/graph.py "$repo_root"/mcp_hybrid_server.py \
   "$repo_root"/config.yaml "$tmp"/
cp -r "$repo_root"/utils "$repo_root"/agentic "$repo_root"/sync \
      "$repo_root"/guardrails "$tmp"/ 2>/dev/null || true

# Violation A: core module imports an out-of-band package (breaks I6).
sed -i.bak 's/^import hmac/import hmac\nimport agentic/' "$tmp/gate.py"
# Violation B: sever the grok_fallback -> audit_logger edge (breaks I4).
sed -i.bak 's/graph.add_edge("grok_fallback", "audit_logger")/pass  # severed/' "$tmp/graph.py"

out="$(python3 "$checker" --repo-root "$tmp" 2>&1)"
rc=$?
if [ "$rc" -ne 2 ]; then
  echo "mutation test: FAIL — expected exit 2 on broken tree, got $rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "$out" | grep -q "gate.py imports none" || { echo "mutation test: import violation not detected" >&2; exit 1; }
echo "$out" | grep -q "reach audit_logger"   || { echo "mutation test: severed edge not detected" >&2; exit 1; }
echo "mutation test: PASS (both injected violations detected, exit 2)"

echo "== invariant-guard verify: OK =="
