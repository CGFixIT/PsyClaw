#!/usr/bin/env python3
"""check_invariants.py – deterministic checker for CyClaw's six security invariants.

Usage: python .claude/skills/invariant-guard/check_invariants.py [--repo-root PATH]

Static analysis only — no imports of project code, no server, no dependencies
beyond the standard library (PyYAML is used when available and degrades to a
text scan when not). Safe to run in a fresh container before any pip install.

Checks (one section per invariant, plus supporting guards):
  I1  RAG-first          retrieve is the unconditional graph entry
  I2  Topology=policy    routing decided only by the two named routers
  I3  Triple-gated external providers  hybrid mode + provider.enabled + user confirmation
  I4  Audit convergence  every node reaches audit_logger; audit_logger -> END
  I5  Soul governance    apply_evolution refuses an empty reason
  I6  Module isolation   agentic/sync/guardrails never meet gate/graph/mcp
  G1  Telemetry kill     env kill-block precedes heavy imports in gate.py
  G2  Auth fail-closed   soul endpoints 401 when CYCLAW_API_KEY unset
  G3  Sanitizer contract documented phrases still caught by banned_patterns
  G4  BM25 stays JSON    bm25_path ends in .json (pickle = RCE)
  G5  MCP no-sampling    CAPABILITIES declares sampling: None

Exit codes (repo convention): 0 all pass · 2 invariant violated · 3 env/config error.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

CORE_FILES = ("gate.py", "gate_ops.py", "graph.py", "mcp_hybrid_server.py")
OUT_OF_BAND_PKGS = ("agentic", "sync", "guardrails", "harness")
# The full documented graph shape (CLAUDE.md's "9-node LangGraph topology").
# I1/I2 previously checked only that specific expected edges/sources were
# PRESENT (membership), never that the graph declares nothing else -- an
# extra graph.add_edge("retrieve", "local_llm") alongside the real edge, or
# a stray add_node, passed every prior check untouched. These two sets close
# that gap by asserting exact equality against the full node/edge shape.
EXPECTED_NODES = frozenset({
    "retrieve", "route_by_score", "guardrail_input", "local_llm", "user_gate",
    "grok_fallback", "claude_fallback", "offline_best_effort", "guardrail_output",
    "audit_logger",
})
EXPECTED_UNCONDITIONAL_EDGES = frozenset({
    ("retrieve", "route_by_score"),
    ("local_llm", "guardrail_output"),
    ("grok_fallback", "guardrail_output"),
    ("claude_fallback", "guardrail_output"),
    ("offline_best_effort", "guardrail_output"),
    ("guardrail_output", "audit_logger"),
    ("audit_logger", "END"),
})
# Conditional-edge sources and their router function names. Single source of
# truth for I2 (topology=policy) and I4 (audit convergence), which both need
# to agree on which nodes route conditionally and via which router -- I4's
# audit-convergence DFS would otherwise silently miss a node's router-driven
# edges if it fell out of sync with I2's expected source set.
COND_SOURCE_ROUTERS = {
    "route_by_score": "score_router",
    "guardrail_input": "guardrail_router",
    "user_gate": "user_gate_router",
}
# Phrases the shipped config must block — mirror tests/test_sanitizer.py
# TestShippedConfigContract. Deleting coverage for any of these is a regression.
CONTRACT_PHRASES = (
    "ignore previous instructions",
    "do anything now",
    "bypass safety",
    "ignore safety",
    "act as your developer",
    "DAN mode",
)

_failures: list[str] = []
_passes: list[str] = []


def ok(label: str) -> None:
    _passes.append(label)
    print(f"  PASS  {label}")


def fail(label: str, detail: str) -> None:
    _failures.append(label)
    print(f"  FAIL  {label}\n        -> {detail}")


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def top_level_import_names(tree: ast.Module) -> list[tuple[str, int]]:
    """(dotted-module-root, lineno) for every import in the file."""
    names: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend((a.name.split(".")[0], node.lineno) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append((node.module.split(".")[0], node.lineno))
    return names


def call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def str_arg(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def first_other_import_line(tree: ast.Module, skip_dotted_module: str) -> int | None:
    """Line of the first module-body-level import, excluding one specific
    ``from X import Y`` module path (the telemetry-kill import itself).

    Walks ``tree.body`` directly rather than the full ``ast.walk`` used by
    ``top_level_import_names`` -- exactly what matters for a small
    ``__init__.py`` where every import lives at module level, not nested
    inside a function.
    """
    lines: list[int] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            lines.extend(node.lineno for a in node.names if a.name != skip_dotted_module)
        elif isinstance(node, ast.ImportFrom) and node.module and node.module != skip_dotted_module:
            lines.append(node.lineno)
    return min(lines, default=None)


def kill_call_line(tree: ast.Module) -> int | None:
    """Line of the telemetry-kill invocation, in either shape CyClaw uses.

    gate.py needs the returned dict for its startup verification table, so it
    assigns: ``_TELEMETRY_KILL = apply_telemetry_kill()``. Every other entry
    point (mcp_hybrid_server.py, agentic/__init__.py, guardrails/__init__.py)
    doesn't need the return value and calls it bare: ``apply_telemetry_kill()``.
    Both must count for the ordering check below.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and call_name(node.value) == "apply_telemetry_kill":
            return node.lineno
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and call_name(node.value) == "apply_telemetry_kill":
            return node.lineno
    return None


