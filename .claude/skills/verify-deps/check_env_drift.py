#!/usr/bin/env python3
# Environment-dependency drift checks that live OUTSIDE the four pin manifests.
#
# dep-guard answers "do the manifests agree with each other." extract_pins.py
# adds "does requirements.txt agree with constraints.txt." Both stop at the
# manifest boundary. This adds the surfaces where an environment dependency is
# real, load-bearing, and recorded NOWHERE a manifest checker looks:
#
#   E1  a tool version pinned inline in a workflow file (flake8, WPS, pip,
#       actionlint, zizmor) -- CI-gating versions with no manifest record
#   E2  the Python version, declared independently in four places
#   E3  a third-party module imported directly by source but declared in no
#       manifest -- the class dep-guard structurally cannot see, because it
#       reads manifests and never reads imports
#   E4  the install-surface SCOPE contract (which surface may carry extras)
#
# Pure stdlib, no network, no install required -- same constraints dep-guard
# and extract_pins.py hold, so this runs in a fresh clone before pip does.

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# --repo-root retargets every check at another tree, which is what makes the
# mutation self-tests in verify.sh possible without touching the real repo.
if "--repo-root" in sys.argv:
    REPO = Path(sys.argv[sys.argv.index("--repo-root") + 1]).resolve()
else:
    REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"

failures: list[str] = []
warnings: list[str] = []


def fail(code: str, msg: str) -> None:
    failures.append(f"[{code}] {msg}")
    print(f"  FAIL  [{code}] {msg}")


def warn(code: str, msg: str) -> None:
    warnings.append(f"[{code}] {msg}")
    print(f"  warn  [{code}] {msg}")


def ok(code: str, msg: str) -> None:
    print(f"  ok    [{code}] {msg}")


def info(code: str, msg: str) -> None:
    print(f"  info  [{code}] {msg}")


# --- E1: tool versions pinned inline in workflows -----------------------------
# These gate CI but appear in no manifest, so nothing cross-checks them. The
# risk is not a wrong version -- it is the SAME tool pinned at two different
# versions in two jobs, which silently makes one lane's result unreproducible
# against the other's. flake8/WPS are the sharpest case: they gate the lint
# lane, and a version skew there changes which findings a PR must waive.
_WORKFLOW_TOOLS = ("flake8", "wemake-python-styleguide", "actionlint-py", "zizmor", "pip", "ruff", "mypy")
_PIN_RE = re.compile(rf"\b({'|'.join(map(re.escape, _WORKFLOW_TOOLS))})==([0-9][0-9a-zA-Z.\-]*)")


def check_workflow_tool_pins() -> None:
    print("E1 workflow-pinned tool versions are internally consistent")
    if not WORKFLOWS.is_dir():
        warn("E1", f"no workflow directory at {WORKFLOWS}")
        return
    seen: dict[str, dict[str, list[str]]] = {}
    for wf in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        for lineno, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            for tool, version in _PIN_RE.findall(line):
                seen.setdefault(tool, {}).setdefault(version, []).append(f"{wf.name}:{lineno}")
    if not seen:
        info("E1", "no inline tool pins found in workflows")
        return
    for tool, versions in sorted(seen.items()):
        sites = sum(len(v) for v in versions.values())
        if len(versions) > 1:
            detail = "; ".join(f"{v} at {', '.join(s)}" for v, s in sorted(versions.items()))
            fail("E1", f"{tool} pinned at {len(versions)} different versions across workflows: {detail}")
        elif sites > 1:
            version = next(iter(versions))
            ok("E1", f"{tool}=={version} consistent across {sites} sites ({', '.join(versions[version][:3])}...)")
        else:
            version = next(iter(versions))
            ok("E1", f"{tool}=={version} ({versions[version][0]})")


