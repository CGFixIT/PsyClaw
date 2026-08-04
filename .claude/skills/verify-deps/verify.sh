#!/usr/bin/env bash
# verify-deps verify — clean-tree pass + mutation self-test for verify-deps.
# Pure stdlib; safe to run before any pip install. Exit 0 = healthy.
# It covers requirements.txt <-> constraints.txt plus environment-only install
# contracts; dep-guard's own verify.sh covers pyproject/constraints mutations.
set -uo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  command -v python >/dev/null 2>&1 || { echo "python3 or python is required" >&2; exit 1; }
  python3() { python "$@"; }
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"
extractor="$here/extract_pins.py"

echo "== verify-deps verify =="

# 1. Clean tree must run cleanly (exit 0) and report no requirements.txt drift.
out="$(python3 "$extractor" --repo-root "$repo_root" 2>&1)"; rc=$?
if [ "$rc" -ne 0 ]; then
  echo "clean tree: FAIL — expected exit 0, got $rc" >&2
  echo "$out" >&2
  exit 1
fi
if ! echo "$out" | grep -q "no drift"; then
  echo "clean tree: FAIL — shipped requirements.txt/constraints.txt disagree" >&2
  echo "$out" >&2
  exit 1
fi
echo "clean tree: PASS (exit 0, no requirements.txt drift)"

# 2. Mutation: drift requirements.txt's httpx pin away from constraints.txt.
_mktree() {
  local d; d="$(mktemp -d)"
  cp "$repo_root/pyproject.toml" "$repo_root/constraints.txt" "$repo_root/requirements.txt" "$d/"
  echo "$d"
}
a="$(_mktree)"
sed -i.bak 's/^httpx==0.28.1/httpx==0.20.0/' "$a/requirements.txt"
out="$(python3 "$extractor" --repo-root "$a" 2>&1)"; rc=$?
rm -rf "$a"
if [ "$rc" -ne 0 ] || ! echo "$out" | grep -q "DRIFT  httpx: requirements.txt==0.20.0 vs constraints.txt==0.28.1"; then
  echo "mutation (requirements.txt drift): FAIL — expected exit 0 + DRIFT line, got rc=$rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "mutation (requirements.txt drift): PASS (DRIFT reported, reporting-only so exit stays 0)"

# 3. Missing pin files must fail closed (exit 3), matching the repo convention.
b="$(mktemp -d)"
out="$(python3 "$extractor" --repo-root "$b" 2>&1)"; rc=$?
rm -rf "$b"
if [ "$rc" -ne 3 ]; then
  echo "missing pin files: FAIL — expected exit 3, got $rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "missing pin files: PASS (exit 3)"

# --- check_env_drift.py: the non-manifest drift surfaces --------------------
drift="$here/check_env_drift.py"

# 4. Clean tree: no FAILures. Warnings are expected and allowed (huggingface_hub
#    and starlette are known-undeclared hard transitives), so this asserts the
#    exit code, not a warning count that would rot on the next transitive.
out="$(python3 "$drift" --repo-root "$repo_root" 2>&1)"; rc=$?
if [ "$rc" -ne 0 ]; then
  echo "env drift clean tree: FAIL — expected exit 0, got $rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "env drift clean tree: PASS (exit 0, no E1/E2/E4 failures)"

# 5. Mutation E1: the same tool pinned at two versions in two workflow files.
#    This is the drift class nothing else in the repo can see.
c="$(mktemp -d)"
mkdir -p "$c/.github/workflows"
printf 'jobs:\n  a:\n    steps:\n      - run: pip install ruff==0.15.20\n' > "$c/.github/workflows/one.yml"
printf 'jobs:\n  b:\n    steps:\n      - run: pip install ruff==0.14.0\n' > "$c/.github/workflows/two.yml"
out="$(python3 "$drift" --repo-root "$c" 2>&1)"; rc=$?
rm -rf "$c"
if [ "$rc" -ne 2 ] || ! echo "$out" | grep -q "ruff pinned at 2 different versions"; then
  echo "env drift mutation (E1 split tool pin): FAIL — expected exit 2 + E1 line, got rc=$rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "env drift mutation (E1 split tool pin): PASS (exit 2)"

# 6. Mutation E4: an extras-only package leaking into requirements.txt, which is
#    the BASE surface. Also asserts the comment-vs-requirement distinction —
#    requirements.txt mentions extras in prose and must not trip on that.
d="$(mktemp -d)"
printf '# nemoguardrails lives in the guardrails extra, not here\nhttpx==0.28.1\n' > "$d/requirements.txt"
printf 'COPY pyproject.toml constraints.txt requirements.txt ./\nRUN uv pip install --system --no-cache-dir -r requirements.txt -c constraints.txt || ( pip install --no-cache-dir torch==1 --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -r requirements.txt -c constraints.txt )\n' > "$d/Dockerfile"
out="$(python3 "$drift" --repo-root "$d" 2>&1)"; rc=$?
if [ "$rc" -ne 0 ]; then
  echo "env drift mutation (E4 comment is not an install): FAIL — a commented package must not trip E4, got rc=$rc" >&2
  echo "$out" >&2; rm -rf "$d"; exit 1
fi
printf 'nemoguardrails==0.19.0\n' >> "$d/requirements.txt"
out="$(python3 "$drift" --repo-root "$d" 2>&1)"; rc=$?
rm -rf "$d"
if [ "$rc" -ne 2 ] || ! echo "$out" | grep -q "installs extras-only package"; then
  echo "env drift mutation (E4 extras leak): FAIL — expected exit 2 + E4 line, got rc=$rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "env drift mutation (E4 extras leak): PASS (exit 2; commented mention correctly ignored)"

# 7. --strict must reject a requirements pin omitted from constraints.txt.
e="$(_mktree)"
sed -i.bak '/^httpx==/d' "$e/constraints.txt"
out="$(python3 "$extractor" --repo-root "$e" --strict 2>&1)"; rc=$?
rm -rf "$e"
if [ "$rc" -ne 2 ] || ! echo "$out" | grep -q "DRIFT  httpx: requirements.txt==0.28.1 missing from constraints.txt"; then
  echo "strict mutation (requirements missing constraint): FAIL - expected exit 2 + DRIFT line, got rc=$rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "strict mutation (requirements missing constraint): PASS (exit 2)"

# 8. The clean tree must also satisfy strict import/environment checks.
out="$(python3 "$drift" --repo-root "$repo_root" --strict 2>&1)"; rc=$?
if [ "$rc" -ne 0 ]; then
  echo "strict environment clean tree: FAIL - expected exit 0, got $rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "strict environment clean tree: PASS (exit 0)"

# 9. Docker must retain both constrained install paths and CPU-wheel routing.
f="$(mktemp -d)"
printf 'COPY pyproject.toml constraints.txt requirements.txt ./\nRUN uv pip install --system --no-cache-dir -r requirements.txt -c constraints.txt\n' > "$f/Dockerfile"
out="$(python3 "$drift" --repo-root "$f" 2>&1)"; rc=$?
rm -rf "$f"
if [ "$rc" -ne 2 ] || ! echo "$out" | grep -q "FAIL  \[E5\]"; then
  echo "environment mutation (E5 Docker contract): FAIL - expected exit 2 + E5 line, got rc=$rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "environment mutation (E5 Docker contract): PASS (exit 2)"

echo "== verify-deps verify: OK =="
