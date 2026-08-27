"""Provider-neutral tool name-gate (issue #1134 Phase 5).

NeMo must never grant a tool. This module does not import ``nemoguardrails``,
does not read ``GuardrailBroker``, and has no ``rails=`` parameter.
Callers pass an explicit allowlist. Empty allowlist is deny.

Not ``guardrails.broker`` — that module wraps NVIDIA ``check()`` around
generation. This one is the tool name-gate.

Audit logs the tool name and argv digest only — never raw argv or URLs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

from utils.errors import AgenticError
from utils.logger import audit_log


class ToolDenied(AgenticError):
    """Unknown or empty tool name. Fail closed."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message, code="TOOL_DENIED", details=details)


@dataclass(frozen=True)
class ToolVerdict:
    allowed: bool
    reason: str
    tool: str
    argv_digest: str


def argv_digest(argv: Sequence[str]) -> str:
    payload = json.dumps(list(argv), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def decide(name: str, argv: Sequence[str], *, allowlist: frozenset[str]) -> ToolVerdict:
    """Allow only if ``name`` is in the caller-supplied allowlist."""
    digest = argv_digest(argv)
    tool = (name or "").strip()
    if not tool:
        return ToolVerdict(False, "empty tool name", tool, digest)
    if tool not in allowlist:
        return ToolVerdict(False, "unknown tool", tool, digest)
    return ToolVerdict(True, "allowlisted", tool, digest)


def assert_allowed(
    name: str,
    argv: Sequence[str],
    *,
    allowlist: frozenset[str],
    config_path: str = "config.yaml",
    cfg: dict | None = None,
) -> ToolVerdict:
    """``decide`` then audit; raise ``ToolDenied`` on deny."""
    verdict = decide(name, argv, allowlist=allowlist)
    audit_log(
        {
            "event": "tool_broker_decision",
            "tool": verdict.tool,
            "argv_digest": verdict.argv_digest,
            "allowed": verdict.allowed,
            "reason": verdict.reason,
        },
        config_path=config_path,
        cfg=cfg,
    )
    if not verdict.allowed:
        raise ToolDenied(
            f"tool {verdict.tool!r} denied ({verdict.reason})",
            details={"tool": verdict.tool, "argv_digest": verdict.argv_digest},
        )
    return verdict