# --- E2: the Python version, declared in four independent places --------------
def check_python_version() -> None:
    print("E2 Python version agrees across every surface that declares one")
    found: dict[str, str] = {}

    pyproject = REPO / "pyproject.toml"
    if pyproject.is_file():
        m = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', pyproject.read_text(encoding="utf-8"))
        if m:
            found["pyproject requires-python"] = m.group(1)

    dockerfile = REPO / "Dockerfile"
    if dockerfile.is_file():
        m = re.search(r"(?m)^FROM\s+python:(\d+\.\d+)", dockerfile.read_text(encoding="utf-8"))
        if m:
            found["Dockerfile FROM"] = m.group(1)

    env = REPO / "environment.yml"
    if env.is_file():
        m = re.search(r"(?m)^\s*-\s*python\s*=\s*(\d+\.\d+)", env.read_text(encoding="utf-8"))
        if m:
            found["environment.yml"] = m.group(1)

    wf_versions: set[str] = set()
    if WORKFLOWS.is_dir():
        for wf in sorted(WORKFLOWS.glob("*.y*ml")):
            for m in re.finditer(r"""python-version:\s*["']?(\d+\.\d+)""", wf.read_text(encoding="utf-8")):
                wf_versions.add(m.group(1))

    # Compare the concrete minor versions. pyproject's is a RANGE (">=3.12"),
    # so it is checked for compatibility rather than string equality -- a
    # ">=3.12" that every other surface satisfies is correct, not drift.
    concrete = {k: v for k, v in found.items() if k != "pyproject requires-python"} | {
        f"workflow python-version[{v}]": v for v in sorted(wf_versions)
    }
    distinct = set(concrete.values())
    if len(distinct) > 1:
        fail("E2", f"Python version disagrees across surfaces: {concrete}")
    elif distinct:
        pinned = next(iter(distinct))
        spec = found.get("pyproject requires-python", "")
        ok("E2", f"every surface targets Python {pinned} (pyproject: {spec or 'unspecified'})")
        floor = re.search(r"(\d+)\.(\d+)", spec)
        if floor and tuple(map(int, pinned.split("."))) < (int(floor.group(1)), int(floor.group(2))):
            fail("E2", f"surfaces target {pinned}, below pyproject's floor {spec}")
    else:
        warn("E2", "no concrete Python version found on any surface")


# --- E3: a direct import declared in no manifest -------------------------------
# The gap dep-guard cannot see by construction: it reads manifests, never
# imports. Found in practice (2026-08-02 audit) -- huggingface_hub and
# starlette are imported directly by source and declared nowhere, surviving
# only as hard transitives of sentence-transformers and fastapi. That works
# until the parent drops them.
#
# Intentionally-undeclared modules go here WITH the reason, so the allowlist
# stays an argued exception list rather than a silencer.
_IMPORT_ALLOWLIST = {
    # Lazy-imported operator-supplied driver: agentic/sqlconnect/client.py
    # imports it inside a function and raises a friendly "pyodbc is not
    # installed (pip install pyodbc)" if absent, so a disabled connector needs
    # nothing installed. Declaring it would install an MSSQL driver on every
    # box for a connector that ships disabled.
    "pyodbc",
}
_FIRST_PARTY = {
    "utils", "retrieval", "llm", "schemas", "sync", "agentic", "guardrails", "harness",
    "gate", "gate_ops", "graph", "mcp_hybrid_server", "metrics", "tests", "conftest",
}
# import name -> PyPI distribution name, for the cases where they differ by more
# than punctuation. The underscore/hyphen cases (rank_bm25, langchain_xai,
# huggingface_hub, ...) are NOT listed here on purpose -- PEP 503 treats "_" and
# "-" as the same character, so those are handled by normalization below rather
# than by a hand-maintained table that would silently rot.
_DIST_ALIAS = {"yaml": "pyyaml", "dateutil": "python-dateutil", "dotenv": "python-dotenv"}
# Only first-party runtime source is in scope. Skipping by name alone is
# fragile -- the venv in this tree is ".venv312", not ".venv" -- so the rule is
# structural: any hidden directory, any build output, and any directory that
# IS a virtualenv (identified by its own pyvenv.cfg, whatever it is named).
_SKIP_DIRS = ("tests/", "docs/", "build/", "dist/", "site-packages/")


