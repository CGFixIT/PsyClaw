#!/usr/bin/env bash
# dep-guard verify — clean-tree pass + mutation self-test.
# Pure stdlib (tomllib); safe to run before any pip install. Exit 0 = healthy.
# A checker that cannot fail proves nothing — the mutation tests keep it honest.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"
checker="$here/check_deps.py"

echo "== dep-guard verify =="

# 1. Clean tree must pass (exit 0).
if python3 "$checker" --repo-root "$repo_root" >/tmp/depguard_live.txt 2>&1; then
  echo "clean tree: PASS (exit 0)"
else
  echo "clean tree: FAIL — the shipped pins violate an invariant" >&2
  cat /tmp/depguard_live.txt >&2
  exit 1
fi

# Helper: fresh temp with both pin files, so the checker has what it needs.
_mktree() {
  local d; d="$(mktemp -d)"
  cp "$repo_root/pyproject.toml" "$repo_root/constraints.txt" "$d/"
  echo "$d"
}

# 2a. D2 FAIL: float numpy to 2.x in BOTH files (keeps D6 consistent).
a="$(_mktree)"
sed -i.bak 's/numpy==1.26.4/numpy==2.0.0/' "$a/pyproject.toml" "$a/constraints.txt"
out="$(python3 "$checker" --repo-root "$a" 2>&1)"; rc=$?
rm -rf "$a"
if [ "$rc" -ne 2 ] || ! echo "$out" | grep -q "FAIL  \[D2\]"; then
  echo "mutation A (D2 numpy<2): FAIL — expected exit 2 + D2, got $rc" >&2; echo "$out" >&2; exit 1
fi
echo "mutation A (D2 numpy<2): PASS (exit 2, D2 reported)"

# 2b. D4 FAIL: give constraints.txt uvicorn a [standard] extra.
b="$(_mktree)"
sed -i.bak 's/^uvicorn==0.49.0/uvicorn[standard]==0.49.0/' "$b/constraints.txt"
out="$(python3 "$checker" --repo-root "$b" 2>&1)"; rc=$?
rm -rf "$b"
if [ "$rc" -ne 2 ] || ! echo "$out" | grep -q "FAIL  \[D4\]"; then
  echo "mutation B (D4 uvicorn extra): FAIL — expected exit 2 + D4, got $rc" >&2; echo "$out" >&2; exit 1
fi
echo "mutation B (D4 uvicorn extra): PASS (exit 2, D4 reported)"

# 2c. D1 WARN: drift pydantic-core out of the documented lock-step.
c="$(_mktree)"
sed -i.bak 's/pydantic-core==2.46.4/pydantic-core==2.47.0/' "$c/constraints.txt"
if ! python3 "$checker" --repo-root "$c" >/tmp/depguard_warn.txt 2>&1; then
  echo "mutation C (D1): FAIL — a WARN alone must not fail (expected exit 0)" >&2
  cat /tmp/depguard_warn.txt >&2; rm -rf "$c"; exit 1
fi
grep -q "WARN  \[D1\]" /tmp/depguard_warn.txt || { echo "mutation C: D1 warning not reported" >&2; rm -rf "$c"; exit 1; }
out="$(python3 "$checker" --repo-root "$c" --strict 2>&1)"; rc=$?
rm -rf "$c"
if [ "$rc" -ne 2 ]; then
  echo "mutation C (D1 --strict): FAIL — expected exit 2 under --strict, got $rc" >&2; echo "$out" >&2; exit 1
fi
echo "mutation C (D1 lock-step drift): PASS (WARN=exit 0, --strict=exit 2)"

echo "== dep-guard verify: OK =="
