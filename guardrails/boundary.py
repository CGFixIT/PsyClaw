"""Provider-independent boundary types for the out-of-band guardrails layer.

Phase 1 of issue #1134: typed decisions and provenance only. No brokers,
no NeMo wiring, no request-path imports. Decisions carry hashes and
reason codes -- never raw prompts, context, responses, or tool payloads.

This module is NEVER imported by ``gate.py``, ``graph.py``, or
``mcp_hybrid_server.py`` (I6).

STATUS (verified 2026-09-04): the consumer never arrived. Phases 2a, 3, 4
and 5 of #1134 all shipped and none of them import these types -- Phase 5's
name gate went out as ``utils/tool_broker.py``, and ``guardrails/profiles.py``
mirrors the ``GuardrailStage`` values by hand rather than importing them.
Outside its own tests this module has no caller. It is kept deliberately
(owner decision) as the typed vocabulary a future broker would adopt, not
because anything reads it today.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from guardrails.errors import GuardrailsConfigError

# Keys that must never land on a GuardrailDecision (raw content / secrets).
# Hash-named fields (*_hash, query_hash) are allowed by design.
_FORBIDDEN_DECISION_KEYS: frozenset[str] = frozenset(
    {
        "prompt",
        "response",
        "query",
        "tool_arguments",
        "tool_result",
        "api_key",
        "authorization",
        "password",
        "token",
    }
)


class TrustLevel(StrEnum):
    """Trust derived from resolved endpoint / provenance class, not a label."""

    UNTRUSTED = "untrusted"
    LOCAL = "local"
    ONLINE = "online"
    OPERATOR = "operator"
    SYSTEM = "system"


class GuardrailStage(StrEnum):
    """Where in the generation / tool / artifact path a rail fires."""

    INPUT = "input"
    RETRIEVAL = "retrieval"
    EGRESS = "egress"
    OUTPUT = "output"
    REASONING = "reasoning"
    TOOL_INTENT = "tool_intent"
    TOOL_RESULT = "tool_result"
    ARTIFACT = "artifact"
    EXTERNAL_WRITE = "external_write"


class GuardrailVerdict(StrEnum):
    """Outcome a rail may emit. Rails may only shrink authority, never grant it."""

    ALLOW = "allow"
    BLOCK = "block"
    REDACT = "redact"
    QUARANTINE = "quarantine"
    REQUIRE_APPROVAL = "require_approval"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Provenance label for one retrieved or external datum.

    ``content_hash`` is a digest only -- never raw bytes or chunk text.
    """

    source_id: str
    trust: TrustLevel
    content_hash: str
    mime_type: str | None = None
    size: int | None = None
    retrieved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class GuardrailDecision:
    """Typed rail outcome with audit metadata and no sensitive payloads.

    Prefer :func:`guardrail_decision` for construction -- it rejects
    forbidden raw-content / secret kwargs before the dataclass is built.
    """

    stage: GuardrailStage
    verdict: GuardrailVerdict
    reason_codes: tuple[str, ...]
    rail_ids: tuple[str, ...]
    policy_hash: str
    config_hash: str
    model: str
    provider: str
    model_digest: str
    provenance_ids: tuple[str, ...]
    latency_ms: float
    degraded: bool
    content_hash: str = ""
    argument_hash: str = ""


def guardrail_decision(**kwargs: Any) -> GuardrailDecision:
    """Build a :class:`GuardrailDecision`, rejecting forbidden sensitive keys.

    Raises :class:`GuardrailsConfigError` if any kwarg name is a forbidden
    raw-content or secret field (``prompt``, ``response``, ``query``,
    ``tool_arguments``, ``tool_result``, ``api_key``, ``authorization``,
    ``password``, ``token``). Hash fields (``*_hash``, ``query_hash``) are
    permitted because they are digests, not raw values.
    """
    for key in kwargs:
        if key in _FORBIDDEN_DECISION_KEYS:
            raise GuardrailsConfigError(
                f"GuardrailDecision rejects sensitive field {key!r}",
                details={"forbidden_key": key},
            )
    try:
        return GuardrailDecision(**kwargs)
    except TypeError as exc:
        raise TypeError(str(exc)) from exc


# --- Phase 3+ stubs (empty; not wired yet) ---------------------------------


@dataclass(frozen=True, slots=True)
class SafetyEnvelope:
    """Egress/consent facts. Hashes and hosts only — never raw query/corpus/soul."""

    destination_host: str = ""
    trust: TrustLevel = TrustLevel.UNTRUSTED
    confirm_digest: str = ""
    send_local_context: bool = False


@dataclass(frozen=True, slots=True)
class ToolIntent:
    """ponytail: stub -- normalized tool call intent not wired until Phase 3+."""


@dataclass(frozen=True, slots=True)
class ToolObservation:
    """ponytail: stub -- post-tool observation not wired until Phase 3+."""


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    """ponytail: stub -- immutable acceptance manifest not wired until Phase 3+."""
