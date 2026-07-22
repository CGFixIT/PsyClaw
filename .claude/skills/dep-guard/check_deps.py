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

Also cross-checks the manifest pin against every CI workflow file that
hardcodes torch's version as a separate string (a dedicated wheel-cache key +
explicit `pip install`/`pip download` pin) instead of reading it from the
manifest -- a dependabot bump only touches the manifest, so those hardcoded
copies silently go stale otherwise (D8; this happened for real on 2026-07-17
and hung the `test`/`verify-install` CI jobs to their 30-minute timeout).
The conda lane's environment.yml duplicates the manifests the same way and
drifts the same way (D9; nltk stayed at 3.9.4 there after the manifests moved
to 3.10.0, so conda CI tested a version the project never ships).

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


# CI workflow files known to hardcode a torch version as a SEPARATE string
# (a dedicated wheel-cache key + an explicit `pip install`/`pip download` pin),
# duplicating the real pin in pyproject.toml/constraints.txt instead of reading
# it. dependabot only touches the manifest files, so a manifest-only pin bump
# silently orphans these -- which is exactly what happened 2026-07-17: #543
# bumped torch to 2.13.0+cpu, these four files kept saying 2.12.1+cpu, and the
# `test`/`verify-install` jobs each paid for a SECOND, uncached ~2GB network
# fetch reconciling the mismatch, long enough to trip the 30-minute job timeout.
_CI_TORCH_FILES = (
    ".github/workflows/ci.yml",
    ".github/workflows/pip-audit.yml",
    ".github/workflows/devskim.yml",
    ".github/workflows/copilot-setup-steps.yml",
)
# Matches both the cache-key form (torch-cpu-2.13.0-...) and the explicit pin
# form (torch==2.13.0+cpu / "torch==2.13.0+cpu") used in the workflow YAMLs.
_TORCH_VERSION_RE = re.compile(r"torch(?:-cpu-|==)(\d+\.\d+\.\d+)")
# .osv-scanner.toml documents the version in prose ("torch 2.13.0+cpu ..."),
# space-separated rather than `==`-joined -- a separate pattern, anchored on
# the trailing "+cpu" to avoid matching unrelated "torch N ..." text.
_TORCH_PROSE_VERSION_RE = re.compile(r"torch\s+(\d+\.\d+\.\d+)\+cpu")

# Executable install scripts that hardcode the pin as `torch==X.Y.Z+cpu`.
# The 2026-07-17 drift didn't stop at workflows: these two scripts kept
# INSTALLING 2.12.1+cpu after the manifest moved to 2.13.0+cpu, reproducing
# the double-fetch failure outside CI -- so they FAIL like the workflows.
_TORCH_SCRIPT_FILES = (
    ".claude/skills/CyClaw-Sandbox/verify.sh",
    ".codex/skills/cyclaw-sandbox-test/scripts/run_sandbox_test.py",
)
# Agent-facing docs/SKILL.md install instructions with the same hardcoded
# pin. Drift here misleads but executes nothing -> WARN, same posture as the
# .osv-scanner.toml comment check. Historical narrative ("moved 2.12.1 ->
# 2.13.0") doesn't match the `torch==`/`torch-cpu-` pattern and is ignored.
_TORCH_DOC_FILES = (
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "docs/SETUP.md",
    ".github/copilot-instructions.md",
    ".claude/rules/PROJECT_RULES.md",
    ".claude/skills/CyClaw-Sandbox/SKILL.md",
    ".claude/skills/python-coding-agent/SKILL.md",
    ".claude/skills/cyclaw-advisor/SKILL.md",
    ".claude/skills/CyClaw-Optimize/SKILL.md",
    ".claude/skills/index-doctor/SKILL.md",
)


