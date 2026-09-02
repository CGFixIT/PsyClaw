#!/usr/bin/env bash
# verify-deps verify — clean-tree pass + mutation self-test for verify-deps.
# Pure stdlib; safe to run before any pip install. Exit 0 = healthy.
# It covers requirements.txt + requirements-test.txt <-> constraints.txt plus
# environment-only install contracts; dep-guard's own verify.sh covers
# pyproject/constraints mutations.
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
extractor="$here/extract_pins.py"

echo "== verify-deps verify =="

# 1. Clean tree must run cleanly (exit 0) and report no requirements*.txt drift.
out="$(python3 "$extractor" --repo-root "$repo_root" 2>&1)"; rc=$?
if [ "$rc" -ne 0 ]; then
  echo "clean tree: FAIL — expected exit 0, got $rc" >&2
  echo "$out" >&2
  exit 1
fi
if ! echo "$out" | grep -q "no drift"; then
  echo "clean tree: FAIL — shipped requirements*.txt/constraints.txt disagree" >&2
  echo "$out" >&2
  exit 1
fi
echo "clean tree: PASS (exit 0, no requirements.txt drift)"

# 2. Mutation: drift requirements.txt's httpx pin away from constraints.txt.
_mktree() {
  local d; d="$(mktemp -d)"
  cp "$repo_root/pyproject.toml" "$repo_root/constraints.txt" "$repo_root/requirements.txt" "$repo_root/requirements-test.txt" "$d/"
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

# 2b. Mutation: drift requirements-test.txt's pytest pin away from constraints.txt.
a2="$(_mktree)"
sed -i.bak 's/^pytest==9.1.1/pytest==8.0.0/' "$a2/requirements-test.txt"
out="$(python3 "$extractor" --repo-root "$a2" 2>&1)"; rc=$?
rm -rf "$a2"
if [ "$rc" -ne 0 ] || ! echo "$out" | grep -q "DRIFT  pytest: requirements-test.txt==8.0.0 vs constraints.txt==9.1.1"; then
  echo "mutation (requirements-test.txt drift): FAIL — expected exit 0 + DRIFT line, got rc=$rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "mutation (requirements-test.txt drift): PASS (DRIFT reported, reporting-only so exit stays 0)"

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

# 4. Clean tree: no FAILures. Asserts the exit code, not a warning count: a
#    clean tree reports zero E3 warnings today, but a future transitive that
#    goes undeclared should surface as a warning to read, not fail this test.
out="$(python3 "$drift" --repo-root "$repo_root" 2>&1)"; rc=$?
if [ "$rc" -ne 0 ]; then
  echo "env drift clean tree: FAIL — expected exit 0, got $rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "env drift clean tree: PASS (exit 0, no E1/E2/E4/E5/E6 failures)"

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
printf 'pytest==9.1.1\n' > "$d/requirements-test.txt"
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

# --- The Docker surface beyond the Dockerfile (E5 torch lock-step + E6) ------
# A pin-manifest tree plus the four Docker-surface files, so E5/E6 run against
# the real shipped shapes. Each mutation below edits one file the way a real
# PR would and must produce exit 2 plus the named check's line.
_mkdockertree() {
  local d; d="$(_mktree)"
  mkdir -p "$d/.github/workflows"
  cp "$repo_root/Dockerfile" "$repo_root/docker-compose.yml" "$repo_root/.dockerignore" "$d/"
  cp "$repo_root/.github/workflows/publish-ghcr.yml" "$d/.github/workflows/"
  echo "$d"
}
_expect_docker_fail() {
  # $1 tree  $2 label  $3 grep pattern the failure line must carry
  local out rc
  out="$(python3 "$drift" --repo-root "$1" 2>&1)"; rc=$?
  rm -rf "$1"
  if [ "$rc" -ne 2 ] || ! echo "$out" | grep -q "$3"; then
    echo "environment mutation ($2): FAIL - expected exit 2 + '$3', got rc=$rc" >&2
    echo "$out" >&2
    exit 1
  fi
  echo "environment mutation ($2): PASS (exit 2)"
}

# 10. Negative control: the shipped Docker surface, copied whole, is clean.
g="$(_mkdockertree)"
out="$(python3 "$drift" --repo-root "$g" 2>&1)"; rc=$?
rm -rf "$g"
if [ "$rc" -ne 0 ] || ! echo "$out" | grep -q "ok    \[E6\] publish-ghcr.yml pushes the image"; then
  echo "docker surface clean copy: FAIL - expected exit 0 + E6 ok lines, got rc=$rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "docker surface clean copy: PASS (exit 0)"

# 11. E5: the fallback torch pre-install lags constraints.txt -- the exact miss
#     the Dockerfile's own comment records (2.12.1 -> 2.13.0).
h="$(_mkdockertree)"
sed -i.bak 's/torch==[^ ]* --index-url/torch==0.0.0+cpu --index-url/' "$h/Dockerfile"
_expect_docker_fail "$h" "E5 torch lock-step" "keep the two in lock-step"

# 12. E6: .dockerignore swallows a manifest the build stage COPYs.
i="$(_mkdockertree)"
printf 'constraints.txt\n' >> "$i/.dockerignore"
_expect_docker_fail "$i" "E6 ignored manifest" "which the Dockerfile COPYs"

# 13. E6: .dockerignore stops excluding the index -- private vectors would bake
#     into a published image.
j="$(_mkdockertree)"
sed -i.bak '/^index\/$/d' "$j/.dockerignore"
_expect_docker_fail "$j" "E6 runtime state baked in" "no longer excludes runtime state"

# 14. E6: the host publish leaves loopback.
k="$(_mkdockertree)"
sed -i.bak 's/"127\.0\.0\.1:/"0.0.0.0:/' "$k/docker-compose.yml"
_expect_docker_fail "$k" "E6 non-loopback publish" "host exposure must stay"

# 15. E6: compose drops the ./index mount (503 INDEX_NOT_FOUND, healthcheck green).
l="$(_mkdockertree)"
sed -i.bak '/^[[:space:]]*- \.\/index:/d' "$l/docker-compose.yml"
_expect_docker_fail "$l" "E6 unmounted runtime state" "mounts nothing at /app/{index}"

# 16. E6: a version bump that skips the compose default tag.
m="$(_mkdockertree)"
sed -i.bak 's/:-[0-9][^}]*}/:-0.0.0}/' "$m/docker-compose.yml"
_expect_docker_fail "$m" "E6 stale image tag" "default CYCLAW_IMAGE_TAG is 0.0.0"

# 17. E6: the publish workflow pushes an image compose never pulls.
n="$(_mkdockertree)"
sed -i.bak 's#^  IMAGE_NAME: .*#  IMAGE_NAME: ghcr.io/someone-else/cyclaw#' "$n/.github/workflows/publish-ghcr.yml"
_expect_docker_fail "$n" "E6 registry name split" "pushes ghcr.io/someone-else/cyclaw but docker-compose.yml pulls"

echo "== verify-deps verify: OK =="
