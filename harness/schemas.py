"""Pydantic request models for the harness control plane.

Kept in their own module so ``server.py`` stays a lean route table and the
models can be imported by tests without touching the FastAPI app. All models
forbid extra keys, matching the repo's schema contract style.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from harness.agent_policy import BRANCH_NAME_RE, DEFAULT_CHECK_PROFILE
from utils.repo_paths import canonical_repo_relative_path

_MAX_MESSAGE_LEN = 32768
_MAX_TITLE_LEN = 200
_MAX_MODEL_LEN = 200
_MAX_INSTRUCTION_LEN = 8192
_MAX_REASON_LEN = 1000
_MAX_COMMIT_MESSAGE_LEN = 500
_MAX_BRANCH_LEN = 88  # longest allowlisted prefix + '/' + topic (1+79)
_MAX_CHECK_PROFILES = 8
_MAX_READ_FILES = 8
_MAX_READ_FILE_LEN = 1024
# Keep in sync with harness.prompts._MAX_GOAL_CHARS. Session data only.
_MAX_GOAL_LEN = 2000
_MAX_WEB_URL_LEN = 500
_MAX_WEB_QUERY_LEN = 200
# Keep in sync with harness.memory_notes._MAX_NOTE_CHARS.
_MAX_NOTE_LEN = 500
_MAX_NOTE_ID_LEN = 32
# The agentic planner caps its generated body at 6,000 characters, then adds a
# fixed truncation marker. Keep enough room for that legitimate reviewed output
# without turning the browser request into an unbounded prompt transport.
_MAX_PLAN_CHARS = 6_100
_MAX_ITERATIONS_CEILING = 10


_READ_PATH_ERR = (
    "read_files must contain non-empty bounded repo-relative paths "
    "without NUL bytes, traversal, or absolute/drive forms"
)


def _one_safe_read_path(raw: object) -> str:
    # One entry → canonical form, or ValueError (jail-aligned).
    if not isinstance(raw, str) or len(raw) > _MAX_READ_FILE_LEN:
        raise ValueError(_READ_PATH_ERR)
    canonical = canonical_repo_relative_path(raw)
    if canonical is None:
        raise ValueError(_READ_PATH_ERR)
    return canonical


def _canonicalize_read_paths(read_paths: list[str]) -> list[str]:
    # Shared by AgentRunRequest: reject jail-unsafe names, dedupe canonical forms.
    if len(read_paths) > _MAX_READ_FILES:
        raise ValueError(f"read_files allows at most {_MAX_READ_FILES} paths")
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in read_paths:
        canonical = _one_safe_read_path(raw)
        if canonical not in seen:
            seen.add(canonical)
            cleaned.append(canonical)
    return cleaned


class _ForbidModel(BaseModel, extra="forbid"):
    """Shared base: reject unexpected request fields."""


class ChatRequest(_ForbidModel):
    message: str = Field(min_length=1, max_length=_MAX_MESSAGE_LEN)
    session_id: str | None = None
    model: str | None = None
    # True only for console /loop turns. Triggers the tighter loop limiter,
    # the in-flight lock, a shorter clipped history window, a 1024-token
    # output budget, and the "goal required" check. Default False so ordinary
    # chatbot turns are unchanged aside from the shared single-generation lock.
    loop: bool = False


class SessionCreateRequest(_ForbidModel):
    title: str = Field(default="", max_length=_MAX_TITLE_LEN)


class RenameRequest(_ForbidModel):
    title: str = Field(min_length=1, max_length=_MAX_TITLE_LEN)


class SoulToggleRequest(_ForbidModel):
    enabled: bool


class MemoryNoteRequest(_ForbidModel):
    """One operator-authored /memory note. Scanned before persist."""

    text: str = Field(min_length=1, max_length=_MAX_NOTE_LEN)


class MemoryForgetRequest(_ForbidModel):
    id: str = Field(min_length=1, max_length=_MAX_NOTE_ID_LEN)


class WebUrlRequest(_ForbidModel):
    """Add, remove, or fetch one allowlisted URL."""

    url: str = Field(min_length=1, max_length=_MAX_WEB_URL_LEN)


class WebSearchRequest(_ForbidModel):
    query: str = Field(min_length=1, max_length=_MAX_WEB_QUERY_LEN)


class GoalRequest(_ForbidModel):
    """Set or clear the session-scoped operator goal.

    Empty string clears. This is session data injected into the system prompt;
    it is not a write authorization and never reaches the real-repo path.
    """

    goal: str = Field(default="", max_length=_MAX_GOAL_LEN)


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
    # BRANCH_NAME_RE.pattern, NOT a copy of its text. A third literal here
    # would be the one actually enforced while the drift test in
    # tests/test_harness_agent_routes.py compared the other two -- the test
    # would stay green with the enforcement wrong. Declared as a pattern (not
    # a route-body check) so a bad branch is a 422 before any subprocess runs.
    branch: str = Field(min_length=1, max_length=_MAX_BRANCH_LEN, pattern=BRANCH_NAME_RE.pattern)
    commit_message: str = Field(min_length=1, max_length=_MAX_COMMIT_MESSAGE_LEN)
    reason: str = Field(min_length=1, max_length=_MAX_REASON_LEN)
    confirm: bool = False
    checks: list[str] = Field(
        default_factory=lambda: [DEFAULT_CHECK_PROFILE],
        min_length=1,
        max_length=_MAX_CHECK_PROFILES,
    )
    # Browser clients supply plan TEXT selected from their own filesystem, not
    # a server-side path. The shim materializes that text briefly for the CLI's
    # existing --plan-file scanner and deletes it on every exit path.
    plan: str | None = Field(default=None, min_length=1, max_length=_MAX_PLAN_CHARS)
    # This is a declared list, never a browse/read API: the CLI resolves each
    # path only inside the fresh jailed clone before showing it to the coder.
    read_files: list[str] = Field(default_factory=list, max_length=_MAX_READ_FILES)
    # ge=1 rather than gt=0 so 0 is a validation error, not a silently-dropped
    # value: run_agentic_op gates --max-iterations on truthiness, so 0 would
    # fall through to the CLI default of 3 rather than doing what it says.
    max_iterations: int | None = Field(default=None, ge=1, le=_MAX_ITERATIONS_CEILING)
    pr: int | None = Field(default=None, ge=1)
    issue: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _one_target_only(self) -> AgentRunRequest:
        """Reject pr AND issue together rather than silently preferring one.

        ``run_agentic_op`` builds its selector with if/elif, so a body carrying
        both sends only ``--pr`` and the issue is dropped without a word. An
        operator who supplied both meant something; guessing which half to
        honor is the wrong answer either way.
        """
        if self.pr is not None and self.issue is not None:
            raise ValueError("pass at most one of pr / issue")
        return self

    @field_validator("plan")
    @classmethod
    def _plan_is_not_blank(cls, plan_text: str | None) -> str | None:
        if plan_text is not None and not plan_text.strip():
            raise ValueError("plan must not be blank")
        return plan_text

    @field_validator("read_files")
    @classmethod
    def _read_files_are_safe_repo_relative(cls, read_paths: list[str]) -> list[str]:
        # Align with the clone jail so confirm never 422s on staged junk.
        return _canonicalize_read_paths(read_paths)


class AgentDecisionRequest(_ForbidModel):
    """Approve (commit) or reject (discard) one pending run.

    A Literal, not a bare str: the shim re-validates the same two values, but
    a 422 naming both of them beats an exit-2 subprocess round-trip.
    """

    decision: Literal["approve", "reject"]


class AgentPublishRequest(_ForbidModel):
    """Open a draft PR for an already-pushed run.

    ``reason`` and ``confirm`` are this request's own, deliberately NOT
    inherited from the run's earlier approve step: opening a pull request
    under the operator's ``gh`` identity is a materially different act from
    committing inside a local clone, and ``agentic/writer.py``'s
    ``execute_write`` requires a fresh confirmation from its immediate caller
    regardless (an earlier version manufactured one internally, which an
    external review caught as making that gate unconditional).

    ``confirm`` is not defaulted to True and is not silently forwarded when
    absent -- same shape as ``AgentRunRequest.confirm``: omitting it reaches
    the CLI's own refusal path (exit 4) rather than a silent default.
    """

    reason: str = Field(min_length=1, max_length=_MAX_REASON_LEN)
    confirm: bool = False
