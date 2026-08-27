"""Re-export of ``utils.tool_broker`` (issue #1134 Phase 5).

Canonical implementation lives in ``utils/`` so ``harness/`` can call the
name-gate without importing this package (I6). This module stays as a
stable ``guardrails.tool_broker`` import path for guardrails-side tests.
"""

from __future__ import annotations

from utils.tool_broker import ToolDenied, ToolVerdict, argv_digest, assert_allowed, decide

__all__ = [
    "ToolDenied",
    "ToolVerdict",
    "argv_digest",
    "assert_allowed",
    "decide",
]
