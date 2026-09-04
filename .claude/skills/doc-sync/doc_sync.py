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
    D6  Hook claims           doc claims about a "stop hook" are backed by .claude/settings.json,
                              across CLAUDE.md, AGENTS.md and .claude/rules/PROJECT_RULES.md
    D7  M5 doctrine           docs/m5-48gb-coding-expectations.md cites the shipped
                               local model tag, Ollama context budget, and timeout values
    D8  Graph node count      the real graph.py add_node() count matches every "<n>-node"
                              claim across the docs and agent-facing prompt files

Exit codes (repo convention):
    0  no drift detected
    2  drift detected (items to reconcile)
    3  env/config error
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

# Repo-relative path prefixes excluded from every "scan agent-facing prompts
# for a stale count/claim" pass (D4, D6, D8). Shared across all three so the
# same exclusion doesn't drift into three slightly different lists.
#   - doc-sync's own directories: verify.sh's test fixtures are literal
#     strings of the exact claims these checks look for, and scanning them
#     makes the checker flag its own test data as live drift.
#   - NEW_SKILL.md: a deliberately version-pinned snapshot ("Baseline of this
#     document: main @ <hash>... Supersedes the <date> baseline") describing
#     the repo as of a past commit. Its counts are correct FOR THAT BASELINE,
#     not the current tree, so as soon as the real count changes again this
#     file would otherwise be flagged and an agent misdirected to "fix" a
#     historical record (Codex P2 on PR #1308).
_AGENT_SCAN_EXCLUDE = (
    ".claude/skills/doc-sync",
    ".codex/skills/doc-sync",
    ".codex/skills/Cyclaw-Sandbox/NEW_SKILL.md",
)


def _agent_scan_excluded(rel_path: str) -> bool:
    return any(rel_path == p or rel_path.startswith(p + "/") for p in _AGENT_SCAN_EXCLUDE)

_drift: list[dict] = []


def note(check: str, source_of_truth: str, detail: str) -> None:
    _drift.append({"check": check, "truth": source_of_truth, "detail": detail})
    print(f"  DRIFT [{check}] {detail}\n         source of truth: {source_of_truth}")


def ok(check: str, detail: str) -> None:
    print(f"  ok    [{check}] {detail}")


def _d7_labels_for_key(key: str) -> list[str]:
    # Regex fragments that name this key in the M5 doctrine.
    if key == "macos.OLLAMA_CONTEXT_LENGTH":
        return [r"OLLAMA_CONTEXT_LENGTH", r"num_ctx"]
    if key == "models.local_llm.model":
        return [r"model", r"local_llm", r"qwen"]
    segment = re.escape(key.rsplit(".", 1)[-1])
    if key == "api.graph_timeout_sec":
        return [segment, r"graph\s+timeout"]
    if key == "models.local_llm.timeout_sec":
        return [segment, r"timeout"]
    return [segment]