def graph_wiring(tree: ast.Module) -> tuple[str | None, list[tuple[str, str]], dict[str, list[str]]]:
    """Extract (entry_point, unconditional_edges, conditional_sources->router-name)."""
    entry: str | None = None
    edges: list[tuple[str, str]] = []
    cond: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)
        if name == "set_entry_point" and node.args:
            entry = str_arg(node.args[0])
        elif name == "add_edge" and len(node.args) >= 2:
            src = str_arg(node.args[0]) or ("__START__" if isinstance(node.args[0], ast.Name) else "?")
            dst = str_arg(node.args[1])
            if dst is None and isinstance(node.args[1], ast.Name):
                dst = node.args[1].id  # END constant
            edges.append((src, dst or "?"))
        elif name == "add_conditional_edges" and node.args:
            src = str_arg(node.args[0]) or "?"
            cond.setdefault(src, [])
    return entry, edges, cond


def node_names(tree: ast.Module) -> set[str]:
    """String literals passed as the first arg of every graph.add_node(...) call."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) == "add_node" and node.args:
            val = str_arg(node.args[0])
            if val:
                names.add(val)
    return names


def router_returns(tree: ast.Module, func_name: str) -> set[str]:
    """String literals returned by a router function (its full routing range)."""
    returns: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    val = str_arg(sub.value)
                    if val:
                        returns.add(val)
    return returns


def dict_literal(node: ast.expr) -> dict[str, str]:
    """A literal ``{"a": "b", ...}`` dict as a plain str->str mapping."""
    # Non-string keys/values are dropped defensively; every real path_map in
    # graph.py is str->str. Returns {} for anything that isn't a dict literal
    # (e.g. a name reference), which callers treat as "no path_map" (identity).
    if not isinstance(node, ast.Dict):
        return {}
    result: dict[str, str] = {}
    for k, v in zip(node.keys, node.values):
        key = str_arg(k) if k is not None else None
        val = str_arg(v)
        if key is not None and val is not None:
            result[key] = val
    return result


def conditional_path_maps(tree: ast.Module) -> dict[str, dict[str, str]]:
    """source -> {router-return-value: real-target-node} for every add_conditional_edges(...) call."""
    # This is the piece I1/I2 were missing entirely: LangGraph resolves a
    # router's return value through this dict to get the actual target node --
    # the router's own returned string is often an internal label, not the
    # real node name. route_by_score's score_router returns "local_llm" for a
    # high-confidence score, but the path_map remaps that to the real target
    # "guardrail_input"; checking score_router's raw return values (as the
    # code did before this fix) verifies the router's own naming convention,
    # not the graph's actual wiring. A source absent here was wired without an
    # explicit path_map (or the map isn't a literal) -- callers fall back to
    # treating the router's return value as the target directly (identity).
    maps: dict[str, dict[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) == "add_conditional_edges" and len(node.args) >= 3:
            src = str_arg(node.args[0])
            if src:
                maps[src] = dict_literal(node.args[2])
    return maps


def conditional_edge_targets(tree: ast.Module, source: str, router_func_name: str) -> set[str]:
    """Real graph targets for a conditional source, resolved through its path_map."""
    # The router's own returned strings, mapped through its add_conditional_edges
    # path_map when one exists (falls back to the raw string per key when the
    # map doesn't cover it, i.e. an identity mapping).
    returns = router_returns(tree, router_func_name)
    path_map = conditional_path_maps(tree).get(source, {})
    return {path_map.get(r, r) for r in returns}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", type=Path, default=None,
                   help="repo root (default: auto-detect from this file's location)")
    args = p.parse_args(argv)

    root = args.repo_root or Path(__file__).resolve().parents[3]
    if not (root / "gate.py").exists():
        print(f"env error: {root} does not look like the CyClaw repo root", file=sys.stderr)
        return 3

    try:
        gate_tree = parse(root / "gate.py")
        gate_ops_tree = parse(root / "gate_ops.py")
        graph_tree = parse(root / "graph.py")
        mcp_tree = parse(root / "mcp_hybrid_server.py")
        agentic_init_tree = parse(root / "agentic" / "__init__.py")
        guardrails_init_tree = parse(root / "guardrails" / "__init__.py")
    except (OSError, SyntaxError) as exc:
        print(f"env error: cannot parse core file: {exc}", file=sys.stderr)
        return 3

    gate_src = (root / "gate.py").read_text(encoding="utf-8")
    graph_src = (root / "graph.py").read_text(encoding="utf-8")
    mcp_src = (root / "mcp_hybrid_server.py").read_text(encoding="utf-8")
    config_src = (root / "config.yaml").read_text(encoding="utf-8")

    # ── I1 RAG-first ─────────────────────────────────────────────────────────
    print("I1 RAG-first")
    entry, edges, cond = graph_wiring(graph_tree)
    if entry == "retrieve":
        ok("graph entry point is 'retrieve'")
    else:
        fail("graph entry point is 'retrieve'", f"entry point is {entry!r}")
    if ("retrieve", "route_by_score") in edges:
        ok("unconditional edge retrieve -> route_by_score")
    else:
        fail("unconditional edge retrieve -> route_by_score", f"edges: {edges}")
    retrieve_edges = {(s, d) for s, d in edges if s == "retrieve"}
    if retrieve_edges == {("retrieve", "route_by_score")}:
        ok("retrieve has exactly one outgoing edge, and it is unconditional")
    else:
        fail("retrieve has exactly one outgoing edge, and it is unconditional",
             f"retrieve's unconditional edges: {sorted(retrieve_edges)} "
             "(an extra add_edge('retrieve', ...) would let something answer before "
             "retrieval without failing the membership check above)")

    # ── I2 Topology = policy ────────────────────────────────────────────────
    print("I2 Topology = policy")
    expected_cond_sources = set(COND_SOURCE_ROUTERS)
    if set(cond) == expected_cond_sources:
        ok("conditional routing only at route_by_score, guardrail_input, and user_gate")
    else:
        fail("conditional routing only at route_by_score, guardrail_input, and user_gate",
             f"conditional sources: {sorted(cond)}")
    # Real targets, not raw router-return strings: score_router returns the
    # string "local_llm" for a high-confidence score, but route_by_score's
    # add_conditional_edges path_map remaps that to the real node
    # "guardrail_input" -- checking the raw return value (as this used to)
    # verifies the router's own internal naming, not the graph's actual
    # wiring, and would not notice a path_map edit that reroutes around
    # guardrail_input/user_gate entirely while score_router's code is
    # untouched. conditional_edge_targets resolves through the path_map.
    score_targets = conditional_edge_targets(graph_tree, "route_by_score", "score_router")
    if score_targets == {"guardrail_input", "user_gate"}:
        ok("route_by_score's real targets are exactly {guardrail_input, user_gate}")
    else:
        fail("route_by_score's real targets are exactly {guardrail_input, user_gate}",
             f"real targets: {sorted(score_targets)} -- a path_map value change can reroute around "
             "guardrail_input/user_gate without score_router's own code changing at all")
    guardrail_targets = conditional_edge_targets(graph_tree, "guardrail_input", "guardrail_router")
    # offline_best_effort joined this set on 2026-08-02: guardrail_input is now
    # the offline branch's entry too, so the rail covers the low-score/declined
    # path instead of only the high-score one.
    if guardrail_targets == {"local_llm", "offline_best_effort", "audit_logger"}:
        ok("guardrail_input's real targets are exactly {local_llm, offline_best_effort, audit_logger}")
    else:
        fail("guardrail_input's real targets are exactly {local_llm, offline_best_effort, audit_logger}",
             f"real targets: {sorted(guardrail_targets)}")
    gate_targets = conditional_edge_targets(graph_tree, "user_gate", "user_gate_router")
    # user_gate_router still RETURNS "offline_best_effort"; the path_map sends
    # that return through guardrail_input first, which is why the real target
    # here is guardrail_input. A path_map edit that points it straight at
    # offline_best_effort again would re-open the un-railed offline path, and
    # this equality is what catches it.
    expected_gate_targets = {"grok_fallback", "claude_fallback", "guardrail_input", "audit_logger"}
    if gate_targets == expected_gate_targets:
        ok("user_gate's real targets are exactly the documented provider/railed-offline/audit targets")
    else:
        fail("user_gate's real targets are the documented provider/railed-offline/audit targets",
             f"real targets: {sorted(gate_targets)}")
    actual_nodes = node_names(graph_tree)
    if actual_nodes == EXPECTED_NODES:
        ok(f"graph declares exactly the {len(EXPECTED_NODES)} documented nodes")
    else:
        fail(f"graph declares exactly the {len(EXPECTED_NODES)} documented nodes",
             f"extra: {sorted(actual_nodes - EXPECTED_NODES)}, missing: {sorted(EXPECTED_NODES - actual_nodes)}")
    actual_edges = set(edges)
    if actual_edges == EXPECTED_UNCONDITIONAL_EDGES:
        ok(f"graph declares exactly the {len(EXPECTED_UNCONDITIONAL_EDGES)} documented unconditional edges")
    else:
        fail(f"graph declares exactly the {len(EXPECTED_UNCONDITIONAL_EDGES)} documented unconditional edges",
             f"extra: {sorted(actual_edges - EXPECTED_UNCONDITIONAL_EDGES)}, "
             f"missing: {sorted(EXPECTED_UNCONDITIONAL_EDGES - actual_edges)}")

    # ── I3 Triple-gated external providers ─────────────────────────────────
    print("I3 Triple-gated external providers")
    if re.search(r'mode.{0,20}==.{0,5}["\']hybrid["\']', gate_src) and "grok" in gate_src:
        ok("gate.py constructs GrokClient only under mode == 'hybrid'")
    else:
        fail("gate.py constructs GrokClient only under mode == 'hybrid'",
             "hybrid-mode guard around GrokClient construction not found")
    if re.search(r'grok.{0,40}enabled', gate_src):
        ok("gate.py checks models.grok.enabled before constructing GrokClient")
    else:
        fail("gate.py checks models.grok.enabled", "enabled check not found near GrokClient")
    if re.search(r'mode.{0,20}==.{0,5}["\']hybrid["\']', gate_src) and "ClaudeClient" in gate_src:
        ok("gate.py constructs ClaudeClient only under mode == 'hybrid'")
    else:
        fail("gate.py constructs ClaudeClient only under mode == 'hybrid'",
             "hybrid-mode guard around ClaudeClient construction not found")
    if re.search(r'claude.{0,40}enabled', gate_src):
        ok("gate.py checks models.claude.enabled before constructing ClaudeClient")
    else:
        fail("gate.py checks models.claude.enabled", "enabled check not found near ClaudeClient")
    if "if not confirmed:" in graph_src and 'return "offline_best_effort"' in graph_src:
        ok("user_gate_router requires user confirmation before external fallback")
    else:
        fail("user_gate_router requires user confirmation before external fallback",
             "declined confirmation branch not found")
    if re.search(r'provider\s*==\s*["\']grok["\']\s+and\s+grok\s+is\s+not\s+None\s+and\s+grok\.is_available\(\)', graph_src):
        ok("user_gate_router requires selected Grok provider and available Grok client")
    else:
        fail("user_gate_router requires selected Grok provider and available Grok client",
             "the Grok provider/client availability condition changed")
    if re.search(r'provider\s*==\s*["\']claude["\']\s+and\s+claude\s+is\s+not\s+None\s+and\s+claude\.is_available\(\)', graph_src):
        ok("user_gate_router requires selected Claude provider and available Claude client")
    else:
        fail("user_gate_router requires selected Claude provider and available Claude client",
             "the Claude provider/client availability condition changed")

    # ── I4 Audit convergence ────────────────────────────────────────────────
    print("I4 Audit convergence")
    adj: dict[str, set[str]] = {}
    for src, dst in edges:
        adj.setdefault(src, set()).add(dst)
    for src, router in COND_SOURCE_ROUTERS.items():
        # Real targets (through the path_map), not raw router-return strings
        # -- same reasoning as I2 above: route_by_score's reachability edge
        # must point at guardrail_input (the real node), not the literal
        # string "local_llm" score_router happens to return internally.
        adj.setdefault(src, set()).update(conditional_edge_targets(graph_tree, src, router))
    nodes = {"retrieve", "route_by_score", "guardrail_input", "local_llm", "user_gate",
             "grok_fallback", "claude_fallback", "offline_best_effort"}

    def reaches_audit(start: str, seen: set[str] | None = None) -> bool:
        seen = seen or set()
        if start in seen:
            return False
        seen.add(start)
        for nxt in adj.get(start, ()):  # noqa: B905 - not zip
            if nxt == "audit_logger" or reaches_audit(nxt, seen):
                return True
        return False

    stranded = sorted(n for n in nodes if not reaches_audit(n))
    if not stranded:
        ok("all 8 upstream nodes reach audit_logger")
    else:
        fail("all 8 upstream nodes reach audit_logger", f"stranded nodes: {stranded}")
    if any(src == "audit_logger" and dst == "END" for src, dst in edges):
        ok("audit_logger -> END")
    else:
        fail("audit_logger -> END", f"edges from audit_logger: {adj.get('audit_logger')}")

    # ── I5 Soul governance ──────────────────────────────────────────────────
    print("I5 Soul governance")
    try:
        pers_src = (root / "utils" / "personality.py").read_text(encoding="utf-8")
    except OSError as exc:
        print(f"env error: {exc}", file=sys.stderr)
        return 3
    if re.search(r"if\s+not\s+reason\s+or\s+not\s+reason\.strip\(\):\s*\n\s*raise", pers_src):
        ok("apply_evolution raises on empty reason")
    else:
        fail("apply_evolution raises on empty reason",
             "the empty-reason guard in utils/personality.py changed or moved")
    if "os.replace(" in pers_src:
        ok("soul writes are atomic (os.replace)")
    else:
        fail("soul writes are atomic (os.replace)", "os.replace not found in personality.py")

    # ── I6 Module isolation ─────────────────────────────────────────────────
    print("I6 Module isolation")
    for fname, tree in (("gate.py", gate_tree), ("gate_ops.py", gate_ops_tree),
                        ("graph.py", graph_tree), ("mcp_hybrid_server.py", mcp_tree)):
        bad = sorted({m for m, _ in top_level_import_names(tree) if m in OUT_OF_BAND_PKGS})
        if not bad:
            ok(f"{fname} imports none of {OUT_OF_BAND_PKGS}")
        else:
            fail(f"{fname} imports none of {OUT_OF_BAND_PKGS}", f"imports {bad}")
    core_roots = {"gate", "gate_ops", "graph", "mcp_hybrid_server"}
    scanned = 0
    offenders: list[str] = []
    for pkg in OUT_OF_BAND_PKGS:
        for py in sorted((root / pkg).rglob("*.py")):
            scanned += 1
            try:
                tree = parse(py)
            except SyntaxError as exc:
                fail(f"{py.relative_to(root)} parses", str(exc))
                continue
            hit = sorted({m for m, _ in top_level_import_names(tree) if m in core_roots})
            if hit:
                offenders.append(f"{py.relative_to(root)} imports {hit}")
    if scanned == 0:
        fail("out-of-band packages scanned", "glob found no files — check repo root")
    elif not offenders:
        ok(f"none of {scanned} out-of-band files import gate/graph/mcp_hybrid_server")
    else:
        fail("out-of-band files never import core modules", "; ".join(offenders))

    # ── G1 Telemetry kill placement ─────────────────────────────────────────
    print("G1 Telemetry kill")
    kill_line = None
    for node in ast.walk(gate_tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_TELEMETRY_KILL":
                    kill_line = node.lineno
    heavy = {"graph", "retrieval", "llm", "langchain", "langgraph", "chromadb", "fastapi"}
    heavy_first = min((ln for m, ln in top_level_import_names(gate_tree) if m in heavy),
                      default=None)
    if kill_line is not None and heavy_first is not None and kill_line < heavy_first:
        ok(f"_TELEMETRY_KILL (line {kill_line}) precedes first heavy import (line {heavy_first})")
    else:
        fail("_TELEMETRY_KILL precedes heavy imports",
             f"kill at {kill_line}, first heavy import at {heavy_first} — env vars must be "
             "set before langchain/chromadb load or telemetry escapes")
    # agentic/__init__.py and guardrails/__init__.py never import gate.py, so each
    # calls apply_telemetry_kill() itself (bare, not assigned -- neither needs the
    # returned dict). Unlike gate.py's curated "heavy" allowlist, a thin __init__.py
    # has no legitimate early import to allow-list around: everything it imports
    # besides the kill itself is a submodule that could transitively reach a
    # telemetry-capable library (agentic.registry -> guardrails.rails is one such
    # path today), so the bar here is stricter -- precede EVERY other import, not
    # just a known-heavy subset.
    for label, tree in (("agentic/__init__.py", agentic_init_tree), ("guardrails/__init__.py", guardrails_init_tree)):
        pkg_kill_line = kill_call_line(tree)
        first_import = first_other_import_line(tree, skip_dotted_module="utils.telemetry_kill")
        if pkg_kill_line is not None and first_import is not None and pkg_kill_line < first_import:
            ok(f"{label}: telemetry kill (line {pkg_kill_line}) precedes first other import (line {first_import})")
        else:
            fail(f"{label}: telemetry kill precedes first other import",
                 f"kill at {pkg_kill_line}, first other import at {first_import} — env vars must be "
                 "set before any submodule import or telemetry escapes")

    # ── G2 Auth fail-closed ─────────────────────────────────────────────────
    print("G2 Auth fail-closed")
    if "hmac.compare_digest" in gate_src:
        ok("constant-time key compare (hmac.compare_digest)")
    else:
        fail("constant-time key compare", "hmac.compare_digest not found in gate.py")
    if re.search(r'CYCLAW_API_KEY.*\n(.*\n){0,8}?.*(401|HTTP_401)', gate_src):
        ok("unset CYCLAW_API_KEY fails closed (401)")
    else:
        fail("unset CYCLAW_API_KEY fails closed (401)",
             "fail-closed branch near CYCLAW_API_KEY not found — soul endpoints may fail open")

    # ── G3 Sanitizer contract phrases ───────────────────────────────────────
    print("G3 Sanitizer contract")
    patterns: list[str] = []
    try:
        import yaml  # noqa: S506 - safe_load below
        cfg = yaml.safe_load(config_src)
        patterns = (cfg.get("policy", {}) or {}).get("prompt_filter", {}).get("banned_patterns", []) or []
    except ImportError:
        # Degraded text scan: collect quoted list items under banned_patterns,
        # stopping at the first line that starts a different key (so patterns
        # from later lists like redact_secrets_like are never counted here).
        in_block = False
        for line in config_src.splitlines():
            if re.match(r"\s*banned_patterns:", line):
                in_block = True
                continue
            if in_block:
                item = re.match(r'\s*-\s*["\'](.+?)["\']\s*(?:#.*)?$', line)
                if item:
                    patterns.append(item.group(1))
                elif line.strip() and not line.strip().startswith("#"):
                    break  # a new key ends the list
    compiled = []
    for pat in patterns:
        try:
            compiled.append(re.compile(pat, re.IGNORECASE))
        except re.error:
            pass  # sanitizer drops uncompilable patterns; mirror that
    missed = [ph for ph in CONTRACT_PHRASES if not any(c.search(ph) for c in compiled)]
    if patterns and not missed:
        ok(f"all {len(CONTRACT_PHRASES)} contract phrases caught by {len(compiled)} compiled patterns")
    elif not patterns:
        fail("banned_patterns present in config.yaml", "no patterns found")
    else:
        fail("all contract phrases caught", f"uncaught: {missed}")

    # ── G4 BM25 stays JSON ──────────────────────────────────────────────────
    print("G4 BM25 format")
    m = re.search(r"bm25_path:\s*(\S+)", config_src)
    if m and m.group(1).strip("'\"").endswith(".json"):
        ok(f"bm25_path is JSON ({m.group(1)})")
    else:
        fail("bm25_path is JSON", f"found {m.group(1) if m else 'nothing'} — pickle is an RCE vector")

    # ── G5 MCP no-sampling ──────────────────────────────────────────────────
    print("G5 MCP no-sampling")
    if re.search(r'["\']sampling["\']\s*:\s*None', mcp_src):
        ok("MCP CAPABILITIES declares sampling: None")
    else:
        fail("MCP CAPABILITIES declares sampling: None",
             "sampling capability changed — the MCP server must never expose an LLM path")

    print(f"\n{len(_passes)} passed, {len(_failures)} failed")
    return 2 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
