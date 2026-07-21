#!/usr/bin/env python3
"""extract_pins.py — normalize every pinned package/version across CyClaw's
four install surfaces (pyproject.toml, constraints.txt, requirements.txt,
.github/workflows/environment.yml) into one JSON table.

Usage:
    python3 .claude/skills/verify-deps/extract_pins.py [--repo-root PATH]

This is a companion to dep-guard, not a replacement: dep-guard's
check_deps.py already validates pyproject.toml <-> constraints.txt <->
environment.yml agreement (D1-D10) with mutation-tested rigor — this script
imports its parsing helpers directly rather than re-implementing them, so
there is one source of truth for "how do we parse a pin line."

What this adds that dep-guard does not check:
  - requirements.txt is parsed and cross-checked against constraints.txt too
    (dep-guard never reads requirements.txt at all — grep the script, it
    has zero references to the file). A stale requirements.txt pin would
    pass every dep-guard check silently.
  - Output is a flat, normalized {package: {file: version}} table meant to
    be handed to a currency check (verify-deps/SKILL.md Step 2) or read by
    a human — not a pass/fail gate.

Exit codes: 0 always, unless a required pin file is missing/unparseable (3).
This script does not FAIL on drift — it reports. Drift detection belongs to
dep-guard (D6 for pyproject<->constraints, and REQ_MISMATCH here as an
extension for requirements.txt<->constraints); this script's job is to
produce the table both dep-guard-style checks and a currency check consume.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dep-guard"))
import check_deps as _dep_guard  # noqa: E402  (sibling-skill import, path set above)

_ENV_YML_FILE = ".github/workflows/environment.yml"
_ENV_SKIP = {"python", "pip"}


def _load_requirements_reqs(text: str) -> list[_dep_guard.Req]:
    """requirements.txt uses the same `name==version  # comment` grammar as
    constraints.txt (both are pip requirements-file format), so the same
    parser applies — dep-guard just never calls it on this file."""
    return _dep_guard._load_constraints_reqs(text)


def _load_environment_reqs(text: str) -> list[_dep_guard.Req]:
    reqs: list[_dep_guard.Req] = []
    for line in text.splitlines():
        m = _dep_guard._ENV_PIP_PIN_RE.match(line) or _dep_guard._ENV_CONDA_PIN_RE.match(line)
        if m is None:
            continue
        name, version = _dep_guard._normalize(m.group(1)), m.group(2)
        if name in _ENV_SKIP:
            continue
        reqs.append(_dep_guard.Req(name=name, extras="", spec=f"=={version}"))
    return reqs


def build_table(root: Path) -> dict[str, dict[str, str]]:
    pyproject_path = root / "pyproject.toml"
    constraints_path = root / "constraints.txt"
    requirements_path = root / "requirements.txt"
    env_path = root / _ENV_YML_FILE

    import tomllib

    with pyproject_path.open("rb") as fh:
        pyproject = tomllib.load(fh)

    sources: dict[str, list[_dep_guard.Req]] = {
        "pyproject.toml": _dep_guard._load_pyproject_reqs(pyproject),
        "constraints.txt": _dep_guard._load_constraints_reqs(
            constraints_path.read_text(encoding="utf-8")
        ),
    }
    if requirements_path.exists():
        sources["requirements.txt"] = _load_requirements_reqs(
            requirements_path.read_text(encoding="utf-8")
        )
    if env_path.exists():
        sources["environment.yml"] = _load_environment_reqs(env_path.read_text(encoding="utf-8"))

    table: dict[str, dict[str, str]] = {}
    for file_label, reqs in sources.items():
        for req in reqs:
            version = _dep_guard._pin(req.spec)
            if version is None:
                continue
            table.setdefault(req.name, {})[file_label] = version
    return table


def find_requirements_drift(table: dict[str, dict[str, str]]) -> list[str]:
    """Packages where requirements.txt disagrees with constraints.txt —
    the one cross-file agreement dep-guard's D6 does not cover."""
    drift = []
    for name, by_file in sorted(table.items()):
        req_v = by_file.get("requirements.txt")
        con_v = by_file.get("constraints.txt")
        if req_v is not None and con_v is not None and req_v != con_v:
            drift.append(f"{name}: requirements.txt=={req_v} vs constraints.txt=={con_v}")
    return drift


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", type=Path, default=None)
    p.add_argument("--json", action="store_true", help="emit the raw table as JSON")
    args = p.parse_args(argv)

    root = args.repo_root or Path(__file__).resolve().parents[3]
    required = ["pyproject.toml", "constraints.txt"]
    missing = [f for f in required if not (root / f).exists()]
    if missing:
        print(f"env error: not found: {', '.join(missing)}", file=sys.stderr)
        return 3

    try:
        table = build_table(root)
    except (OSError, ValueError) as exc:
        print(f"env error: could not parse pins: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(table, indent=2, sort_keys=True))
        return 0

    print(f"{'package':<24} {'pyproject.toml':<16} {'constraints.txt':<16} "
          f"{'requirements.txt':<18} {'environment.yml':<16}")
    for name, by_file in sorted(table.items()):
        print(
            f"{name:<24} "
            f"{by_file.get('pyproject.toml', '-'):<16} "
            f"{by_file.get('constraints.txt', '-'):<16} "
            f"{by_file.get('requirements.txt', '-'):<18} "
            f"{by_file.get('environment.yml', '-'):<16}"
        )

    drift = find_requirements_drift(table)
    print()
    if drift:
        print(f"requirements.txt <-> constraints.txt drift ({len(drift)}):")
        for line in drift:
            print(f"  DRIFT  {line}")
    else:
        print("requirements.txt <-> constraints.txt: no drift (dep-guard does not check this pair)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
