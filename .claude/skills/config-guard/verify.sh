#!/usr/bin/env bash
# config-guard verify — clean-tree pass + mutation self-test.
# Needs PyYAML (nested config parsing); SKIPs cleanly (exit 0) without it so a
# fresh pre-install container does not fail CI. A checker that cannot fail proves
# nothing — the mutation test keeps it honest.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"
checker="$here/check_config.py"

echo "== config-guard verify =="

if ! python3 -c "import yaml" 2>/dev/null; then
  echo "SKIP: PyYAML not importable; install project deps first." >&2
  exit 0
fi

# 1. Clean tree must pass (exit 0).
if python3 "$checker" --repo-root "$repo_root" >/tmp/cfgguard_live.txt 2>&1; then
  echo "clean tree: PASS (exit 0)"
else
  echo "clean tree: FAIL — the shipped config.yaml violates the contract" >&2
  cat /tmp/cfgguard_live.txt >&2
  exit 1
fi

# 2a. FAIL-path mutation: break the graph/LLM timeout relation (C2).
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp "$repo_root/config.yaml" "$tmp/config.yaml"
sed -i.bak 's/graph_timeout_sec: 330/graph_timeout_sec: 200/' "$tmp/config.yaml"

out="$(python3 "$checker" --repo-root "$tmp" 2>&1)"; rc=$?
if [ "$rc" -ne 2 ]; then
  echo "mutation A (C2): FAIL — expected exit 2 on graph_timeout < llm_timeout, got $rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "$out" | grep -q "FAIL  \[C2\]" || { echo "mutation A: C2 violation not reported" >&2; exit 1; }
echo "mutation A (C2 timeout relation): PASS (exit 2, C2 reported)"

# 2b. WARN semantics: a cosine-scale min_score is a WARN (exit 0) by default and
#     a failure only under --strict (C7 — the RRF-scale trap).
tmp2="$(mktemp -d)"
trap 'rm -rf "$tmp" "$tmp2"' EXIT
cp "$repo_root/config.yaml" "$tmp2/config.yaml"
sed -i.bak 's/min_score: 0.028/min_score: 0.5/' "$tmp2/config.yaml"

if ! python3 "$checker" --repo-root "$tmp2" >/tmp/cfgguard_warn.txt 2>&1; then
  echo "mutation B (C7): FAIL — a WARN alone must not fail (expected exit 0)" >&2
  cat /tmp/cfgguard_warn.txt >&2
  exit 1
fi
grep -q "WARN  \[C7\]" /tmp/cfgguard_warn.txt || { echo "mutation B: C7 warning not reported" >&2; exit 1; }
out="$(python3 "$checker" --repo-root "$tmp2" --strict 2>&1)"; rc=$?
if [ "$rc" -ne 2 ]; then
  echo "mutation B (C7 --strict): FAIL — expected exit 2 under --strict, got $rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "mutation B (C7 RRF-scale trap): PASS (WARN=exit 0, --strict=exit 2)"

echo "== config-guard verify: OK =="
