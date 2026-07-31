"""Policy the harness applies before handing an agentic coding run to the shim.

Everything here answers one question: what is the operator's console allowed to
ask ``agentic.cli`` to do? It is deliberately a separate module from
``server.py`` (which stays a route table) and from ``schemas.py`` (which holds
request shapes), because these are security decisions with their own tests.

Three constants below are DUPLICATES of values that live in ``agentic/``. That
is intentional and load-bearing: invariant I6 forbids ``harness/`` from
importing ``agentic`` at all, so the values are re-declared here and
``tests/test_harness_agent_routes.py`` asserts they still match the originals.
The test may import ``agentic``; this module may not. That asymmetry buys drift
detection without the runtime coupling -- the same trade
``tests/test_metrics_injection_findings.py::test_code_constants_match_the_producer``
already makes for ``metrics.py``'s copies of ``agentic/context.py``'s codes.

The check profiles are the security core of this module. ``agentic.executor``
runs each check as ``subprocess.run(list(check.argv), cwd=<worktree>,
env=_scrubbed_env())`` and the scrubbed env keeps the parent ``PATH``, so an
``argv`` reaching that call is arbitrary local command execution. Nothing
between the HTTP body and that call inspects ``argv[0]``:
``utils.ops_runner.run_agentic_op`` only rejects an EMPTY checks list, and
``agentic.cli._load_checks_file`` validates the manifest's SHAPE (name is a
string, argv is a non-empty list of strings) rather than its content. So the
console never sends an argv -- it sends a profile NAME, and this module maps
that name to a fixed command. A request body that could carry an argv would
turn an authenticated console route into a remote shell, whatever its schema
said the field's type was.
"""

from __future__ import annotations

import re
import sys

# Mirrors agentic/real_repo_run_store.py's RUN_ID_RE. A run_id arrives as an
# HTTP path parameter and is interpolated into a `--run-id=<value>` argv
# element, so it is validated at the boundary rather than only inside the
# child: the anchored 32-char lowercase-hex shape (exactly uuid4().hex) leaves
# no room for a leading dash, a path separator, or a traversal segment.
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Mirrors agentic/deepagent_github/repo_workspace.py's BRANCH_NAME_RE. The
# backend re-validates this itself, but only after a full clone, a model call,
# and a verification run -- up to fifteen minutes spent before a typo'd branch
# name is reported. Rejecting it at the boundary turns that into an immediate
# 422.
BRANCH_NAME_RE = re.compile(r"^claude/[A-Za-z0-9][A-Za-z0-9._/-]{0,79}$")

# The one entry every request gets when it names no profile. Named rather than
# inlined so the console's `/agent checks` listing and the request model's
# default cannot drift apart.
DEFAULT_CHECK_PROFILE = "pytest"

# name -> (human description, argv). Mirrors agentic.executor.default_checks()'s
# three commands, minus its invariant-guard entry: that one interpolates an
# absolute path into the worktree being verified, which this module has no way
# to compute without importing agentic. An operator who wants it can still run
# it through the CLI directly.
#
# sys.executable (not a bare "python") for the same reason every other
# subprocess in this repo uses it: the harness may be running from a venv whose
# interpreter is not the one a bare name would resolve to on PATH.
_CHECK_PROFILES: dict[str, tuple[str, tuple[str, ...]]] = {
    "pytest": ("run the test suite", (sys.executable, "-m", "pytest", "-q", "--tb=short")),
    "ruff": (
        "lint with the repo's ruff selection",
        (sys.executable, "-m", "ruff", "check", "--select", "E,F,I,B,C4,UP,S", "."),
    ),
}


class CheckProfileError(ValueError):
    """A requested check profile is not on the allow-list."""


def available_profiles() -> list[tuple[str, str]]:
    """``(name, description)`` for every selectable profile, for the console."""
    return [(name, description) for name, (description, _argv) in sorted(_CHECK_PROFILES.items())]


def resolve_check_profiles(names: list[str]) -> list[dict[str, object]]:
    """Map profile names to the check manifest ``run_agentic_op`` expects.

    Returns the ``[{"name": str, "argv": [str, ...]}, ...]`` shape
    ``agentic.cli._load_checks_file`` parses. An unknown name raises rather
    than being skipped: silently dropping one of two requested profiles would
    let a run report success having verified less than the operator asked for,
    and an empty result would hit ``run_agentic_op``'s own "non-empty checks
    list" refusal with a message that names no cause.
    """
    if not names:
        raise CheckProfileError("at least one check profile is required")
    resolved: list[dict[str, object]] = []
    for name in names:
        entry = _CHECK_PROFILES.get(name)
        if entry is None:
            raise CheckProfileError(
                f"unknown check profile {name!r}; available: {', '.join(sorted(_CHECK_PROFILES))}"
            )
        _description, argv = entry
        resolved.append({"name": name, "argv": list(argv)})
    return resolved


__all__ = [
    "BRANCH_NAME_RE",
    "DEFAULT_CHECK_PROFILE",
    "RUN_ID_RE",
    "CheckProfileError",
    "available_profiles",
    "resolve_check_profiles",
]
