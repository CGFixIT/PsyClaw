#!/usr/bin/env bash
# dep-guard verify — clean-tree pass + mutation self-test.
# Pure stdlib (tomllib); safe to run before any pip install. Exit 0 = healthy.
# A checker that cannot fail proves nothing — the mutation tests keep it honest.
set -uo pipefail

if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys' >/dev/null 2>&1; then
  :
elif command -v python >/dev/null 2>&1 && python -c 'import sys' >/dev/null 2>&1; then
  python3() { python "$@"; }
elif command -v py >/dev/null 2>&1 && py -3 -c 'import sys' >/dev/null 2>&1; then
  python3() { py -3 "$@"; }
else
  echo "a working Python 3 launcher (python3, python, or py -3) is required" >&2
  exit 1
fi

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
sed -i.bak 's/^uvicorn==0.51.0/uvicorn[standard]==0.51.0/' "$b/constraints.txt"
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

# 2d. D8 FAIL: a CI workflow hardcodes a torch version that disagrees with the
# manifest pin (this is the real 2026-07-17 incident, reproduced synthetically).
d="$(_mktree)"
mkdir -p "$d/.github/workflows"
printf 'steps:\n  - run: pip install torch==9.9.9+cpu\n' > "$d/.github/workflows/ci.yml"
out="$(python3 "$checker" --repo-root "$d" 2>&1)"; rc=$?
rm -rf "$d"
if [ "$rc" -ne 2 ] || ! echo "$out" | grep -q "FAIL  \[D8\]"; then
  echo "mutation D (D8 CI torch drift): FAIL — expected exit 2 + D8, got $rc" >&2; echo "$out" >&2; exit 1
fi
echo "mutation D (D8 CI torch drift): PASS (exit 2, D8 reported)"

# 2e. D8 WARN: only .osv-scanner.toml's torch documentation is stale; every CI
# file agrees with the real pin. Comment-only drift never breaks CI -> WARN.
e="$(_mktree)"
mkdir -p "$e/.github/workflows"
real_pin="$(sed -nE 's/^torch==([0-9.]+)\+cpu.*/\1/p' "$repo_root/constraints.txt" | head -n 1)"
[ -n "$real_pin" ] || { echo "mutation E: could not read the torch pin" >&2; rm -rf "$e"; exit 1; }
printf 'steps:\n  - run: pip install torch==%s+cpu\n' "$real_pin" > "$e/.github/workflows/ci.yml"
printf 'reason = "torch 9.9.9+cpu -- stale doc only"\n' > "$e/.osv-scanner.toml"
if ! python3 "$checker" --repo-root "$e" >/tmp/depguard_d8warn.txt 2>&1; then
  echo "mutation E (D8 osv-scanner doc drift): FAIL — a WARN alone must not fail" >&2
  cat /tmp/depguard_d8warn.txt >&2; rm -rf "$e"; exit 1
fi
grep -q "WARN  \[D8\]" /tmp/depguard_d8warn.txt || {
  echo "mutation E: D8 warning not reported" >&2; cat /tmp/depguard_d8warn.txt >&2; rm -rf "$e"; exit 1
}
rm -rf "$e"
echo "mutation E (D8 osv-scanner doc drift): PASS (WARN, exit 0)"

# 2f. D9 FAIL: environment.yml pins a version the pip manifests moved past
# (the real nltk 3.9.4 -> 3.10.0 conda-lane drift, reproduced synthetically).
f="$(_mktree)"
printf 'dependencies:\n  - nltk=0.0.1\n' > "$f/environment.yml"
out="$(python3 "$checker" --repo-root "$f" 2>&1)"; rc=$?
rm -rf "$f"
if [ "$rc" -ne 2 ] || ! echo "$out" | grep -q "FAIL  \[D9\]"; then
  echo "mutation F (D9 environment.yml drift): FAIL — expected exit 2 + D9, got $rc" >&2; echo "$out" >&2; exit 1
fi
echo "mutation F (D9 environment.yml drift): PASS (exit 2, D9 reported)"

# 2g. D6 FAIL: a direct pyproject pin must not disappear from constraints.txt.
g="$(_mktree)"
sed -i.bak '/^httpx==/d' "$g/constraints.txt"
out="$(python3 "$checker" --repo-root "$g" 2>&1)"; rc=$?
rm -rf "$g"
if [ "$rc" -ne 2 ] || ! echo "$out" | grep -q "FAIL  \[D6\]"; then
  echo "mutation G (D6 missing constraint): FAIL - expected exit 2 + D6, got $rc" >&2; echo "$out" >&2; exit 1
fi
echo "mutation G (D6 missing constraint): PASS (exit 2, D6 reported)"

# 2h. D8 FAIL: Docker's fallback torch pin is as load-bearing as CI's copies.
h="$(_mktree)"
printf 'RUN pip install --no-cache-dir torch==9.9.9+cpu --index-url https://download.pytorch.org/whl/cpu\n' > "$h/Dockerfile"
out="$(python3 "$checker" --repo-root "$h" 2>&1)"; rc=$?
rm -rf "$h"
if [ "$rc" -ne 2 ] || ! echo "$out" | grep -q "FAIL  \[D8\]"; then
  echo "mutation H (D8 Docker torch drift): FAIL - expected exit 2 + D8, got $rc" >&2; echo "$out" >&2; exit 1
fi
echo "mutation H (D8 Docker torch drift): PASS (exit 2, D8 reported)"

echo "== dep-guard verify: OK =="
