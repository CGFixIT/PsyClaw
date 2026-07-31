"""Pydantic request models for the harness control plane.

Kept in their own module so ``server.py`` stays a lean route table and the
models can be imported by tests without touching the FastAPI app. All models
forbid extra keys, matching the repo's schema contract style.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from harness.agent_policy import DEFAULT_CHECK_PROFILE

_MAX_MESSAGE_LEN = 32768
_MAX_TITLE_LEN = 200
_MAX_MODEL_LEN = 200
_MAX_INSTRUCTION_LEN = 8192
_MAX_REASON_LEN = 1000
_MAX_COMMIT_MESSAGE_LEN = 500
_MAX_BRANCH_LEN = 88  # 'claude/' + BRANCH_NAME_RE's 1 + 79 body chars
_MAX_CHECK_PROFILES = 8
_MAX_ITERATIONS_CEILING = 10


class _ForbidModel(BaseModel, extra="forbid"):
    """Shared base: reject unexpected request fields."""


class ChatRequest(_ForbidModel):
    message: str = Field(min_length=1, max_length=_MAX_MESSAGE_LEN)
    session_id: str | None = None
    model: str | None = None


class SessionCreateRequest(_ForbidModel):
    title: str = Field(default="", max_length=_MAX_TITLE_LEN)


class RenameRequest(_ForbidModel):
    title: str = Field(min_length=1, max_length=_MAX_TITLE_LEN)


class SoulToggleRequest(_ForbidModel):
    enabled: bool


class ModelSelectRequest(_ForbidModel):
    model: str = Field(min_length=1, max_length=_MAX_TITLE_LEN)


class AgentRunRequest(_ForbidModel):
    """Start one real-repo coding run.

    ``checks`` carries profile NAMES, never commands -- see
    ``harness/agent_policy.py`` for why a request body that could carry an
    argv would make this route a remote shell. The names are resolved against
    that module's allow-list in the route, not here, so an unknown name
    produces a message that lists the valid ones instead of a regex mismatch.

    ``confirm`` is NOT defaulted to True and is NOT silently forwarded when
    absent: ``run_agentic_op`` only appends ``--confirm`` when the caller set
    it, and omitting it reaches the CLI's own refusal path (exit 4). That
    refusal is the visible half of the same "no anonymous mutations" gate
    ``reason`` is the other half of -- both are surfaced, neither is defaulted.
    """

    instruction: str = Field(min_length=1, max_length=_MAX_INSTRUCTION_LEN)
    # Validated against agent_policy.BRANCH_NAME_RE's exact source. Declared as
    # a pattern here (not a route-body check) so a bad branch is a 422 carrying
    # the offending value, before any subprocess is spawned.
    branch: str = Field(
        min_length=1,
        max_length=_MAX_BRANCH_LEN,
        pattern=r"^claude/[A-Za-z0-9][A-Za-z0-9._/-]{0,79}$",
    )
    commit_message: str = Field(min_length=1, max_length=_MAX_COMMIT_MESSAGE_LEN)
    reason: str = Field(min_length=1, max_length=_MAX_REASON_LEN)
    confirm: bool = False
    checks: list[str] = Field(
        default_factory=lambda: [DEFAULT_CHECK_PROFILE],
        min_length=1,
        max_length=_MAX_CHECK_PROFILES,
    )
    # ge=1 rather than gt=0 so 0 is a validation error, not a silently-dropped
    # value: run_agentic_op gates --max-iterations on truthiness, so 0 would
    # fall through to the CLI default of 3 rather than doing what it says.
    max_iterations: int | None = Field(default=None, ge=1, le=_MAX_ITERATIONS_CEILING)
    pr: int | None = Field(default=None, ge=1)
    issue: int | None = Field(default=None, ge=1)


class AgentDecisionRequest(_ForbidModel):
    """Approve (commit) or reject (discard) one pending run.

    A Literal, not a bare str: the shim re-validates the same two values, but
    a 422 naming both of them beats an exit-2 subprocess round-trip.
    """

    decision: Literal["approve", "reject"]
