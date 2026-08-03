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
from types import MappingProxyType
from typing import NamedTuple

from utils.agent_identity import BRANCH_NAME_RE

# Mirrors agentic/real_repo_run_store.py's RUN_ID_RE. A run_id arrives as an
# HTTP path parameter and is interpolated into a `--run-id=<value>` argv
# element, so it is validated at the boundary rather than only inside the
# child: the anchored 32-char lowercase-hex shape (exactly uuid4().hex) leaves
# no room for a leading dash, a path separator, or a traversal segment.
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# BRANCH_NAME_RE imported from utils.agent_identity (same module agentic uses).
# The backend re-validates itself, but only after a full clone, a model call,
# and a verification run -- up to fifteen minutes before a typo is reported.
# Rejecting at the boundary turns that into an immediate 422. I6 still holds:
# harness imports utils/, never agentic/.

# The one entry every request gets when it names no profile. Named rather than
# inlined so the console's `/agent checks` listing and the request model's
# default cannot drift apart.
DEFAULT_CHECK_PROFILE = "pytest"

# name -> (human description, argv). Mirrors agentic.executor.default_checks()'s
# pytest/ruff pair; invariant-guard and config-guard are added below with a
# REPO-RELATIVE argv, not an absolute one.
#
# An earlier version of this comment excluded invariant-guard on the theory
# that its script "interpolates an absolute path into the worktree being
# verified, which this module has no way to compute without importing
# agentic." That premise was checked against the actual scripts and does not
# hold for either one: both .claude/skills/invariant-guard/check_invariants.py
# and .claude/skills/config-guard/check_config.py self-anchor their own repo
# root via `Path(__file__).resolve().parents[3]` (with an optional
# --repo-root override neither profile below needs). agentic.executor.
# run_verification pins subprocess `cwd` to the worktree being checked, so a
# repo-relative argv like ".claude/skills/invariant-guard/check_invariants.py"
# resolves against THAT worktree's own copy of the script -- which is exactly
# what default_checks() already documents wanting ("it must be the SAME
# worktree run_verification runs against, not this process's own checkout").
# No absolute path, no `agentic` import, needed.
#
# Both are meaningful only when the configured target IS this repository (or
# a fork sharing its layout) -- config.yaml's shipped agentic.repo default is
# "CGFixIT/CyClaw", so that is the common case, not an edge case. Against any
# OTHER target, the script simply is not there and the check fails closed
# (non-zero exit -> a rejected candidate), never silently passing.
#
# A WPS/flake8 profile is deliberately NOT added here: CLAUDE.md's own §4
# documents that the full tree does not pass wemake-python-styleguide clean
# end-to-end today (CI's own lint lane runs it only against a diff of changed
# files, not the whole tree). A profile running it over an entire cloned
# worktree would fail even a candidate that touched nothing WPS-relevant,
# on pre-existing findings the candidate never introduced -- an operator who
# wants it can compute the diff-scoped invocation manually via --checks-file.
#
# sys.executable (not a bare "python") for the same reason every other
# subprocess in this repo uses it: the harness may be running from a venv whose
# interpreter is not the one a bare name would resolve to on PATH.
class CheckProfile(NamedTuple):
    """One selectable verification command, named so a caller never sends argv."""

    description: str
    argv: tuple[str, ...]


# MappingProxyType, not a bare dict: this is the allow-list standing between an
# HTTP body and subprocess.run, so it should not be mutable at runtime by
# anything that imports it. A read-only view makes that structural rather than
# a convention.
_CHECK_PROFILES: MappingProxyType[str, CheckProfile] = MappingProxyType({
    "pytest": CheckProfile("run the test suite", (sys.executable, "-m", "pytest", "-q", "--tb=short")),
    "ruff": CheckProfile(
        "lint with the repo's ruff selection",
        (sys.executable, "-m", "ruff", "check", "--select", "E,F,I,B,C4,UP,S", "."),
    ),
    "invariant-guard": CheckProfile(
        "static-assert the six security invariants (meaningful only when the target is CyClaw itself)",
        (sys.executable, ".claude/skills/invariant-guard/check_invariants.py"),
    ),
    "config-guard": CheckProfile(
        "validate config.yaml's relational/value contract (meaningful only when the target is CyClaw itself)",
        (sys.executable, ".claude/skills/config-guard/check_config.py"),
    ),
})


class CheckProfileError(ValueError):
    """A requested check profile is not on the allow-list."""


def available_profiles() -> list[tuple[str, str]]:
    """``(name, description)`` for every selectable profile, for the console."""
    return [(name, _CHECK_PROFILES[name].description) for name in sorted(_CHECK_PROFILES)]


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
        profile = _CHECK_PROFILES.get(name)
        if profile is None:
            known = ", ".join(sorted(_CHECK_PROFILES))
            raise CheckProfileError(f"unknown check profile {name!r}; available: {known}")
        resolved.append({"name": name, "argv": list(profile.argv)})
    return resolved


__all__ = [
    "BRANCH_NAME_RE",
    "DEFAULT_CHECK_PROFILE",
    "RUN_ID_RE",
    "CheckProfileError",
    "available_profiles",
    "resolve_check_profiles",
]
