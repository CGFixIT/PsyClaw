#!/usr/bin/env bash
# doc-sync verify — the checker runs, and it actually detects injected drift.
# Drift on the live tree is EXPECTED (docs lag code); this does not fail on it.
# It fails only if the checker errors (exit 3) or cannot detect a planted drift.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"
checker="$here/doc_sync.py"

echo "== doc-sync verify =="

if python3 -c "import yaml" 2>/dev/null; then
  PY=python3
elif python -c "import yaml" 2>/dev/null; then
  PY=python
else
  echo "SKIP: PyYAML not importable; install project deps first." >&2
  exit 0
fi

# 1. The checker must run without an env error (exit 0 or 2, not 3).
"$PY" "$checker" --repo-root "$repo_root" >/tmp/docsync_live.txt 2>&1
rc=$?
if [ "$rc" -eq 3 ]; then
  echo "checker errored (exit 3):" >&2; cat /tmp/docsync_live.txt >&2; exit 1
fi
echo "checker ran on live tree (exit $rc; drift on live tree is expected)"

# 2. Detection self-test: build a temp tree whose CLAUDE.md omits a real skill
#    and a real route, and confirm the checker flags drift (exit 2).
#
#    The assertion is per-check, not a bare "exit != 0". The stub tree used to
#    omit setup-guide.md, docs/ and macos/, so D7's "could not read M5 doctrine
#    inputs" alone satisfied a bare non-zero exit -- D1 and D5 detection could
#    have been completely broken and this test would still have passed. It now
#    copies the route modules and setup-guide.md and asserts on the specific
#    DRIFT [D1] and DRIFT [D5] strings, both directions of D5 included.
tmp="$(mktemp -d)"
d7tmp=""
trap 'rm -rf "$tmp" "$d7tmp"' EXIT
cp "$repo_root"/config.yaml "$repo_root"/pyproject.toml "$repo_root"/gate.py "$tmp"/
for extra in gate_ops.py gate_auth.py gate_memory.py graph.py; do
  [ -f "$repo_root/$extra" ] && cp "$repo_root/$extra" "$tmp"/
done
mkdir -p "$tmp/.claude" "$tmp/harness"
cp "$repo_root"/.claude/settings.json "$tmp/.claude/"
cp -r "$repo_root"/.claude/skills "$tmp/.claude/"
for h in server.py agent_routes.py auth_routes.py; do
  [ -f "$repo_root/harness/$h" ] && cp "$repo_root/harness/$h" "$tmp/harness/"
done
# A CLAUDE.md that mentions almost nothing => guaranteed D1/D5 drift.
printf '# CLAUDE.md\n\nMinimal stub with no skills table and no route list.\n' > "$tmp/CLAUDE.md"
# setup-guide.md claiming a route that does not exist => the OTHER direction of
# D5 (phantom), which the old stub tree never exercised because the file was
# absent and the cross-check short-circuited to "skipped".
printf '# setup-guide\n\n## REST API\n\n`/definitely/not/a/real/route`\n' > "$tmp/setup-guide.md"

stub_out="$tmp/_out.txt"
"$PY" "$checker" --repo-root "$tmp" > "$stub_out" 2>&1 && {
  echo "detection self-test: FAIL — checker found no drift in a stub CLAUDE.md" >&2
  cat "$stub_out" >&2; exit 1
}
for want in "DRIFT [D1]" "DRIFT [D5]"; do
  grep -qF "$want" "$stub_out" || {
    echo "detection self-test: FAIL — planted drift did not produce '$want'" >&2
    cat "$stub_out" >&2; exit 1
  }
done
grep -qF "definitely/not/a/real/route" "$stub_out" || {
  echo "detection self-test: FAIL — D5 phantom-route direction not exercised" >&2
  cat "$stub_out" >&2; exit 1
}
echo "detection self-test: PASS (D1 + D5 both directions detected by name, exit 2)"

