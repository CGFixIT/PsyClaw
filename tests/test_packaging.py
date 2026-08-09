"""Static regression guard for pyproject.toml's wheel/sdist build targets.

Verified empirically (2026-08-09): hatchling's `[tool.hatch.build.targets.wheel]
packages` only pulls in directory packages. Top-level single-file modules added
to a plain `include` list were silently dropped from the built wheel -- only
`force-include` (an explicit source->destination mapping) actually worked. A
real `pip install cyclaw` from a wheel (e.g. the one python-publish.yml builds
on every GitHub Release) raised `ModuleNotFoundError` for gate/gate_ops/
gate_auth/gate_memory/graph/mcp_hybrid_server/metrics -- every [project.scripts]
entry point except the ones rooted at an already-packaged directory. `pip
install -e .` (the documented dev path) was unaffected: its .pth-based editable
mechanism points at the repo root directly and never consults this config.

This test is deliberately static (tomllib only, no real hatchling build) so it
runs in the same no-deps-installed environment as the rest of the unit suite.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Every top-level single-file module that ships in this repo and is either a
# [project.scripts] entry-point target or a direct import of one (gate_memory
# via gate.py). Directory packages (utils, retrieval, ...) are covered by the
# `packages` list itself and are not repeated here.
_TOP_LEVEL_MODULES = {
    "gate.py",
    "gate_ops.py",
    "gate_auth.py",
    "gate_memory.py",
    "graph.py",
    "mcp_hybrid_server.py",
    "metrics.py",
}


def _load_pyproject() -> dict:
    with open(_REPO_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


def test_wheel_force_includes_every_top_level_module() -> None:
    cfg = _load_pyproject()
    wheel_cfg = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]
    force_include = wheel_cfg.get("force-include", {})
    missing = _TOP_LEVEL_MODULES - set(force_include)
    assert not missing, (
        f"pyproject.toml's [tool.hatch.build.targets.wheel.force-include] is missing "
        f"{sorted(missing)} -- a real (non-editable) `pip install` of the built wheel "
        f"would raise ModuleNotFoundError for these modules. `packages` alone does not "
        f"pull in top-level single-file modules; see this file's module docstring."
    )


def test_wheel_packages_includes_memory() -> None:
    cfg = _load_pyproject()
    wheel_cfg = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert "memory" in wheel_cfg.get("packages", []), (
        "gate.py imports gate_memory.register_memory_routes unconditionally, and "
        "gate_memory lazy-imports the `memory` package inside its handlers -- both "
        "must ship in the wheel for the memory admin surface to be reachable."
    )


def test_sdist_includes_every_top_level_module_and_memory() -> None:
    cfg = _load_pyproject()
    sdist_include = set(cfg["tool"]["hatch"]["build"]["targets"]["sdist"]["include"])
    missing = _TOP_LEVEL_MODULES - sdist_include
    assert not missing, f"pyproject.toml's sdist include list is missing {sorted(missing)}"
    assert "memory" in sdist_include, "pyproject.toml's sdist include list is missing 'memory'"


def test_coverage_source_includes_memory_subsystem() -> None:
    cfg = _load_pyproject()
    source = set(cfg["tool"]["coverage"]["run"]["source"])
    missing = {"gate_memory", "memory"} - source
    assert not missing, (
        f"[tool.coverage.run] source is missing {sorted(missing)} -- the memory "
        f"subsystem's coverage would never be measured or gated."
    )
