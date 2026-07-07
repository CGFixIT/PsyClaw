#!/usr/bin/env python3
"""doc_sync.py – detect drift between CyClaw's code/config and its docs.

Usage:
    python .claude/skills/doc-sync/doc_sync.py [--repo-root PATH] [--json]

Code is the source of truth; docs are derived. This script extracts facts from
code/config and checks that the docs that cite them agree. It reports drift; it
NEVER edits anything. Fixing docs is a human/agent judgment call (and behavior
must never be changed to match a stale doc — see the SKILL).

Checks:
    D1  Skills on disk        every .claude/skills/<name> appears in the CLAUDE.md skills table
    D2  Console entry points  pyproject [project.scripts] names appear in CLAUDE.md
    D3  Config numbers        port / min_score / rrf_k / graph_timeout_sec / soul_max_chars
                              cited in CLAUDE.md match config.yaml
    D4  Banned-pattern count  the real banned_patterns length matches the "<n> patterns"
                              claims across CLAUDE.md, config.yaml, guardrails, fsconnect
    D5  Route table           gate.py @app routes are all named in CLAUDE.md
    D6  Hook claims           doc claims about a "stop hook" are backed by .claude/settings.json

Exit codes (repo convention):
    0  no drift detected
    2  drift detected (items to reconcile)
    3  env/config error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_drift: list[dict] = []


def note(check: str, source_of_truth: str, detail: str) -> None:
    _drift.append({"check": check, "truth": source_of_truth, "detail": detail})
    print(f"  DRIFT [{check}] {detail}\n         source of truth: {source_of_truth}")


def ok(check: str, detail: str) -> None:
    print(f"  ok    [{check}] {detail}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", type=Path, default=None)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    root = args.repo_root or Path(__file__).resolve().parents[3]
    claude_md_path = root / "CLAUDE.md"
    if not claude_md_path.exists():
        print(f"env error: {claude_md_path} not found", file=sys.stderr)
        return 3
    try:
        import yaml
        claude = claude_md_path.read_text(encoding="utf-8")
        cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        settings = (root / ".claude" / "settings.json").read_text(encoding="utf-8")
    except (OSError, ImportError) as exc:
        print(f"env error: {exc}", file=sys.stderr)
        return 3

    # ── D1 Skills on disk vs CLAUDE.md table ────────────────────────────────
    print("D1 Skills on disk -> CLAUDE.md")
    skills_dir = root / ".claude" / "skills"
    disk_skills = sorted(d.name for d in skills_dir.iterdir()
                         if d.is_dir() and (d / "SKILL.md").exists())
    missing = [s for s in disk_skills if s not in claude]
    if not missing:
        ok("D1", f"all {len(disk_skills)} skills referenced in CLAUDE.md")
    else:
        note("D1", "the .claude/skills/ directory",
             f"skills on disk but absent from CLAUDE.md: {missing}")

    # ── D2 Entry points ─────────────────────────────────────────────────────
    print("D2 Console entry points -> CLAUDE.md")
    scripts = re.findall(r'^([a-z0-9-]+)\s*=\s*"[^"]+"', pyproject.split("[project.scripts]", 1)[-1]
                         .split("[", 1)[0], re.MULTILINE)
    missing = [s for s in scripts if s not in claude]
    if scripts and not missing:
        ok("D2", f"all {len(scripts)} entry points named in CLAUDE.md")
    elif not scripts:
        note("D2", "pyproject [project.scripts]", "no entry points parsed — check pyproject")
    else:
        note("D2", "pyproject [project.scripts]", f"entry points absent from CLAUDE.md: {missing}")

    # ── D3 Config numbers cited in CLAUDE.md ────────────────────────────────
    print("D3 Config numbers -> CLAUDE.md")
    facts = {
        "api.port": cfg["api"]["port"],
        "retrieval.min_score": cfg["retrieval"]["min_score"],
        "retrieval.rrf_k": cfg["retrieval"]["rrf_k"],
        "api.graph_timeout_sec": cfg["api"]["graph_timeout_sec"],
        "personality.soul_max_chars": cfg["personality"]["soul_max_chars"],
    }
    # Only flag a number that CLAUDE.md cites with a WRONG value; absence is fine
    # (not every tunable is documented). We detect "cited" by the config key's
    # short name near a number.
    citation_hints = {
        "api.port": r"8787",
        "retrieval.min_score": r"min_score",
        "retrieval.rrf_k": r"rrf_k|RRF.*\b60\b|k=60",
        "api.graph_timeout_sec": r"graph_timeout|330",
        "personality.soul_max_chars": r"soul_max_chars|8000",
    }
    for key, val in facts.items():
        hint = citation_hints[key]
        if re.search(hint, claude):
            # CLAUDE.md talks about this tunable — does the true value appear?
            if re.search(rf"\b{re.escape(str(val))}\b", claude):
                ok("D3", f"{key} = {val} consistent with CLAUDE.md")
            else:
                note("D3", f"config.yaml {key}={val}",
                     f"CLAUDE.md discusses {key} but the value {val} is not present (possible stale number)")
        else:
            ok("D3", f"{key} = {val} (not cited in CLAUDE.md; nothing to check)")

    # ── D4 Banned-pattern count ─────────────────────────────────────────────
    print("D4 Banned-pattern count")
    real_n = len(cfg["policy"]["prompt_filter"]["banned_patterns"])
    cite_files = {
        "CLAUDE.md": claude,
        "config.yaml": (root / "config.yaml").read_text(encoding="utf-8"),
    }
    for opt in ("guardrails/rails.py", "agentic/fsconnect/client.py"):
        fp = root / opt
        if fp.exists():
            cite_files[opt] = fp.read_text(encoding="utf-8")
    drift_files = []
    for name, text in cite_files.items():
        # Find "<n> patterns" / "<n>-pattern" claims and check they equal real_n.
        for m in re.finditer(r"(\d+)[\s-]+pattern", text):
            claimed = int(m.group(1))
            if claimed != real_n and claimed > 5:  # ignore small unrelated numbers
                drift_files.append(f"{name} claims {claimed}")
    if not drift_files:
        ok("D4", f"banned_patterns count {real_n} consistent everywhere it's cited")
    else:
        note("D4", f"config.yaml banned_patterns (actual {real_n})",
             f"count drift: {drift_files}")

    # ── D5 Route table ──────────────────────────────────────────────────────
    print("D5 gate.py routes -> CLAUDE.md")
    gate_src = (root / "gate.py").read_text(encoding="utf-8")
    routes = sorted(set(re.findall(r'@app\.(?:get|post)\("([^"]+)"', gate_src)))
    # Ignore the static mount and root; check the meaningful API routes.
    api_routes = [r for r in routes if r not in ("/",)]
    missing = [r for r in api_routes if r not in claude]
    if not missing:
        ok("D5", f"all {len(api_routes)} API routes named in CLAUDE.md")
    else:
        note("D5", "gate.py @app decorators", f"routes absent from CLAUDE.md: {missing}")

    # ── D6 Stop-hook claims ─────────────────────────────────────────────────
    print("D6 Hook claims -> settings.json")
    claims_stop_hook = "stop hook" in claude.lower()
    has_stop_hook = '"Stop"' in settings
    # An accurate statement acknowledges the enforcement is applied by the
    # session runtime rather than wired in repo settings.json. Only flag the
    # NAIVE claim (implies a repo-wired hook) that no Stop hook backs.
    acknowledges_runtime = bool(re.search(r"session runtime|not wired|runtime[- ]enforced", claude, re.I))
    if claims_stop_hook and not has_stop_hook and not acknowledges_runtime:
        note("D6", ".claude/settings.json (no Stop hook wired)",
             "CLAUDE.md references a 'stop hook' as if repo-wired, but settings.json wires no "
             "Stop hook — wire it, or state that the enforcement is applied by the session runtime")
    elif claims_stop_hook and has_stop_hook:
        ok("D6", "stop-hook claim backed by a wired Stop hook")
    else:
        ok("D6", "stop-hook claim absent or accurately attributed to the runtime")

    result = {"drift_count": len(_drift), "drift": _drift}
    if args.json:
        print(json.dumps(result, indent=2))
    print(f"\n{len(_drift)} drift item(s) found")
    return 2 if _drift else 0


if __name__ == "__main__":
    sys.exit(main())