# 3. D7 key-adjacency: an unrelated 8000 must not green max_context_tokens.
#    Plant a doctrine that cites every shipped value next to its key, then
#    rewrite the RAG row so max_context_tokens is stale while an unrelated
#    "8000 chars" remains elsewhere — whole-doc substring would still pass.
d7tmp="$(mktemp -d)"
cp "$repo_root"/config.yaml "$repo_root"/pyproject.toml "$repo_root"/gate.py "$d7tmp"/
mkdir -p "$d7tmp/.claude" "$d7tmp/docs" "$d7tmp/macos"
cp "$repo_root"/.claude/settings.json "$d7tmp/.claude/"
cp -r "$repo_root"/.claude/skills "$d7tmp/.claude/"
cp "$repo_root"/macos/ollama-mlx.env "$d7tmp/macos/"
# Minimal CLAUDE.md so D1/D5 still drift; D7 is what we assert on.
printf '# CLAUDE.md\n\nMinimal stub.\n' > "$d7tmp/CLAUDE.md"
# Shipped values from config.yaml / ollama-mlx.env (read live so retunes stay honest).
read -r model max_tok llm_to graph_to max_ctx ollama_ctx < <(
  "$PY" - "$repo_root/config.yaml" "$repo_root/macos/ollama-mlx.env" <<'PY'
import re, sys, yaml
cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
env = open(sys.argv[2], encoding="utf-8").read()
m = re.search(r"(?m)^OLLAMA_CONTEXT_LENGTH=(\d+)$", env)
assert m is not None
llm = cfg["models"]["local_llm"]
print(
    llm["model"],
    llm["max_tokens"],
    llm["timeout_sec"],
    cfg["api"]["graph_timeout_sec"],
    cfg["retrieval"]["max_context_tokens"],
    m.group(1),
)
PY
)

cat > "$d7tmp/docs/m5-48gb-coding-expectations.md" <<EOF
# M5 fixture

| Local model | \`$model\` |
| RAG budget | \`max_context_tokens\` $max_ctx + \`max_tokens\` $max_tok |
| Query deadlines | **${llm_to}s** local LLM timeout; **${graph_to}s** graph timeout |
| Ollama | OLLAMA_CONTEXT_LENGTH $ollama_ctx |
EOF

# Positive: adjacent citations → D7 ok (other checks may still drift).
d7_ok_out="$("$PY" "$checker" --repo-root "$d7tmp" 2>&1 || true)"
if ! printf '%s\n' "$d7_ok_out" | grep -q 'ok    \[D7\]'; then
  echo "D7 adjacency self-test: FAIL — expected D7 ok on key-adjacent fixture:" >&2
  printf '%s\n' "$d7_ok_out" >&2
  exit 1
fi
echo "D7 adjacency positive: PASS"

# Negative: stale max_context_tokens, unrelated 8000 left in the file.
cat > "$d7tmp/docs/m5-48gb-coding-expectations.md" <<EOF
# M5 fixture

| Local model | \`$model\` |
| RAG budget | \`max_context_tokens\` 1234 + \`max_tokens\` $max_tok |
| Query deadlines | **${llm_to}s** local LLM timeout; **${graph_to}s** graph timeout |
| Ollama | OLLAMA_CONTEXT_LENGTH $ollama_ctx |
| Agentic | unrelated **${max_ctx}** chars of GitHub context elsewhere
EOF

d7_bad_out="$("$PY" "$checker" --repo-root "$d7tmp" 2>&1 || true)"
if ! printf '%s\n' "$d7_bad_out" | grep -q "retrieval.max_context_tokens=${max_ctx}"; then
  echo "D7 adjacency self-test: FAIL — unrelated ${max_ctx} greened max_context_tokens:" >&2
  printf '%s\n' "$d7_bad_out" >&2
  exit 1
fi
echo "D7 adjacency negative: PASS (unrelated ${max_ctx} did not green max_context_tokens)"

echo "== doc-sync verify: OK =="
