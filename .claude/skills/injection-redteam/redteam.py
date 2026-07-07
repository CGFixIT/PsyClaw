#!/usr/bin/env python3
"""redteam.py – drive the injection probe corpus through the shipped sanitizer.

Usage:
    python .claude/skills/injection-redteam/redteam.py [--probes PATH] [--config config.yaml] [--json]

Runs every probe in probes.yaml through utils.sanitizer.check_input against the
REAL config.yaml (or a config you point at) and reports:
  - new_bypasses   : expect=blocked probes that got through and are NOT flagged
                     open_finding — i.e. a REGRESSION (a pattern stopped working)
  - open_findings  : expect=blocked probes that got through and ARE flagged
                     open_finding — the known gaps the loop is working to close
  - fixed_findings : probes flagged open_finding that are now blocked — drop the
                     flag in probes.yaml to bank them as regression anchors
  - false_positives: expect=allowed probes that WERE blocked (usability gap)

Exit codes (repo convention):
    0  baseline matches: no new bypasses, no false positives (open findings ok)
    2  a NEW bypass (regression) or a false positive appeared — act on it
    3  env/config error (missing deps, unreadable files)

Requires the project deps (PyYAML + utils importable). Run from the repo root,
or with the repo root on PYTHONPATH. Each run re-reads config fresh; run in a
NEW process after editing config.yaml — the sanitizer lru_caches by config path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_yaml(path: Path):
    try:
        import yaml
    except ImportError:
        print("env error: PyYAML not installed; run inside the project venv", file=sys.stderr)
        raise SystemExit(3)
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:  # type: ignore[name-defined]
        print(f"env error: cannot read {path}: {exc}", file=sys.stderr)
        raise SystemExit(3)


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    repo_root = here.parents[2]
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--probes", type=Path, default=here / "probes.yaml")
    p.add_argument("--config", type=Path, default=repo_root / "config.yaml")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = p.parse_args(argv)

    # Import the project's sanitizer — the whole point is to test the REAL one.
    sys.path.insert(0, str(repo_root))
    try:
        from utils.sanitizer import check_input
        from utils.errors import PromptInjectionError
    except ImportError as exc:
        print(f"env error: cannot import utils.sanitizer ({exc}). "
              "Run from the repo root inside the project venv.", file=sys.stderr)
        return 3

    doc = _load_yaml(args.probes)
    probes = (doc or {}).get("probes", [])
    if not probes:
        print("env error: no probes found", file=sys.stderr)
        return 3

    new_bypasses: list[dict] = []
    open_findings: list[dict] = []
    fixed_findings: list[dict] = []
    false_positives: list[dict] = []
    blocked = allowed = 0

    for probe in probes:
        text = probe["text"]
        expect = probe["expect"]
        is_open = bool(probe.get("open_finding"))
        try:
            check_input(text, str(args.config))
            was_blocked = False
        except PromptInjectionError:
            was_blocked = True

        if was_blocked:
            blocked += 1
        else:
            allowed += 1

        if expect == "blocked" and not was_blocked:
            (open_findings if is_open else new_bypasses).append(probe)
        elif expect == "blocked" and was_blocked and is_open:
            fixed_findings.append(probe)
        elif expect == "allowed" and was_blocked:
            false_positives.append(probe)

    result = {
        "total": len(probes),
        "blocked": blocked,
        "allowed": allowed,
        "new_bypasses": new_bypasses,
        "open_findings": open_findings,
        "fixed_findings": fixed_findings,
        "false_positives": false_positives,
    }

    def _dump(label: str, rows: list[dict]) -> None:
        print(f"\n{label} ({len(rows)}):")
        for r in rows:
            print(f"  [{r['id']}/{r['family']}] {r['text']!r}")

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"probes: {len(probes)}  blocked: {blocked}  allowed: {allowed}")
        if new_bypasses:
            _dump("NEW BYPASSES — regression, expected blocked, got through", new_bypasses)
        if false_positives:
            _dump("FALSE POSITIVES — legit query blocked", false_positives)
        if open_findings:
            _dump("OPEN FINDINGS — known gaps still to close", open_findings)
        if fixed_findings:
            _dump("FIXED — now blocked; drop open_finding flag in probes.yaml", fixed_findings)
        if not (new_bypasses or false_positives):
            print("\nBaseline OK: no new bypasses, no false positives.")

    return 2 if (new_bypasses or false_positives) else 0


if __name__ == "__main__":
    sys.exit(main())
