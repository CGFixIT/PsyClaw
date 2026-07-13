#!/usr/bin/env python3
"""check_deps.py – static validation of CyClaw's dependency-pin invariants.

Usage:
    python3 .claude/skills/dep-guard/check_deps.py [--repo-root PATH]
                                                   [--strict] [--json]

CyClaw's install is a minefield of pin invariants (CLAUDE.md §4 "Dependencies"):
a few pins are load-bearing and break the resolver, the runtime, or the security
posture if bumped in isolation. Those rules live only in prose and comments — no
automated check enforces them. This does, statically, with zero third-party
imports (tomllib is stdlib on 3.12), so it runs in a fresh clone before any
`pip install`.

Cross-file: `constraints.txt` (its own header) says its versions "MUST match
exactly" the direct pins in `pyproject.toml`. This checker is the thing that
actually verifies that claim.

Severity:
    FAIL  a documented pin invariant is broken (exit 2).
    WARN  a reproducibility/soft-pin drift an operator may accept (exit 0;
          --strict escalates every WARN to a failure).
    INFO  advisory context (risk-accepted CVE); never affects the exit code.

Exit codes (repo convention):
    0  pins hold (warnings may be present without --strict)
    2  a FAIL check tripped (or a WARN under --strict)
    3  env/config error (pyproject.toml / constraints.txt missing or unparseable)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

# The pydantic family bumps in lock-step: pydantic 2.13.4 hard-pins
# pydantic-core==2.46.4 (exact ==). Bumping one alone makes
# `pip install -c constraints.txt` unresolvable (CLAUDE.md §4). Drift from this
# documented pair is a conscious update, not a silent one -> WARN, not FAIL.
_PYDANTIC_LOCKSTEP = {"pydantic": "2.13.4", "pydantic-core": "2.46.4"}

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


class Req(NamedTuple):
    name: str          # PEP 503-normalized (lowercase, [-_.] runs -> "-")
    extras: str        # e.g. "[standard]" or "" if none
    spec: str          # the full version specifier, e.g. "==0.49.0" or "<2"


_REQ_RE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^#;]*?)\s*(?:[#;].*)?$"
)


def _normalize(name: str) -> str:
    """PEP 503 canonical form so pyproject/constraints names compare cleanly."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_req(line: str) -> Req | None:
    """Parse one PEP 508-ish requirement line; None for blanks/comments."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    m = _REQ_RE.match(stripped)
    if not m:
        return None
    name, extras, spec = m.group(1), m.group(2) or "", m.group(3).strip()
    return Req(_normalize(name), extras, spec)


def _pin(spec: str) -> str | None:
    """Return the exact-pinned version for an '==X' spec, else None."""
    m = re.match(r"^==\s*([^\s,]+)$", spec)
    return m.group(1) if m else None


def _major(version: str) -> int | None:
    """Leading integer of a version (local tag / suffix ignored)."""
    m = re.match(r"^(\d+)", version)
    return int(m.group(1)) if m else None


def _load_pyproject_reqs(pyproject: dict) -> list[Req]:
    """All direct requirements from [project.dependencies] + optional-dependencies."""
    project = pyproject.get("project", {})
    raw: list[str] = list(project.get("dependencies", []))
    for group in (project.get("optional-dependencies", {}) or {}).values():
        raw.extend(group)
    return [r for r in (_parse_req(line) for line in raw) if r is not None]


def _load_constraints_reqs(text: str) -> list[Req]:
    return [r for r in (_parse_req(line) for line in text.splitlines()) if r is not None]


def run_checks(py_reqs: list[Req], con_reqs: list[Req]) -> None:
    con_by_name: dict[str, Req] = {r.name: r for r in con_reqs}
    py_by_name: dict[str, Req] = {r.name: r for r in py_reqs}
    all_reqs = py_reqs + con_reqs

    # ── D1 pydantic <-> pydantic-core lock-step (constraints.txt) ───────────
    print("D1 pydantic / pydantic-core lock-step")
    pyd = con_by_name.get("pydantic")
    core = con_by_name.get("pydantic-core")
    if pyd is None or core is None:
        fail("D1", "constraints.txt must pin BOTH pydantic and pydantic-core "
                   "(pydantic hard-pins an exact pydantic-core; one alone breaks the resolver)")
    else:
        pyd_v, core_v = _pin(pyd.spec), _pin(core.spec)
        if pyd_v is None or core_v is None:
            fail("D1", f"pydantic ({pyd.spec}) and pydantic-core ({core.spec}) must both be "
                       "exact '==' pins in constraints.txt")
        elif (pyd_v, core_v) != (_PYDANTIC_LOCKSTEP["pydantic"], _PYDANTIC_LOCKSTEP["pydantic-core"]):
            warn("D1", f"pydantic=={pyd_v} / pydantic-core=={core_v} drift from the documented "
                       f"lock-step ({_PYDANTIC_LOCKSTEP['pydantic']} / {_PYDANTIC_LOCKSTEP['pydantic-core']}); "
                       "bump both together and update this checker + CLAUDE.md §4 in the same commit")
        else:
            ok("D1", f"pydantic=={pyd_v} pinned in lock-step with pydantic-core=={core_v}")

    # ── D2 numpy held below 2.x ─────────────────────────────────────────────
    print("D2 numpy < 2 (numpy 2 removes np.float_, breaks chromadb/onnxruntime)")
    numpy_reqs = [r for r in all_reqs if r.name == "numpy"]
    if not numpy_reqs:
        warn("D2", "numpy is not pinned anywhere — it is a hard dep; expected a <2 pin")
    else:
        bad = []
        for r in numpy_reqs:
            pin_v = _pin(r.spec)
            if pin_v is not None:
                if (_major(pin_v) or 0) >= 2:
                    bad.append(f"numpy=={pin_v}")
            elif not re.search(r"<\s*2", r.spec):
                # No exact pin AND no explicit "< 2" upper bound -> admits numpy 2.x
                bad.append(f"numpy{r.spec or ' (unbounded)'}")
        if bad:
            fail("D2", "numpy is not held below 2.x: " + ", ".join(bad)
                       + " — dependabot ignores numpy>=2.0.0 for this reason")
        else:
            ok("D2", f"numpy held < 2 ({', '.join(sorted({r.spec for r in numpy_reqs}))})")

    # ── D3 torch is the CPU build ───────────────────────────────────────────
    print("D3 torch pinned to a +cpu build")
    torch_reqs = [r for r in all_reqs if r.name == "torch"]
    if not torch_reqs:
        info("D3", "torch not pinned in these files (optional torch-cpu extra may live elsewhere)")
    else:
        bad = [r.spec for r in torch_reqs if "+cpu" not in r.spec]
        if bad:
            fail("D3", "torch pin(s) missing the +cpu local build tag: " + ", ".join(bad)
                       + " — the default index pulls a CUDA wheel (CLAUDE.md §4)")
        else:
            ok("D3", f"torch pinned CPU-only ({', '.join(sorted({r.spec for r in torch_reqs}))})")

    # ── D4 uvicorn carries no extras in constraints.txt ─────────────────────
    print("D4 uvicorn has no [extra] in constraints.txt (pip >= 26.1.2 rejects it)")
    con_uv = con_by_name.get("uvicorn")
    if con_uv is None:
        warn("D4", "uvicorn is not pinned in constraints.txt")
    elif con_uv.extras:
        fail("D4", f"constraints.txt uvicorn carries an extra {con_uv.extras} — pip >= 26.1.2 "
                   "rejects extras in a -c constraints file; keep [standard] in pyproject/requirements only")
    else:
        ok("D4", "constraints.txt uvicorn has no extra (the [standard] extra stays in pyproject)")
    py_uv = py_by_name.get("uvicorn")
    if py_uv is not None and "standard" not in py_uv.extras:
        warn("D4", f"pyproject uvicorn lacks the [standard] extra (got {py_uv.extras or 'none'}) — "
                   "the runtime expects uvicorn[standard]")

    # ── D5 constraints.txt uses exact pins (reproducibility) ────────────────
    print("D5 constraints.txt pins are all exact '=='")
    loose = [f"{r.name}{r.extras}{r.spec or ' (no version)'}"
             for r in con_reqs if _pin(r.spec) is None]
    if loose:
        warn("D5", "non-exact pins in constraints.txt defeat its reproducibility purpose: "
                   + ", ".join(loose))
    else:
        ok("D5", f"all {len(con_reqs)} constraints.txt entries are exact '==' pins")

    # ── D6 constraints.txt agrees with pyproject exact pins ─────────────────
    print("D6 constraints.txt versions match pyproject exact pins")
    mismatches = []
    for name, py_req in py_by_name.items():
        py_v = _pin(py_req.spec)
        con_req = con_by_name.get(name)
        if py_v is None or con_req is None:
            continue
        con_v = _pin(con_req.spec)
        if con_v is not None and con_v != py_v:
            mismatches.append(f"{name}: pyproject=={py_v} vs constraints=={con_v}")
    if mismatches:
        fail("D6", "constraints.txt contradicts pyproject (its header says they MUST match): "
                   + "; ".join(mismatches))
    else:
        ok("D6", "every package pinned in both files agrees on version")

    # ── D7 chromadb CVE posture (advisory) ──────────────────────────────────
    print("D7 chromadb pin (risk-accepted CVE, advisory)")
    chroma = con_by_name.get("chromadb") or py_by_name.get("chromadb")
    if chroma is None:
        info("D7", "chromadb not pinned in these files")
    else:
        info("D7", f"chromadb{chroma.spec} — CVE-2026-45829 risk-accepted for embedded "
                   "PersistentClient only (SECURITY.md); do NOT switch to the HTTP client or file a fix PR")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", type=Path, default=None)
    p.add_argument("--strict", action="store_true",
                   help="treat WARN as failure (exit 2 on any warning)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    root = args.repo_root or Path(__file__).resolve().parents[3]
    pyproject_path = root / "pyproject.toml"
    constraints_path = root / "constraints.txt"
    if not pyproject_path.exists() or not constraints_path.exists():
        missing = [str(p) for p in (pyproject_path, constraints_path) if not p.exists()]
        print(f"env error: not found: {', '.join(missing)}", file=sys.stderr)
        return 3
    try:
        with pyproject_path.open("rb") as fh:
            pyproject = tomllib.load(fh)
        constraints_text = constraints_path.read_text(encoding="utf-8")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"env error: could not read pins: {exc}", file=sys.stderr)
        return 3

    print("== dep-guard: static dependency-pin invariants ==")
    run_checks(_load_pyproject_reqs(pyproject), _load_constraints_reqs(constraints_text))

    strict_fail = args.strict and _warns
    print(f"\n{len(_fails)} failure(s), {len(_warns)} warning(s)"
          + (" (--strict: warnings count as failures)" if args.strict else ""))
    if args.json:
        print(json.dumps({"fails": _fails, "warns": _warns, "strict": args.strict}, indent=2))
    return 2 if (_fails or strict_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
