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

cp "$repo_root"/gate.py "$repo_root"/gate_ops.py "$repo_root"/graph.py \
   "$repo_root"/mcp_hybrid_server.py "$repo_root"/config.yaml "$tmp"/
cp -r "$repo_root"/utils "$repo_root"/agentic "$repo_root"/sync \
      "$repo_root"/guardrails "$tmp"/ 2>/dev/null || true

# Violation A: core module imports an out-of-band package (breaks I6).
sed -i.bak 's/^import hmac/import hmac\nimport agentic/' "$tmp/gate.py"
# Violation B: sever the grok_fallback -> audit_logger edge (breaks I4).
sed -i.bak 's/graph.add_edge("grok_fallback", "audit_logger")/pass  # severed/' "$tmp/graph.py"
# Violation C: an EXTRA unconditional edge out of retrieve, alongside the real
# one — a stray graph.add_edge("retrieve", "local_llm") would let something
# answer before retrieval finishes routing, but a membership-only check
# (`("retrieve", "route_by_score") in edges`) can't see it since the real
# edge is still present. Only the exclusivity checks (I1's retrieve-edge-count
# assertion, I2's exact node/edge-set equality) catch this.
sed -i.bak 's/graph.add_edge("retrieve", "route_by_score")/graph.add_edge("retrieve", "route_by_score")\n    graph.add_edge("retrieve", "local_llm")  # planted: extra unconditional edge/' "$tmp/graph.py"
# Violation D: retarget route_by_score's path_map so a high-confidence score
# reroutes straight to grok_fallback instead of guardrail_input — score_router
# itself is untouched (it still returns the string "local_llm"), only the
# add_conditional_edges path_map that resolves that string to a real node
# changes. This bypasses guardrail_input AND user_gate (I3's entire
# triple-gate) with no LLM-preceding-retrieval or dangling-edge symptom, so
# only a check that resolves router return values through the literal
# path_map (not the router's own return strings) can catch it.
sed -i.bak 's/"local_llm": "guardrail_input",/"local_llm": "grok_fallback",/' "$tmp/graph.py"

out="$(python3 "$checker" --repo-root "$tmp" 2>&1)"
rc=$?
if [ "$rc" -ne 2 ]; then
  echo "mutation test: FAIL — expected exit 2 on broken tree, got $rc" >&2
  echo "$out" >&2
  exit 1
fi
echo "$out" | grep -q "gate.py imports none" || { echo "mutation test: import violation not detected" >&2; exit 1; }
echo "$out" | grep -q "reach audit_logger"   || { echo "mutation test: severed edge not detected" >&2; exit 1; }
echo "$out" | grep -q "retrieve has exactly one outgoing edge" || { echo "mutation test: extra retrieve edge not detected" >&2; exit 1; }
echo "$out" | grep -q "route_by_score's real targets" || { echo "mutation test: path_map retarget not detected" >&2; exit 1; }
echo "mutation test: PASS (all four injected violations detected, exit 2)"

echo "== invariant-guard verify: OK =="
