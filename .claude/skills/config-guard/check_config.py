#!/usr/bin/env python3
"""check_config.py – static validation of CyClaw's config.yaml contract.

Usage:
    python3 .claude/skills/config-guard/check_config.py [--repo-root PATH]
                                                        [--strict] [--json]

config.yaml is CyClaw's single source of truth (CLAUDE.md §1). Its load-bearing
numbers are not independent knobs — several are bound by RELATIONS the running
system assumes but no automated check enforces. This script validates those
relations, the value-safety ranges, and the threat-model boundary statically,
before boot, with no app import (only PyYAML).

It is deliberately NOT a duplicate of two neighbours:
  * utils/config_validation.py runs at BOOT, imports the app, and checks a
    narrow set (min_score in [0,1]; top_k_*/rrf_k positive ints; soul_max_chars
    positive). This checker adds the RELATIONAL and CROSS-KEY invariants it does
    not: graph_timeout > llm_timeout, chunk_overlap < chunk_size, the soul/context
    budget, loopback-only host, and the shipped default posture.
  * invariant-guard owns the security-STRUCTURE guards (BM25 stays JSON, telemetry
    kill order, MCP sampling). This checker does not re-check those; it covers the
    numeric/relational/posture contract they leave open — including the min_score
    RRF-scale trap invariant-guard's own SKILL.md flags as uncaught.

Severity:
    FAIL  a documented contract or the threat-model boundary is broken (exit 2).
    WARN  drift from a shipped default that an operator MAY change deliberately
          (exit 0; --strict escalates every WARN to a failure).
    INFO  advisory arithmetic; never affects the exit code.

Exit codes (repo convention):
    0  contract holds (warnings may be present without --strict)
    2  a FAIL check tripped (or a WARN under --strict)
    3  env/config error (config.yaml missing/unparseable, or PyYAML absent)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Loopback hosts CyClaw is permitted to bind (docs/THREAT_MODEL.md: loopback-only).
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Documented graph-timeout margin: graph_timeout_sec >= local_llm.timeout_sec + 30
# (config.yaml api.graph_timeout_sec comment; covers retrieval + routing + audit).
_TIMEOUT_MARGIN_SEC = 30

# min_score lives on the RRF scale (~top-3-4 rank ≈ 0.028); fused ranks rarely
# exceed ~0.1. A value above this reads like a cosine/similarity threshold and
# routes nearly every query to the user gate — the trap invariant-guard names.
_RRF_SANITY_CEILING = 0.1

_fails: list[dict[str, str]] = []
_warns: list[dict[str, str]] = []


def fail(check: str, detail: str) -> None:
    _fails.append({"check": check, "detail": detail})
    print(f"  FAIL  [{check}] {detail}")


def warn(check: str, detail: str) -> None:
    _warns.append({"check": check, "detail": detail})
    print(f"  WARN  [{check}] {detail}")


def ok(check: str, detail: str) -> None:
    print(f"  ok    [{check}] {detail}")


def info(check: str, detail: str) -> None:
    print(f"  info  [{check}] {detail}")


def _is_num(value: Any) -> bool:
    """Real number but NOT bool (bool is an int subclass in Python)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_pos_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _dig(cfg: dict[str, Any], *keys: str) -> Any:
    """Walk nested mappings, returning None if any hop is missing/not a mapping."""
    node: Any = cfg
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def run_checks(cfg: dict[str, Any]) -> None:
    # ── C1 min_score is a routable RRF score ────────────────────────────────
    print("C1 retrieval.min_score range")
    min_score = _dig(cfg, "retrieval", "min_score")
    if not _is_num(min_score) or not 0 <= float(min_score) <= 1:
        fail("C1", f"retrieval.min_score must be a number in [0, 1], got {min_score!r}")
    else:
        ok("C1", f"min_score={min_score} is a valid RRF-scale score")

    # ── C2 graph timeout strictly dominates the LLM timeout ─────────────────
    print("C2 api.graph_timeout_sec > models.local_llm.timeout_sec (+margin)")
    graph_t = _dig(cfg, "api", "graph_timeout_sec")
    llm_t = _dig(cfg, "models", "local_llm", "timeout_sec")
    if not _is_num(graph_t) or not _is_num(llm_t):
        fail("C2", f"graph_timeout_sec ({graph_t!r}) and local_llm.timeout_sec ({llm_t!r}) must both be numbers")
    elif graph_t <= llm_t:
        fail("C2", f"graph_timeout_sec ({graph_t}) must EXCEED local_llm.timeout_sec ({llm_t}) "
                   "or the graph is cut before the LLM call finishes (orphaned invocation)")
    elif graph_t - llm_t < _TIMEOUT_MARGIN_SEC:
        warn("C2", f"graph_timeout_sec ({graph_t}) - timeout_sec ({llm_t}) = {graph_t - llm_t}s "
                   f"< documented {_TIMEOUT_MARGIN_SEC}s margin for retrieval+routing+audit")
    else:
        ok("C2", f"graph_timeout_sec ({graph_t}) exceeds timeout_sec ({llm_t}) by {graph_t - llm_t}s")

    # ── C3 chunk overlap stays below chunk size ─────────────────────────────
    print("C3 indexing.chunk_overlap < indexing.chunk_size")
    overlap = _dig(cfg, "indexing", "chunk_overlap")
    size = _dig(cfg, "indexing", "chunk_size")
    if not _is_pos_int(overlap) or not _is_pos_int(size):
        fail("C3", f"chunk_overlap ({overlap!r}) and chunk_size ({size!r}) must both be positive integers")
    elif overlap >= size:
        fail("C3", f"chunk_overlap ({overlap}) must be < chunk_size ({size}) "
                   "or chunking loops / never advances")
    else:
        ok("C3", f"chunk_overlap ({overlap}) < chunk_size ({size})")

    # ── C4 loopback-only bind (threat-model boundary) ───────────────────────
    print("C4 api.host is loopback-only")
    host = _dig(cfg, "api", "host")
    if host in _LOOPBACK_HOSTS:
        ok("C4", f"api.host={host!r} is loopback (threat model: 127.0.0.1 only)")
    else:
        fail("C4", f"api.host={host!r} binds beyond loopback — docs/THREAT_MODEL.md is "
                   "single-operator, loopback-bound (127.0.0.1:8787). Never a public interface")

    # ── C5 soul budget stays inside the prompt budget ───────────────────────
    print("C5 personality.soul_max_chars fits the context budget")
    soul_cap = _dig(cfg, "personality", "soul_max_chars")
    max_ctx = _dig(cfg, "retrieval", "max_context_tokens")
    personality_on = bool(_dig(cfg, "personality", "enabled"))
    if not personality_on:
        ok("C5", "personality disabled — soul budget not applicable")
    elif not _is_pos_int(soul_cap):
        fail("C5", f"soul_max_chars must be a positive integer, got {soul_cap!r} "
                   "(0 silently drops the soul from every prompt)")
    elif _is_num(max_ctx) and soul_cap >= max_ctx * 4:
        fail("C5", f"soul_max_chars ({soul_cap}) must stay below max_context_tokens*4 "
                   f"({int(max_ctx * 4)}, ~4 chars/token) or the soul crowds out retrieved context")
    else:
        ok("C5", f"soul_max_chars ({soul_cap}) fits within max_context_tokens*4 "
                 f"({int(max_ctx * 4) if _is_num(max_ctx) else '?'})")

    # ── C6 rate-limit knobs are usable ──────────────────────────────────────
    print("C6 api.rate_limit is a positive window")
    max_req = _dig(cfg, "api", "rate_limit", "max_requests")
    window = _dig(cfg, "api", "rate_limit", "window_seconds")
    if not _is_pos_int(max_req) or not _is_pos_int(window):
        fail("C6", f"rate_limit.max_requests ({max_req!r}) and window_seconds ({window!r}) "
                   "must both be positive integers or per-IP throttling is disabled/broken")
    else:
        ok("C6", f"rate_limit = {max_req} requests / {window}s")

    # ── C7 min_score RRF-scale sanity (the acknowledged gap) ────────────────
    print("C7 retrieval.min_score RRF-scale sanity")
    if _is_num(min_score) and 0 <= float(min_score) <= 1:
        if float(min_score) > _RRF_SANITY_CEILING:
            warn("C7", f"min_score={min_score} exceeds the RRF sanity ceiling "
                       f"({_RRF_SANITY_CEILING}). RRF-fused scores rarely top ~0.1, so this "
                       "routes nearly every query to the user gate. A stricter gate belongs in "
                       "the 0.05–0.08 band (config.yaml comment), not the cosine-scale 0.5")
        else:
            ok("C7", f"min_score={min_score} is on the RRF scale (<= {_RRF_SANITY_CEILING})")

    # ── C8 rrf_k load-bearing constant ──────────────────────────────────────
    print("C8 retrieval.rrf_k == 60 (documented authoritative RRF k)")
    rrf_k = _dig(cfg, "retrieval", "rrf_k")
    if rrf_k == 60:
        ok("C8", "rrf_k=60 (matches the documented fusion constant)")
    elif _is_pos_int(rrf_k):
        warn("C8", f"rrf_k={rrf_k} differs from the documented 60 — re-tune retrieval "
                   "deliberately and update CLAUDE.md §2 if this is intentional")
    else:
        fail("C8", f"rrf_k must be a positive integer, got {rrf_k!r}")

    # ── C9 shipped default posture is safe/offline ──────────────────────────
    print("C9 shipped default posture (offline; external fallbacks off)")
    mode = _dig(cfg, "app", "mode")
    grok_on = bool(_dig(cfg, "models", "grok", "enabled"))
    claude_on = bool(_dig(cfg, "models", "claude", "enabled"))
    posture = []
    if mode != "offline":
        posture.append(f"app.mode={mode!r} (shipped default is 'offline')")
    if grok_on:
        posture.append("models.grok.enabled=true")
    if claude_on:
        posture.append("models.claude.enabled=true")
    if posture:
        warn("C9", "committed config ships with external/online posture: "
                   + "; ".join(posture) + " — fine for a live operator, surprising for a portfolio default")
    else:
        ok("C9", "mode=offline, grok & claude disabled (safe shipped default)")

    # ── C10 local context is not forwarded off-box by default ───────────────
    print("C10 policy.fallback does not leak local context off-box")
    send_grok = bool(_dig(cfg, "policy", "fallback", "send_local_context_to_grok"))
    send_claude = bool(_dig(cfg, "policy", "fallback", "send_local_context_to_claude"))
    if send_grok or send_claude:
        leaks = [n for n, v in (("grok", send_grok), ("claude", send_claude)) if v]
        warn("C10", f"send_local_context_to_{'/'.join(leaks)}=true forwards retrieved local "
                    "context to a paid external API — confirm this is intended")
    else:
        ok("C10", "local retrieved context stays off the external providers")

    # ── C11 guardrails engine tracks the local LLM ──────────────────────────
    print("C11 guardrails model/base_url in sync with the local LLM")
    g_model = _dig(cfg, "guardrails", "model")
    l_model = _dig(cfg, "models", "local_llm", "model")
    g_url = _dig(cfg, "guardrails", "base_url")
    if g_model is None and g_url is None:
        ok("C11", "no guardrails block — nothing to sync")
    else:
        drift = []
        if g_model != l_model:
            drift.append(f"guardrails.model={g_model!r} != local_llm.model={l_model!r}")
        if isinstance(g_url, str) and not any(h in g_url for h in ("127.0.0.1", "localhost")):
            drift.append(f"guardrails.base_url={g_url!r} is not loopback")
        if drift:
            warn("C11", "; ".join(drift) + " (config.yaml says keep guardrails in sync with local_llm)")
        else:
            ok("C11", "guardrails.model and base_url track the local LLM")

    # ── C12 no-stall arithmetic (advisory) ──────────────────────────────────
    print("C12 no-stall context arithmetic (advisory)")
    llm_max = _dig(cfg, "models", "local_llm", "max_tokens")
    if _is_num(max_ctx) and _is_num(llm_max):
        floor = int(max_ctx) + int(llm_max) + 1500
        info("C12", f"set the local model's context length (num_ctx) >= {floor} "
                    f"(max_context_tokens {max_ctx} + max_tokens {llm_max} + ~1500 headroom) to avoid a stall")
    else:
        info("C12", "max_context_tokens/max_tokens not both numeric — skipping no-stall arithmetic")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", type=Path, default=None)
    p.add_argument("--strict", action="store_true",
                   help="treat WARN as failure (exit 2 on any warning)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    root = args.repo_root or Path(__file__).resolve().parents[3]
    config_path = root / "config.yaml"
    if not config_path.exists():
        print(f"env error: {config_path} not found", file=sys.stderr)
        return 3
    try:
        import yaml  # soft dep: nested YAML needs a real parser (mark: Needs PyYAML)
    except ImportError:
        print("env error: PyYAML not importable — install project deps to run config-guard "
              "(pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML)",
              file=sys.stderr)
        return 3
    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"env error: could not parse {config_path}: {exc}", file=sys.stderr)
        return 3
    if not isinstance(cfg, dict):
        print(f"env error: {config_path} did not parse to a mapping", file=sys.stderr)
        return 3

    print("== config-guard: static config.yaml contract ==")
    run_checks(cfg)

    strict_fail = args.strict and _warns
    print(f"\n{len(_fails)} failure(s), {len(_warns)} warning(s)"
          + (" (--strict: warnings count as failures)" if args.strict else ""))
    if args.json:
        print(json.dumps({"fails": _fails, "warns": _warns, "strict": args.strict}, indent=2))
    return 2 if (_fails or strict_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