def _skipped(rel: str) -> bool:
    parts = rel.split("/")[:-1]
    if any(p.startswith(".") for p in parts):
        return True
    if "site-packages" in parts or "node_modules" in parts:
        return True
    return any(rel.startswith(d) for d in _SKIP_DIRS)


def check_undeclared_imports() -> None:
    print("E3 every third-party module imported by source is declared somewhere")
    manifests = {
        name: (REPO / name).read_text(encoding="utf-8").lower()
        for name in ("requirements.txt", "constraints.txt", "pyproject.toml", "environment.yml")
        if (REPO / name).is_file()
    }
    if not manifests:
        warn("E3", "no manifests found to check against")
        return

    stdlib = set(sys.stdlib_module_names)
    venv_roots = {p.parent for p in REPO.rglob("pyvenv.cfg")}
    imported: dict[str, list[str]] = {}
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if _skipped(rel) or any(v in path.parents for v in venv_roots):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.setdefault(a.name.split(".")[0], []).append(rel)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.setdefault(node.module.split(".")[0], []).append(rel)

    undeclared: list[tuple[str, str, str]] = []
    for mod in sorted(imported):
        if mod in stdlib or mod in _FIRST_PARTY or mod in _IMPORT_ALLOWLIST:
            continue
        dist = _DIST_ALIAS.get(mod, mod)
        # PEP 503: "_" and "-" are equivalent in a distribution name, so accept
        # either spelling rather than guessing which one a manifest used.
        stem = re.escape(dist).replace("_", "[-_]").replace(r"\-", "[-_]")
        pattern = re.compile(rf"(?mi)^\s*[-\"']?{stem}\b")
        if not any(pattern.search(text) for text in manifests.values()):
            undeclared.append((mod, dist, imported[mod][0]))

    if undeclared:
        for mod, dist, where in undeclared:
            warn("E3", f"'{mod}' (dist: {dist}) imported by {where} but declared in no manifest "
                       f"-- relies on being a transitive of something else")
    else:
        ok("E3", f"all {len(imported)} imported modules resolve to stdlib, first-party, a manifest, or the allowlist")


# --- E4: the install-surface scope contract ------------------------------------
# The distinction a reviewer most often gets wrong, and the reason a correct
# tree can look like drift: constraints.txt pins packages that NO install
# surface installs by default. It is a version ceiling, not an install list.
_EXTRA_ONLY_MARKERS = ("deepagents", "nemoguardrails", "psycopg", "pgvector",
                       "langchain-openai", "langchain-anthropic", "langchain-xai")


def check_install_surface_scope() -> None:
    print("E4 install-surface scope contract (which surface may carry extras)")
    req = REPO / "requirements.txt"
    if not req.is_file():
        warn("E4", "requirements.txt not found")
        return
    text = req.read_text(encoding="utf-8")
    # Only real requirement lines -- a package named in a comment (the file
    # documents the postgres extras in prose) is not an install.
    lines = [ln.split("#")[0].strip().lower() for ln in text.splitlines()]
    leaked = [m for m in _EXTRA_ONLY_MARKERS
              if any(re.match(rf"^{re.escape(m)}\b", ln) for ln in lines if ln)]
    if leaked:
        fail("E4", f"requirements.txt installs extras-only package(s) {leaked} -- it is the BASE "
                   f"surface; extras belong to pyproject.toml, pinned in constraints.txt")
    else:
        ok("E4", "requirements.txt carries base runtime + test tools only (no extras) -- as designed")
    info("E4", "constraints.txt pins extras/transitives NO surface installs by default; that is a "
               "version ceiling, not drift")


def main() -> int:
    print("== verify-deps: environment-dependency drift (outside the pin manifests) ==")
    check_workflow_tool_pins()
    check_python_version()
    check_undeclared_imports()
    check_install_surface_scope()
    print()
    print(f"{len(failures)} failure(s), {len(warnings)} warning(s)")
    if "--strict" in sys.argv and warnings and not failures:
        print("(--strict: warnings count as failures)")
        return 2
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
