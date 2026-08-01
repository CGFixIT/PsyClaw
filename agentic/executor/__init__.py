"""Sandboxed verification executor for the agentic coding harness.

This is the production-grade gate the audit named as the biggest engineering
gap: something that can actually run ``pytest``/``ruff``/the invariant guard
over a working tree, rather than the ``in``-substring scoring
``harness_optimizer``'s fixture runner uses today.

Read ``docs/THREAT_MODEL.md`` sections 4-6 before wiring this into anything
live -- this package is the one that requires the amendment recorded there:
adding code execution to a layer previously documented as "deliberately
non-executing" is a real, named change to the threat model, not a detail.

Never imported by ``gate.py``/``graph.py``/``mcp_hybrid_server.py`` (I6).
Nothing in this repository invokes this package automatically as of this
change -- see ``runner.py``'s module docstring for exactly what is, and is
not, wired.
"""

from __future__ import annotations

from agentic.executor.runner import (
    Check,
    CheckResult,
    VerificationReport,
    default_checks,
    run_verification,
)

__all__ = [
    "Check",
    "CheckResult",
    "VerificationReport",
    "default_checks",
    "run_verification",
]