def run_ci_pin_checks(root: Path, torch_pin: str | None) -> None:
    """D8: every CI-hardcoded torch version string agrees with the real pin.

    torch_pin is the exact version from constraints.txt/pyproject.toml (no
    +cpu suffix, e.g. "2.13.0"); None if torch isn't pinned there at all.
    """
    print("D8 CI-hardcoded torch pins agree with the manifest pin")
    if torch_pin is None:
        info("D8", "torch not pinned in pyproject/constraints -- skipping CI cross-check")
        return

    mismatches: list[str] = []
    files_checked = 0
    for rel in _CI_TORCH_FILES:
        path = root / rel
        if not path.exists():
            continue
        files_checked += 1
        text = path.read_text(encoding="utf-8")
        found = {m.group(1) for m in _TORCH_VERSION_RE.finditer(text)}
        stale = found - {torch_pin}
        if stale:
            mismatches.append(f"{rel}: hardcodes {sorted(stale)} but the pin is {torch_pin}")
    if files_checked == 0:
        warn("D8", "none of the known CI files were found -- nothing to cross-check")
    elif mismatches:
        fail("D8", f"CI workflow(s) hardcode a stale torch version (pin is {torch_pin}+cpu): "
                   + "; ".join(mismatches))
    else:
        ok("D8", f"all {files_checked} CI file(s) with a hardcoded torch version agree with {torch_pin}+cpu")

    # Install scripts (FAIL) and docs (WARN) that duplicate the pin outside CI.
    for rel in _TORCH_SCRIPT_FILES:
        path = root / rel
        if not path.exists():
            continue
        stale = {m.group(1) for m in _TORCH_VERSION_RE.finditer(path.read_text(encoding="utf-8"))} - {torch_pin}
        if stale:
            fail("D8", f"{rel}: install script hardcodes torch {sorted(stale)} "
                       f"but the pin is {torch_pin}+cpu -- it would install the stale version")
    doc_mismatches: list[str] = []
    for rel in _TORCH_DOC_FILES:
        path = root / rel
        if not path.exists():
            continue
        stale = {m.group(1) for m in _TORCH_VERSION_RE.finditer(path.read_text(encoding="utf-8"))} - {torch_pin}
        if stale:
            doc_mismatches.append(f"{rel}: {sorted(stale)}")
    if doc_mismatches:
        warn("D8", f"doc(s) reference a stale torch version (pin is {torch_pin}+cpu): "
                   + "; ".join(doc_mismatches))

    # .osv-scanner.toml's torch version appears only in comments/reason strings
    # (documentation, not functional) -- drift here doesn't break CI the way
    # the workflow files do, so it's a WARN, not a FAIL. Still worth catching:
    # this exact file drifted stale once already (PR #538 fixed 2.6.0->2.12.1;
    # the very next dependabot bump re-orphaned it to 2.13.0 within hours).
    osv_path = root / ".osv-scanner.toml"
    if osv_path.exists():
        osv_text = osv_path.read_text(encoding="utf-8")
        osv_found = {m.group(1) for m in _TORCH_PROSE_VERSION_RE.finditer(osv_text)}
        osv_stale = osv_found - {torch_pin}
        if osv_stale:
            warn("D8", f".osv-scanner.toml documents torch {sorted(osv_stale)} "
                       f"but the pin is {torch_pin}+cpu (comment-only drift, doesn't break CI, "
                       "but misleads anyone auditing the ignore list)")


# The conda CI lane (python-package-conda.yml) installs from this file, whose
# own header says its pins must stay in sync with pyproject.toml and
# constraints.txt. dependabot only touches the pip manifests, so these
# hand-duplicated conda pins silently go stale otherwise -- which is exactly
# what happened by 2026-07-19: PYSEC-2026-597 moved nltk 3.9.4 -> 3.10.0 in
# all three pip manifests while environment.yml kept the conda lane testing
# 3.9.4, a version the project no longer ships.
_ENV_YML_FILE = "environment.yml"
# Not pip packages -- the manifests have nothing to compare them against.
_ENV_SKIP = {"python", "pip"}
# fastapi diverges on purpose: conda-forge's chromadb=1.5.9 build hard-pins
# fastapi==0.115.9 (a packaging constraint documented in environment.yml
# itself, not a CyClaw choice) -- advisory, never a failure.
_ENV_DOCUMENTED_DIVERGENCE = {"fastapi"}
# Two pin forms in the file: conda deps ("  - name=1.2.3", single '=') and the
# pip: sublist ("      - name==1.2.3"). The conda pattern anchors the version
# on a leading digit so it cannot half-match a pip '==' line.
_ENV_PIP_PIN_RE = re.compile(r"^\s*-\s*([A-Za-z0-9_.-]+)==([^\s#]+)")
_ENV_CONDA_PIN_RE = re.compile(r"^\s*-\s*([A-Za-z0-9_.-]+)=([0-9][^\s#]*)")


def run_environment_pin_check(root: Path, py_reqs: list[Req], con_reqs: list[Req]) -> None:
    """D9: environment.yml (conda CI lane) pins agree with the pip manifests."""
    print("D9 environment.yml pins agree with the pip manifests")
    path = root / _ENV_YML_FILE
    if not path.exists():
        warn("D9", f"{_ENV_YML_FILE} not found -- nothing to cross-check")
        return
    # constraints.txt wins on conflict (same precedence as D8's torch pin);
    # D6 already fails the run when the two manifests disagree.
    manifest_pin: dict[str, str] = {}
    for req in py_reqs + con_reqs:
        version = _pin(req.spec)
        if version is not None:
            manifest_pin[req.name] = version
    mismatches: list[str] = []
    compared = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ENV_PIP_PIN_RE.match(line) or _ENV_CONDA_PIN_RE.match(line)
        if m is None:
            continue
        name, env_version = _normalize(m.group(1)), m.group(2)
        if name in _ENV_SKIP:
            continue
        if name in _ENV_DOCUMENTED_DIVERGENCE:
            info("D9", f"{name}={env_version} diverges on purpose (conda-forge chromadb "
                       f"build pins it; documented in {_ENV_YML_FILE})")
            continue
        manifest_version = manifest_pin.get(name)
        if manifest_version is None:
            continue
        compared += 1
        if env_version != manifest_version:
            mismatches.append(f"{name}: environment.yml={env_version} "
                              f"vs manifests=={manifest_version}")
    if mismatches:
        fail("D9", "the conda CI lane tests version(s) the project never ships: "
                   + "; ".join(mismatches))
    else:
        ok("D9", f"all {compared} cross-pinned package(s) agree with the pip manifests")


