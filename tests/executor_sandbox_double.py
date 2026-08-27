"""Test-only ArgvListSandbox injector.

Production ``production_sandbox()`` is fail-closed off Windows. CI tests that
need to exercise argv/cwd/timeout/numbat/env plumbing inject this double via
monkeypatch -- never an env flag. ``tests/test_agentic_hard_sandbox.py`` must
NOT use this helper.
"""

from __future__ import annotations

from agentic.executor.hard_sandbox import ArgvListSandbox

PRODUCTION_SANDBOX_TARGET = "agentic.executor.runner.production_sandbox"


def inject_argv_list_sandbox(monkeypatch) -> None:
    """Replace production_sandbox with the ArgvListSandbox class (zero-arg factory)."""
    monkeypatch.setattr(PRODUCTION_SANDBOX_TARGET, ArgvListSandbox)
