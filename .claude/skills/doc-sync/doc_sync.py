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
    D7  M5 doctrine           docs/m5-48gb-coding-expectations.md cites the shipped
                               Ollama context budget and timeout values

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

    # ── D7 M5 hardware doctrine numbers ─────────────────────────────────────
    print("D7 M5 doctrine numbers -> config.yaml + macos/ollama-mlx.env")
    m5_doc_path = root / "docs" / "m5-48gb-coding-expectations.md"
    ollama_env_path = root / "macos" / "ollama-mlx.env"
    try:
        m5_doc = m5_doc_path.read_text(encoding="utf-8")
        ollama_env = ollama_env_path.read_text(encoding="utf-8")
    except OSError as exc:
        note("D7", "docs/m5-48gb-coding-expectations.md + macos/ollama-mlx.env",
             f"could not read M5 doctrine inputs: {exc}")
    else:
        context_match = re.search(r"(?m)^OLLAMA_CONTEXT_LENGTH=(\d+)$", ollama_env)
        if context_match is None:
            note("D7", "macos/ollama-mlx.env",
                 "OLLAMA_CONTEXT_LENGTH is absent or not a decimal integer")
        else:
            expected = {
                "models.local_llm.max_tokens": cfg["models"]["local_llm"]["max_tokens"],
                "models.local_llm.timeout_sec": cfg["models"]["local_llm"]["timeout_sec"],
                "api.graph_timeout_sec": cfg["api"]["graph_timeout_sec"],
                "retrieval.max_context_tokens": cfg["retrieval"]["max_context_tokens"],
                "macos.OLLAMA_CONTEXT_LENGTH": int(context_match.group(1)),
            }
            missing = [
                f"{key}={value}"
                for key, value in expected.items()
                if str(value) not in m5_doc
            ]
            if missing:
                note("D7", "config.yaml + macos/ollama-mlx.env",
                     "M5 doctrine omits shipped value(s): " + ", ".join(missing))
            else:
                ok("D7", "M5 doctrine cites all shipped context-budget and timeout values")

    # ── D4 Banned-pattern count ─────────────────────────────────────────────
    print("D4 Banned-pattern count")
    real_n = len(cfg["policy"]["prompt_filter"]["banned_patterns"])
    cite_files = {
        "CLAUDE.md": claude,
        "config.yaml": (root / "config.yaml").read_text(encoding="utf-8"),
    }
    # README.md and docs/THREAT_MODEL.md were NOT scanned here originally, and
    # both silently drifted to a stale "32 patterns" while this check reported
    # "consistent everywhere it's cited" -- the count is cited in a mermaid node
    # and a threat table, which no other check reads either. A blind spot in a
    # drift checker is worse than no checker, because it is trusted.
    #
    # Second round of the same lesson: .claude/** was still unscanned after the
    # files above were added, so the fable-protocol skill and its command copy
    # sat on a stale "32 banned_patterns" while this check again reported
    # "consistent everywhere it's cited". Agent-facing prompt files are read by
    # every session, so a wrong number there is repeated back with confidence.
    for opt in (
        "guardrails/rails.py", "agentic/fsconnect/client.py",
        "README.md", "docs/THREAT_MODEL.md", "INVARIANTS.md",
        "AGENTS.md",
    ):
        fp = root / opt
        if fp.exists():
            cite_files[opt] = fp.read_text(encoding="utf-8")
    # Agent-facing prompt/rule files. Globbed rather than listed because skills
    # and commands are added routinely, and a new one citing the count must not
    # need an edit here to be covered.
    for sub in (".claude/skills", ".claude/commands", ".claude/rules"):
        base = root / sub
        if base.is_dir():
            for fp in sorted(base.rglob("*.md")):
                cite_files[str(fp.relative_to(root))] = fp.read_text(encoding="utf-8")
    drift_files = []
    for name, text in cite_files.items():
        # Find "<n> patterns" / "<n>-pattern" / "<n> banned_patterns" claims and
        # check they equal real_n. The banned_patterns spelling is matched
        # explicitly: "32 banned_patterns" has no whitespace directly before
        # "pattern", so the generic alternative below never saw it.
        for m in re.finditer(r"(\d+)[\s-]+(?:banned_)?pattern", text):
            claimed = int(m.group(1))
            if claimed != real_n and claimed > 5:  # ignore small unrelated numbers
                drift_files.append(f"{name} claims {claimed}")
    if not drift_files:
        ok("D4", f"banned_patterns count {real_n} consistent everywhere it's cited")
    else:
        note("D4", f"config.yaml banned_patterns (actual {real_n})",
             f"count drift: {drift_files}")

    # ── D5 Route table ──────────────────────────────────────────────────────
    print("D5 gate.py routes -> CLAUDE.md + setup-guide.md")
    gate_src = (root / "gate.py").read_text(encoding="utf-8")
    # gate_ops.py / gate_auth.py / gate_memory.py each register their routes
    # onto gate.py's own app with the same @app.get/@app.post decorators, just
    # from inside a registration function. Reading only gate.py left those
    # routes unchecked in both directions -- gate_auth.py added 2026-08-08 for
    # docs/AUTHENTICATION_DESIGN.md Stage 2 (/auth/*); gate_memory.py added
    # 2026-08-09 for the optional default-off memory admin surface
    # (/memory/* + /query/export/html).
    # Allow the path on the next line: `@app.post(\n        "/auth/..."`.
    _decl = r'@app\.(?:get|post|delete|put|patch)\(\s*"([^"]+)"'
    routes: set[str] = set(re.findall(_decl, gate_src))
    for extra_module in ("gate_ops.py", "gate_auth.py", "gate_memory.py"):
        extra_path = root / extra_module
        if extra_path.exists():
            routes |= set(re.findall(_decl, extra_path.read_text(encoding="utf-8")))
    routes = sorted(routes)
    # Ignore the static mount and root; check the meaningful API routes.
    api_routes = [r for r in routes if r not in ("/",)]
    missing = [r for r in api_routes if r not in claude]
    if not missing:
        ok("D5", f"all {len(api_routes)} API routes named in CLAUDE.md")
    else:
        note("D5", "gate.py/gate_ops.py/gate_auth.py/gate_memory.py @app decorators", f"routes absent from CLAUDE.md: {missing}")

    # setup-guide.md's REST section enumerates the same routes with runnable
    # curl invocations, so it drifts the same way CLAUDE.md's table does -- and
    # more damagingly, since a reader copy-pastes from it. Checked BOTH ways:
    # a route the code has but the guide omits, and a route the guide documents
    # that no longer exists (the failure mode a one-way check misses entirely).
    guide_path = root / "setup-guide.md"
    if not guide_path.exists():
        ok("D5", "setup-guide.md absent -- REST-section cross-check skipped")
    else:
        guide = guide_path.read_text(encoding="utf-8")
        # Scope to the REST section only. Outside it the guide is full of
        # filesystem paths (/tmp/..., /etc/...) and other-service endpoints
        # (Ollama's /v1/models) that are not gateway routes.
        sec = re.search(r"(?ms)^## REST API\b.*?(?=^## |\Z)", guide)
        if sec is None:
            note("D5", "setup-guide.md",
                 "no '## REST API' section found -- it was renamed or removed, so the "
                 "route cross-check silently stopped covering anything")
        else:
            body = sec.group(0)
            undocumented = [r for r in api_routes if r not in body]
            # Route-shaped tokens the guide claims: backticked table cells and
            # literal curl URLs against a loopback host:port.
            claimed = set(re.findall(r"`(/[A-Za-z0-9_/*-]*)`", body))
            claimed |= set(re.findall(r"https?://127\.0\.0\.1:\d+(/[A-Za-z0-9_/-]*)", body))
            # The section closes by naming a few harness-console routes to make
            # the point that they live on a DIFFERENT app and port. Those are
            # real routes, so validate them against harness/server.py rather
            # than either ignoring them (no coverage) or flagging them (noise).
            harness_path = root / "harness" / "server.py"
            harness_routes = set(
                re.findall(_decl, harness_path.read_text(encoding="utf-8"))
            ) if harness_path.exists() else set()
            known = set(api_routes) | harness_routes | {"/", "/static/*"}

            def _known(token: str) -> bool:
                if token in known:
                    return True
                # A documented glob ("/soul/*") is satisfied when at least one
                # real route sits under that prefix. Writing the family rather
                # than ten rows is normal prose, not drift -- but a glob over a
                # prefix that no longer exists still gets caught.
                if token.endswith("/*"):
                    prefix = token[:-1]
                    return any(r.startswith(prefix) for r in known)
                return False

            phantom = sorted(c for c in claimed if not _known(c))
            if not undocumented and not phantom:
                ok("D5", f"setup-guide.md's REST section matches all {len(api_routes)} "
                         "routes, with no phantom routes")
            if undocumented:
                note("D5", "gate.py/gate_ops.py/gate_auth.py/gate_memory.py @app decorators",
                     f"routes missing from setup-guide.md's REST section: {undocumented}")
            if phantom:
                note("D5", "gate.py/gate_ops.py/gate_auth.py/gate_memory.py @app decorators",
                     f"setup-guide.md documents routes that do not exist in code: {phantom}")

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