# Workflow pytest lanes enumerate their own --cov flags. Granularity differs on
# purpose: ci.yml curates individual modules (excluding optional-backend modules
# like utils/personality_db.py that have their own lanes), while the conda lane
# measures whole packages. What must NOT drift is *presence*: every entry in
# [tool.coverage.run] source has to be measured by every lane at SOME
# granularity. Drift class (D10): the conda lane silently lost --cov=gate_ops
# when the module was added to ci.yml + pyproject (2026-07-19), understating its
# measured total and blinding that lane to a gate_ops coverage regression.
_CI_COV_FILES = (".github/workflows/ci.yml", ".github/workflows/python-package-conda.yml")
_COV_FLAG_RE = re.compile(r"--cov=([A-Za-z0-9_.-]+)")


def run_coverage_source_check(root: Path, pyproject: dict) -> None:
    """D10: every [tool.coverage.run] source entry is --cov'd by every pytest lane."""
    print("D10 workflow --cov flags cover every pyproject coverage source")
    source = (
        pyproject.get("tool", {}).get("coverage", {}).get("run", {}).get("source", [])
    )
    if not source:
        warn("D10", "no [tool.coverage.run] source list in pyproject.toml -- nothing to cross-check")
        return
    for rel in _CI_COV_FILES:
        path = root / rel
        if not path.exists():
            warn("D10", f"{rel} not found -- skipped")
            continue
        flags: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            flags.update(m.group(1) for m in _COV_FLAG_RE.finditer(line))
        if not flags:
            # Not a coverage lane at all -> same absence-is-not-failure policy
            # as D8/D9. Besides real non-coverage workflows this also keeps
            # verify.sh's mutation self-tests green: their synthetic trees ship
            # a minimal ci.yml (a torch pin, an environment.yml stub) with no
            # pytest step, and must not trip D10. The guard's target is a lane
            # that MEASURES coverage but dropped one source's flag -- that case
            # still fails below.
            warn("D10", f"{rel} has no --cov flags -- not a coverage lane, skipped")
            continue
        missing: list[str] = []
        for name in source:
            if (root / f"{name}.py").is_file():
                # Top-level module (gate, gate_ops, graph, ...): a lane either
                # measures it or it does not -- require the exact flag.
                covered = name in flags
            else:
                # Package (llm, utils, ...): whole-package flag, or a curated
                # submodule flag (ci.yml's deliberate module-level selection).
                covered = name in flags or any(f.startswith(f"{name}.") for f in flags)
            if not covered:
                missing.append(name)
        if missing:
            fail("D10", f"{rel} does not measure pyproject coverage source(s): "
                        f"{missing} -- add matching --cov flag(s) or trim the source list")
        else:
            ok("D10", f"{rel} measures every pyproject coverage source ({len(source)} entries)")


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
    py_reqs = _load_pyproject_reqs(pyproject)
    con_reqs = _load_constraints_reqs(constraints_text)
    run_checks(py_reqs, con_reqs)

    # D8 needs the resolved torch pin (constraints.txt wins; D6 above already
    # enforces it agrees with pyproject when both pin it).
    torch_req = next((r for r in con_reqs if r.name == "torch"), None) \
        or next((r for r in py_reqs if r.name == "torch"), None)
    torch_pin = _pin(torch_req.spec) if torch_req is not None else None
    # Strip a "+cpu"-style local version tag -- _CI_TORCH_FILES regex and the
    # manifest pin both need the bare X.Y.Z to compare equal.
    if torch_pin is not None:
        torch_pin = torch_pin.split("+")[0]
    run_ci_pin_checks(root, torch_pin)
    run_environment_pin_check(root, py_reqs, con_reqs)
    run_coverage_source_check(root, pyproject)

    strict_fail = args.strict and _warns
    print(f"\n{len(_fails)} failure(s), {len(_warns)} warning(s)"
          + (" (--strict: warnings count as failures)" if args.strict else ""))
    if args.json:
        print(json.dumps({"fails": _fails, "warns": _warns, "strict": args.strict}, indent=2))
    return 2 if (_fails or strict_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
