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

# Derive a fixture value for D8 that is guaranteed stale, rather than a
# hardcoded "10" -- if graph.py legitimately reaches 10 add_node() calls one
# day, a hardcoded 10 stops being stale and the DRIFT [D8] assertion below
# would fail even on a correctly working checker (Codex P2 on PR #1308).
# AST-based, matching D8's own counting logic exactly -- a textual grep for
# ".add_node(" is fooled by a call written as "graph.add_node (...)" (extra
# space) or by comments/strings, which D8's ast.walk() correctly ignores or
# still counts differently (Codex P2 on PR #1308: a grep-derived count can
# equal the real AST count even when one is off, defeating this fixture).
real_node_count=$("$PY" -c "
import ast
tree = ast.parse(open('$repo_root/graph.py', encoding='utf-8').read())
print(sum(
    1 for node in ast.walk(tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == 'add_node'
))
")
stale_node_count=$((real_node_count + 1))
mkdir -p "$tmp/.claude" "$tmp/harness"
cp "$repo_root"/.claude/settings.json "$tmp/.claude/"
cp -r "$repo_root"/.claude/skills "$tmp/.claude/"
for h in server.py agent_routes.py auth_routes.py; do
  [ -f "$repo_root/harness/$h" ] && cp "$repo_root/harness/$h" "$tmp/harness/"
done
# A CLAUDE.md that mentions almost nothing => guaranteed D1/D5 drift. The planted
# stale node-count claim is D8's own fixture -- copying graph.py into $tmp (above)
# without ever planting a stale claim against it meant this self-test never
# exercised D8 at all. Confirmed by Codex reviewing PR #1308: deleting the entire
# D8 block from doc_sync.py still left this self-test reporting PASS, exit 0.
printf '# CLAUDE.md\n\nMinimal stub with no skills table and no route list.\n\nStale claim: the graph is a %s-node topology.\n\nThe stop hook blocks force-push, if applied by the session runtime.\n' "$stale_node_count" > "$tmp/CLAUDE.md"
mkdir -p "$tmp/.claude/rules"
printf 'The stop hook blocks --force-with-lease.\n' > "$tmp/.claude/rules/PROJECT_RULES.md"
# setup-guide.md claiming a route that does not exist => the OTHER direction of
# D5 (phantom), which the old stub tree never exercised because the file was
# absent and the cross-check short-circuited to "skipped".
printf '# setup-guide\n\n## REST API\n\n`/definitely/not/a/real/route`\n' > "$tmp/setup-guide.md"

stub_out="$tmp/_out.txt"
"$PY" "$checker" --repo-root "$tmp" > "$stub_out" 2>&1; stub_rc=$?
# Require exactly 2 (drift found), not merely nonzero. A checker that crashes
# (e.g. an unhandled exception reading a missing input) exits 1 -- letting that
# masquerade as "drift detected" is the exact hole a prior version of this
# self-test had on D7 (fixed above) and D8 (doc_sync.py, fixed after Codex
# found it crashed on any tree without graph.py).
if [ "$stub_rc" -ne 2 ]; then
  echo "detection self-test: FAIL — checker exited $stub_rc (expected 2); it either" >&2
  echo "  found no drift or crashed instead of detecting it:" >&2
  cat "$stub_out" >&2; exit 1
fi
for want in "DRIFT [D1]" "DRIFT [D5]"; do
  grep -qF "$want" "$stub_out" || {
    echo "detection self-test: FAIL — planted drift did not produce '$want'" >&2
    cat "$stub_out" >&2; exit 1
  }
done
# D5 fires for two independent reasons in this fixture: routes gate.py has that
# the stub setup-guide.md never mentions (undocumented), and the one phantom
# route the stub setup-guide.md invents (phantom). Only asserting the phantom
# text left the undocumented branch free to break silently -- disabling
# doc_sync.py's `if undocumented:` block would still satisfy every assertion
# above and report PASS (Codex P2 on PR #1308).
grep -qF "definitely/not/a/real/route" "$stub_out" || {
  echo "detection self-test: FAIL — D5 phantom-route direction not exercised" >&2
  cat "$stub_out" >&2; exit 1
}
if ! grep -qF "missing from setup-guide.md's REST section" "$stub_out" || ! grep -qF "/health" "$stub_out"; then
  echo "detection self-test: FAIL — D5 undocumented-route direction not exercised" >&2
  cat "$stub_out" >&2; exit 1
fi
# D8's own positive case: the stub CLAUDE.md claims 10-node against a graph.py
# (copied from the real repo above) that has 12 add_node() calls.
if ! grep -qF "DRIFT [D8]" "$stub_out"; then
  echo "detection self-test: FAIL — D8 node-count drift not detected" >&2
  cat "$stub_out" >&2; exit 1
fi
# D6's own positive+negative case: the planted PROJECT_RULES.md sentence
# ("The stop hook blocks --force-with-lease.") has no attribution anywhere in
# its own file and must fire; the stub CLAUDE.md's sentence ("The stop hook
# blocks force-push, if applied by the session runtime.") carries its
# attribution in the SAME sentence and must NOT cause CLAUDE.md to be named
# for this reason (it may still appear in the D6 line for unrelated D1/D5
# causes elsewhere in this same stub, so the assertion targets the D6 line's
# own doc list precisely). The CLAUDE.md sentence must itself match
# _stop_hook_claim (control verb "blocks" within the word window) or this
# negative case is vacuous -- it would pass even with _runtime_attribution
# deleted entirely, since no claim was ever detected in the first place
# (Codex P2 on PR #1308: the original wording put 7 words between "stop hook"
# and "block", one past the regex's 6-word window, so it silently never
# matched at all).
d6_line=$(grep -F "DRIFT [D6]" "$stub_out" || true)
if [ -z "$d6_line" ] || ! printf '%s' "$d6_line" | grep -qF "PROJECT_RULES.md"; then
  echo "detection self-test: FAIL — D6 naive stop-hook claim not detected" >&2
  cat "$stub_out" >&2; exit 1
fi
if printf '%s' "$d6_line" | grep -qF "CLAUDE.md"; then
  echo "detection self-test: FAIL — D6 flagged CLAUDE.md's correctly-attributed sentence" >&2
  cat "$stub_out" >&2; exit 1
fi
echo "detection self-test: PASS (D1 + D5 both directions detected by name, exit 2)"

# 3. D7 key-adjacency: an unrelated 8000 must not green max_context_tokens.
#    Plant a doctrine that cites every shipped value next to its key, then
#    rewrite the RAG row so max_context_tokens is stale while an unrelated
#    "8000 chars" remains elsewhere — whole-doc substring would still pass.
d7tmp="$(mktemp -d)"
cp "$repo_root"/config.yaml "$repo_root"/pyproject.toml "$repo_root"/gate.py "$d7tmp"/
# graph.py feeds D8. Omitting it used to crash the checker mid-run; the
# `|| true` below then hid a traceback behind output printed before it.
cp "$repo_root"/graph.py "$d7tmp"/
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
d7_ok_out="$("$PY" "$checker" --repo-root "$d7tmp" 2>&1)" && d7_rc=0 || d7_rc=$?
# 0 = clean, 2 = drift. Anything else (notably 1 from an unhandled exception) is
# the checker crashing, which every `|| true` in this file would otherwise mask.
if [ "$d7_rc" -ne 0 ] && [ "$d7_rc" -ne 2 ]; then
  echo "D7 adjacency self-test: FAIL — checker exited $d7_rc (expected 0 or 2); it crashed:" >&2
  printf '%s\n' "$d7_ok_out" >&2
  exit 1
fi
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