def _d7_value_key_adjacent(doc: str, key: str, value: object) -> bool:
    # Value must sit on a line that names the key (or on the next line).
    val = re.escape(str(value))
    val_pat = rf"(?<!\d){val}(?!\d)"
    label_pat = "(?:" + "|".join(_d7_labels_for_key(key)) + ")"
    same = re.compile(
        rf"(?im)^.*(?:{label_pat}).*{val_pat}.*$|^.*{val_pat}.*(?:{label_pat}).*$"
    )
    if same.search(doc):
        return True
    nxt = re.compile(rf"(?im)^.*(?:{label_pat}).*\n[^\n]*{val_pat}")
    return nxt.search(doc) is not None


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
        "api.graph_timeout_sec": r"graph_timeout",
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
                "models.local_llm.model": cfg["models"]["local_llm"]["model"],
                "models.local_llm.max_tokens": cfg["models"]["local_llm"]["max_tokens"],
                "models.local_llm.timeout_sec": cfg["models"]["local_llm"]["timeout_sec"],
                "api.graph_timeout_sec": cfg["api"]["graph_timeout_sec"],
                "retrieval.max_context_tokens": cfg["retrieval"]["max_context_tokens"],
                "macos.OLLAMA_CONTEXT_LENGTH": int(context_match.group(1)),
            }
            missing = [
                f"{key}={value}"
                for key, value in expected.items()
                if not _d7_value_key_adjacent(m5_doc, key, value)
            ]
            if missing:
                note("D7", "config.yaml + macos/ollama-mlx.env",
                     "M5 doctrine omits shipped value(s) next to their keys: "
                     + ", ".join(missing))
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
    for sub in (".claude/skills", ".claude/commands", ".claude/rules", ".codex"):
        base = root / sub
        if base.is_dir():
            for fp in sorted(base.rglob("*.md")):
                rel = str(fp.relative_to(root))
                if not _agent_scan_excluded(rel):
                    cite_files[rel] = fp.read_text(encoding="utf-8")
    # A bare "<n> patterns" claim is ambiguous outside CLAUDE.md/config.yaml --
    # widening the scan to .codex/ surfaced a real unrelated claim ("17 patterns
    # for matching filenames") that this same regex would flag as sanitizer
    # drift (Codex P2 on PR #1308). Require the word to be spelled
    # "banned_pattern(s)" (unambiguous on its own) OR sit near a sanitizer/
    # injection-filter context word on the same line.
    _pattern_context = re.compile(r"banned|sanitiz|injection", re.I)
    _PATTERN_CONTEXT_WINDOW_CHARS = 60
    drift_files = []
    for name, text in cite_files.items():
        for m in re.finditer(r"(\d+)[\s-]+(?:(banned_)?pattern)", text):
            claimed = int(m.group(1))
            if claimed == real_n or claimed <= 5:  # ignore small unrelated numbers
                continue
            if m.group(2):  # explicit "banned_pattern(s)" spelling, unambiguous
                drift_files.append(f"{name} claims {claimed}")
                continue
            # A bounded character window, not just the physical line: hard-
            # wrapped Markdown routinely puts the context word and the count
            # on adjacent lines ("Sanitizer protection includes\n99
            # patterns."), which a same-line-only check missed entirely
            # (Codex P2 on PR #1308).
            lo = max(0, m.start() - _PATTERN_CONTEXT_WINDOW_CHARS)
            hi = min(len(text), m.end() + _PATTERN_CONTEXT_WINDOW_CHARS)
            if _pattern_context.search(text[lo:hi]):
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
            # server.py owns 23 of the 29 guarded routes; the other six
            # (/api/agent/*) are in agent_routes.py and the /api/auth/*
            # surface is in auth_routes.py. Reading only server.py meant a
            # doc citing a REAL route from either of those was reported as a
            # phantom -- a false positive in the one direction of D5 that
            # nothing else covers.
            harness_routes: set[str] = set()
            for _hname in ("server.py", "agent_routes.py", "auth_routes.py"):
                _hp = root / "harness" / _hname
                if _hp.exists():
                    harness_routes |= set(
                        re.findall(_decl, _hp.read_text(encoding="utf-8"))
                    )
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
    has_stop_hook = '"Stop"' in settings
    # Two blind spots, both found the hard way: this check read CLAUDE.md
    # ONLY, so PROJECT_RULES.md sat on a flat "the stop hook blocks
    # --force-with-lease" while D6 reported clean -- exactly the claim it
    # exists to catch. And the substring was "stop hook", which never matched
    # the hyphenated "stop-hook" spelling CLAUDE.md itself uses. Every rule
    # file that can make the claim is scanned now, and attribution is judged
    # per file: one doc getting it right does not excuse another getting it
    # wrong.
    _runtime_attribution = re.compile(r"session[- ]runtime|not wired|runtime[- ]enforced", re.I)
    # Bare co-occurrence with "stop hook" was too broad: CLAUDE.md's own Kimi
    # passage ("Kimi has neither the GitHub MCP tools nor the session
    # stop-hook...") mentions the phrase while denying it applies, which is not
    # the claim this check exists to catch and is not a "session runtime"-style
    # qualifier either -- the paragraph-level fix from the previous round turned
    # that true statement into a false positive against a canonical, accurate
    # doc (Codex P2 on PR #1308). Narrowed to require an assertive control verb
    # near the phrase, which is what actually distinguishes a claim of
    # enforcement ("the stop hook blocks...", "...the stop hook requires...")
    # from prose that merely mentions or denies one.
    _stop_hook_claim = re.compile(
        r"stop[- ]hook\W+(?:\w+\W+){0,6}?(?:blocks?|enforces?|requires?|prevents?|rejects?)"
        r"|(?:blocks?|enforces?|requires?|prevents?|rejects?)(?:\W+\w+){0,6}?\W+stop[- ]hook",
        re.I,
    )
    hook_docs = {"CLAUDE.md": claude}
    for opt in (
        "AGENTS.md", ".claude/rules/PROJECT_RULES.md",
        # The canonical GitHub Copilot prompt -- D8 already scans it (a fourth
        # agent surface alongside Claude/.codex); D6 needs the same coverage or
        # an unsupported hook claim delivered to Copilot bypasses the checker
        # entirely (Codex P2 on PR #1308).
        ".github/copilot-instructions.md",
    ):
        fp = root / opt
        if fp.exists():
            hook_docs[opt] = fp.read_text(encoding="utf-8")
    # doc-sync's own verify.sh (both trees) embeds the exact claim/attribution
    # strings D6 looks for, as literal test-fixture printf data -- scanning
    # its own directory makes the checker self-flag on its own test data, not
    # a real agent-facing claim. Shares _AGENT_SCAN_EXCLUDE with D4/D8.
    for sub in (".claude/skills", ".claude/commands", ".codex"):
        base = root / sub
        if base.is_dir():
            for fp in sorted(base.rglob("*.md")):
                rel = fp.relative_to(root).as_posix()
                if not _agent_scan_excluded(rel):
                    hook_docs[rel] = fp.read_text(encoding="utf-8")
            # Skill shell scripts carry the same enforcement claims as their
            # SKILL.md (bootstrap.sh:9 "pins the git identity the stop hook
            # requires"), and a *.md-only glob left them unscanned (Codex P2
            # on PR #1308).
            for fp in sorted(base.rglob("*.sh")):
                rel = fp.relative_to(root).as_posix()
                if not _agent_scan_excluded(rel):
                    hook_docs[rel] = fp.read_text(encoding="utf-8")
    # Per-claim-unit, not per-sentence: plain "split on sentence punctuation"
    # still failed on markdown bullet lists, which often carry no terminal
    # punctuation at all. Codex reproduced it with two adjacent bullets --
    # "- The stop hook blocks force pushes" / "- The pre-commit hook is not
    # wired" -- which sentence-splitting left as ONE unit (no ".", "!" or "?"
    # between them), so the second bullet's qualifier excused the first
    # bullet's unrelated claim. A paragraph that looks like a list (two or
    # more lines starting with a list marker) is split by list item instead:
    # each new marker line starts a fresh unit, and any following
    # continuation line (no marker) folds into the item above it, so a single
    # wrapped bullet still reads as one claim. A paragraph that is plain
    # prose (fewer than two marker lines) keeps the sentence split from the
    # previous round, since CLAUDE.md's real hand-wrapped sentences rely on
    # exactly that: the claim and its attribution sit on different raw lines
    # of the same soft-wrapped sentence, and splitting by raw line there
    # would separate them incorrectly.
    _sentence_split = re.compile(r"(?<=[.!?])\s+")
    _list_item = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
    def _claim_units(text: str) -> list[str]:
        units: list[str] = []
        for para in re.split(r"\n\s*\n", text):
            lines = para.split("\n")
            if sum(1 for line in lines if _list_item.match(line)) >= 2:
                current: list[str] = []
                for line in lines:
                    if _list_item.match(line) and current:
                        units.append(" ".join(current))
                        current = [line]
                    else:
                        current.append(line)
                if current:
                    units.append(" ".join(current))
            else:
                units.extend(_sentence_split.split(para))
        return units
    # KNOWN LIMIT (Codex P2 on PR #1308, deliberately NOT "fixed" further):
    # whole-unit attribution lets an unrelated qualifier on a DIFFERENT hook
    # excuse a real claim about the stop hook -- "The pre-commit hook is not
    # wired, but the stop hook blocks force pushes." carries both "not wired"
    # and "stop hook...blocks" in one sentence, and "not wired" describes the
    # pre-commit hook, not this one. A character/word-proximity anchor to the
    # "stop hook" phrase does NOT fix this: measured on this exact sentence
    # pair, the malicious qualifier sits 10 chars from "stop hook" while the
    # legitimate one in CLAUDE.md's real "if applied by the session runtime"
    # phrasing sits 38 chars away -- no fixed window excludes the first
    # without also excluding the second. Correctly scoping a qualifier to its
    # subject needs a real dependency parse, not a regex window; this
    # checker's remit is mechanical fact-checking (see SKILL.md), not NLP.
    # Rather than ship a change that only moves which sentence shape slips
    # through, this is accepted as a precision limit: a sentence naming TWO
    # hooks with different enforcement claims in one unit can still bypass
    # D6. Split such a sentence into two during Step 3's manual pass if one
    # is ever found.
    def _naive_claims(text: str) -> list[str]:
        return [
            unit for unit in _claim_units(text)
            if _stop_hook_claim.search(unit) and not _runtime_attribution.search(unit)
        ]
    naive_docs = [name for name, text in hook_docs.items() if _naive_claims(text)]
    claiming_docs = [n for n, t in hook_docs.items() if _stop_hook_claim.search(t)]
    if naive_docs and not has_stop_hook:
        note("D6", ".claude/settings.json (no Stop hook wired)",
             f"{', '.join(naive_docs)} reference a 'stop hook' as if repo-wired, but "
             "settings.json wires no Stop hook — wire it, or state that the enforcement is "
             "applied by the session runtime")
    elif claiming_docs and has_stop_hook:
        ok("D6", f"stop-hook claim backed by a wired Stop hook ({len(claiming_docs)} doc(s) cite it)")
    else:
        ok("D6", f"stop-hook claim absent or accurately attributed to the runtime ({len(hook_docs)} rule file(s) scanned)")

    # ── D8 Graph node count ─────────────────────────────────────────────────
    print("D8 Graph node count -> graph.py add_node()")
    graph_path = root / "graph.py"
    # A missing graph.py must never take the checker outside its documented 0/2/3
    # exit contract. Reading it unconditionally crashed with a traceback and exit 1
    # on any tree without it -- including verify.sh's own D7 fixture, where the
    # surrounding `|| true` hid the crash and the self-test still reported success
    # (Codex P2 on PR #1308). Absence is reported as drift below, not ok() --
    # graph.py is the core policy file, and silently succeeding when it is missing
    # would hide exactly the failure this check exists to catch.
    graph_src = graph_path.read_text(encoding="utf-8") if graph_path.exists() else None
    graph_tree = None
    if graph_src is not None:
        # ast, not a regex: a textual `.add_node(` count includes comments and
        # string literals, so documenting a retired registration in a comment
        # (e.g. "# Retired: graph.add_node(...)") silently inflates the reported
        # topology (Codex P2 on PR #1308). Counting actual Call nodes whose
        # attribute is add_node only sees executable code. A SyntaxError is
        # treated the same as a missing file below -- falling back to the regex
        # here would silently reintroduce the exact bug this fix closes.
        try:
            graph_tree = ast.parse(graph_src, filename=str(graph_path))
        except SyntaxError:
            graph_tree = None
    if graph_src is None or graph_tree is None:
        # graph.py is the core policy file, not an optional citation like D7's M5
        # doctrine inputs -- treating its absence as ok() understated the same
        # comment's own "absent-safe like D7" claim: D7 reports an unreadable input
        # via note() (a drift item), not ok(). An incomplete checkout, an
        # accidental rename/deletion, or a syntax error in graph.py should all read
        # the same way, not as success (Codex P2 on PR #1308).
        reason = "graph.py not found" if graph_src is None else "graph.py does not parse as Python"
        note("D8", "graph.py", f"{reason} -- node-count cross-check could not run")
        real_nodes = 0
        graph_src = None  # normalizes both failure branches below
    else:
        real_nodes = sum(
            1
            for node in ast.walk(graph_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_node"
        )
    if graph_src is not None and real_nodes == 0:
        note("D8", "graph.py .add_node() calls", "no add_node() calls found — parser or graph.py changed shape")
    elif graph_src is not None:
        # Same shape as D4, and added for the same reason: four separate docs
        # (a command doc, its .codex mirror, a work note and a NeMo phase doc)
        # all sat on "10-node" after the two pre_action_hook_* nodes landed,
        # while nothing checked the claim. A number repeated across agent-facing
        # files is exactly what a mechanical check is for.
        node_files = {"CLAUDE.md": claude}
        for opt in (
            "README.md", "AGENTS.md", "INVARIANTS.md", "docs/THREAT_MODEL.md",
            # The canonical GitHub Copilot prompt -- a fourth agent surface
            # alongside Claude/.codex, easy to forget precisely because nothing
            # else in this file scans it (Codex P2 on PR #1308).
            ".github/copilot-instructions.md",
        ):
            fp = root / opt
            if fp.exists():
                node_files[opt] = fp.read_text(encoding="utf-8")
        # Scope is deliberately the live authorities + agent-facing prompt files,
        # the same set D4 settled on -- NOT docs/** wholesale. Dated audits,
        # archived memories and superseded NeMo phase plans correctly describe
        # the graph as it was when they were written; flagging them would make
        # D8 fire 17 times on a healthy tree and be ignored within a week.
        for sub in (".claude/skills", ".claude/commands", ".claude/rules", ".codex"):
            base = root / sub
            if base.is_dir():
                for fp in sorted(base.rglob("*.md")):
                    rel = str(fp.relative_to(root))
                    if not _agent_scan_excluded(rel):
                        node_files[rel] = fp.read_text(encoding="utf-8")
        node_drift = []
        for name, text in node_files.items():
            for m in re.finditer(r"(~\s?)?(\d+)[\s-]+nodes?\b", text):
                # A leading "~" is generic sizing advice ("avoid monolithic
                # graphs beyond ~10 nodes"), not a claim about CyClaw's own
                # topology -- python-coding-agent/SKILL.md:241 triggered a
                # false positive here (Codex P2 on PR #1308) because the
                # regex had no way to distinguish "~10 nodes" from "10-node".
                if m.group(1):
                    continue
                if int(m.group(2)) != real_nodes:
                    node_drift.append(f"{name} claims {m.group(2)}-node")
        if node_drift:
            note("D8", f"graph.py has {real_nodes} add_node() calls",
                 f"stale graph node-count claims: {sorted(set(node_drift))}")
        else:
            ok("D8", f"node count {real_nodes} consistent across {len(node_files)} scanned file(s)")

    result = {"drift_count": len(_drift), "drift": _drift}
    if args.json:
        print(json.dumps(result, indent=2))
    print(f"\n{len(_drift)} drift item(s) found")
    return 2 if _drift else 0


if __name__ == "__main__":
    sys.exit(main())
