"""Advisory inventory of direct model-generation call sites.

Phase 1 of issue #1134: fail-open / advisory. Phase 3 will fail the build on
new callers outside registered adapters. This scanner never imports
nemoguardrails and never runs on the /query path.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# Narrow: generic ``.generate()`` is LocalLLMClient / graph helpers, not NeMo.
# Phase 1 inventories LLMRails.generate_async and vendor chat constructors.
_GENERATE_ATTRS = frozenset({"generate_async"})
_GENERATE_NAMES = frozenset({"ChatOpenAI", "ChatAnthropic", "ChatXAI"})

# Paths relative to repo root that are allowed to mention generation APIs
# (adapters, tests, docs-adjacent examples). Advisory: extras are reported,
# not rejected, until Phase 3.
_ALLOW_PREFIXES = (
    "tests/",
    "guardrails/",
    "llm/",
    "harness/",
    "agentic/",
    "docs/",
)


class CallSite(NamedTuple):
    path: str
    line: int
    name: str


def _is_allowed(rel: str) -> bool:
    posix = rel.replace("\\", "/")
    return any(posix.startswith(p) for p in _ALLOW_PREFIXES)


def scan_tree(root: Path | None = None) -> list[CallSite]:
    """Return generation-like call sites under ``root`` (repo root default)."""
    base = root or REPO_ROOT
    found: list[CallSite] = []
    for py in base.rglob("*.py"):
        if any(part in {".git", "__pycache__", ".venv", "node_modules"} for part in py.parts):
            continue
        rel = str(py.relative_to(base)).replace("\\", "/")
        try:
            source = py.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ""
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _GENERATE_ATTRS:
                name = func.attr
            elif isinstance(func, ast.Name) and func.id in _GENERATE_NAMES:
                name = func.id
            if not name:
                continue
            found.append(CallSite(path=rel, line=node.lineno, name=name))
    return found


def extra_call_sites(root: Path | None = None) -> list[CallSite]:
    """Call sites outside the advisory allowlist (empty today is OK)."""
    return [site for site in scan_tree(root) if not _is_allowed(site.path)]


def main() -> int:
    extras = extra_call_sites()
    if not extras:
        print("call_inventory: no extra generation call sites")
        return 0
    print("call_inventory: advisory extras (not a merge blocker in Phase 1):")
    for site in extras:
        print(f"  {site.path}:{site.line} {site.name}")
    return 0  # advisory


if __name__ == "__main__":
    raise SystemExit(main())
