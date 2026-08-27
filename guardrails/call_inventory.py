"""Fail-closed inventory of direct model-generation call sites.

Issue #1134 Phase 5 slice: a new ``ChatXAI`` / ``ChatAnthropic`` /
``ChatOpenAI`` constructor or ``generate_async`` call outside the registered
adapter files fails the test suite and this module's CLI (exit 1).

Never imports ``nemoguardrails``. Never runs on the ``/query`` path.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# Narrow: generic ``.generate()`` is LocalLLMClient / graph helpers, not NeMo.
_GENERATE_ATTRS = frozenset({"generate_async"})
_GENERATE_NAMES = frozenset({"ChatOpenAI", "ChatAnthropic", "ChatXAI"})

# Registered adapters only. Package prefixes (agentic/, harness/, llm/) were
# how Phase 1 stayed advisory-empty. A new caller in those trees must be
# added here explicitly or the build fails.
_ALLOW_FILES = frozenset(
    {
        "agentic/deepagent_github/model_adapter.py",
        "guardrails/integration.py",
    }
)
_ALLOW_PREFIXES = ("tests/",)


class CallSite(NamedTuple):
    path: str
    line: int
    name: str


def _is_allowed(rel: str) -> bool:
    posix = rel.replace("\\", "/")
    if posix in _ALLOW_FILES:
        return True
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
    """Call sites outside the registered-adapter allowlist."""
    return [site for site in scan_tree(root) if not _is_allowed(site.path)]


def main() -> int:
    extras = extra_call_sites()
    if not extras:
        print("call_inventory: no extra generation call sites")
        return 0
    print("call_inventory: unregistered generation call sites:")
    for site in extras:
        print(f"  {site.path}:{site.line} {site.name}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
