"""Advanced rail logic tailored to CyClaw soul / personality topics.

The Colang flows in ``config/rails.co`` express *when* a rail fires; the actual
*checks* they call live here as plain, fully-typed, offline-testable Python.
Keeping the logic in Python (rather than only in Colang) means:

  * every check is unit-testable WITHOUT a running LLM or ``nemoguardrails``;
  * the same functions back both the NeMo actions and the CLI ``check``
    subcommand, so the offline heuristics and the live rails never drift.

When ``nemoguardrails`` is installed these functions are also registered as
NeMo actions via :func:`register_actions`; when it is absent, importing this
module still succeeds (the registration decorator is a no-op shim).

This module is NEVER imported by gate.py, graph.py, or mcp_hybrid_server.py.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# --- Soft, import-safe NeMo action decorator -------------------------------
# Importing nemoguardrails must never be required just to import this module.
try:  # pragma: no cover - exercised only when the optional dep is installed
    from nemoguardrails.actions import action as _nemo_action

    NEMO_AVAILABLE = True
except ImportError:  # pragma: no cover - default offline path
    NEMO_AVAILABLE = False

    def _nemo_action(*_args: object, **_kwargs: object):  # type: ignore[no-redef]
        """No-op stand-in so ``@action(...)`` is valid without nemoguardrails."""

        def _decorator(func):  # type: ignore[no-untyped-def]
            return func

        return _decorator


_WORD_RE = re.compile(r"[a-z0-9']+")

# Intent to *modify / override* the soul or identity -- the governance boundary.
# Soul mutation must always carry an explicit human reason via the gate.py
# endpoint; a query that tries to do it inline is refused outright.
_SOUL_MUTATION_RE = re.compile(
    r"\b("
    r"(re)?write\s+your\s+(soul|personality|identity|system\s+prompt)"
    r"|change\s+your\s+(soul|personality|identity|persona|name)"
    r"|update\s+your\s+(soul|personality|identity)"
    r"|forget\s+(who\s+you\s+are|your\s+(soul|identity|personality))"
    r"|you\s+are\s+now\s+(a|an|my)\b"
    r"|from\s+now\s+on\s+you\s+(are|will\s+be)\b"
    r"|ignore\s+your\s+(soul|identity|personality|persona)"
    r"|overwrite\s+your\s+(soul|identity)"
    r")",
    re.IGNORECASE,
)

# Light injection markers (defense-in-depth; the authoritative 32-pattern filter
# stays in utils/sanitizer.py + config.yaml). These exist so the guardrails CLI
# can flag obvious payloads offline without loading the full sanitizer config.
_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard the above",
    "system prompt:",
    "you are now",
    "reveal your prompt",
    "print your instructions",
)


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def is_soul_topic(query: str, soul_topics: Iterable[str]) -> bool:
    """True when the query touches the soul / personality / identity layer.

    Substring match on the configured ``soul_topics`` keywords (case-folded).
    Cheap and deterministic -- used to decide whether the soul-specific topical
    rails apply to a given turn.
    """
    low = query.lower()
    return any(topic.lower() in low for topic in soul_topics)


def detect_soul_mutation_intent(query: str) -> bool:
    """True when the query tries to *modify or override* the soul / identity.

    This is the enforcement arm of CyClaw's Soul-Governance invariant at the
    content layer: autonomous soul modification is never allowed, and a user
    cannot smuggle one in through a normal query. The legitimate path is the
    explicit, reason-bearing gate.py soul-evolution endpoint.
    """
    return bool(_SOUL_MUTATION_RE.search(query))


def scan_injection(text: str) -> list[str]:
    """Return the list of light injection markers found in ``text`` (may be empty)."""
    low = text.lower()
    return [marker for marker in _INJECTION_MARKERS if marker in low]


def grounding_score(answer: str, context: str) -> float:
    """Fraction of answer tokens that also appear in the retrieved context.

    A fast, model-free proxy for RAG faithfulness: 1.0 means every content word
    in the answer is supported by retrieved context; 0.0 means none are. The
    NeMo ``self_check_facts`` output rail is the model-assisted complement -- this
    heuristic is the offline floor that needs no second LLM call.

    Returns 1.0 for an empty answer (nothing unsupported to flag) and 0.0 when
    there is no context to ground against but the answer has content.
    """
    answer_tokens = _tokenize(answer)
    if not answer_tokens:
        return 1.0
    context_tokens = _tokenize(context)
    if not context_tokens:
        return 0.0
    overlap = answer_tokens & context_tokens
    return len(overlap) / len(answer_tokens)


def is_possible_hallucination(answer: str, context: str, threshold: float) -> bool:
    """True when grounding falls below ``threshold`` (likely ungrounded answer)."""
    return grounding_score(answer, context) < threshold


# --- NeMo action registration (no-op when nemoguardrails is absent) ---------


@_nemo_action(name="check_soul_mutation")
async def _action_check_soul_mutation(context: dict | None = None) -> bool:
    """NeMo action: True (allowed) unless the user message tries to mutate the soul."""
    user_message = (context or {}).get("user_message", "")
    return not detect_soul_mutation_intent(user_message)


@_nemo_action(name="check_injection")
async def _action_check_injection(context: dict | None = None, text: str | None = None) -> bool:
    """NeMo action: True (allowed) unless light injection markers are present.

    Accepts an optional explicit ``text`` so the action can scan a string other
    than the current user message. The ``check soul leak`` output flow in
    ``rails.co`` calls ``check_injection(text=$bot_message)`` to reuse this scan on
    the *bot* message; without a ``text`` parameter NeMo would pass that kwarg to a
    function that does not accept it and raise ``TypeError`` at rail-execution
    time. When ``text`` is omitted it falls back to the user message in context,
    preserving the input-rail behaviour.
    """
    target = text if text is not None else (context or {}).get("user_message", "")
    return not scan_injection(target)


@_nemo_action(name="get_grounding_score")
async def _action_get_grounding_score(context: dict | None = None) -> float:
    """NeMo action: token-overlap grounding score for the last bot message."""
    ctx = context or {}
    return grounding_score(ctx.get("bot_message", ""), ctx.get("relevant_chunks", ""))


# Floor used by :func:`_action_is_ungrounded`. Set at engine build via
# :func:`register_actions` so live Colang matches config.yaml's
# guardrails.hallucination_threshold (default 0.18 matches GuardrailsConfig).
_active_hallucination_threshold: float = 0.18


def set_hallucination_threshold(threshold: float) -> None:
    """Update the live-rail grounding floor (call from engine build / tests)."""
    global _active_hallucination_threshold
    if not isinstance(threshold, (int, float)) or not (0.0 <= float(threshold) <= 1.0):
        raise ValueError(
            f"hallucination_threshold must be a float in [0.0, 1.0], got: {threshold!r}"
        )
    _active_hallucination_threshold = float(threshold)


def get_hallucination_threshold() -> float:
    """Return the threshold currently applied by :func:`_action_is_ungrounded`."""
    return _active_hallucination_threshold


@_nemo_action(name="is_ungrounded")
async def _action_is_ungrounded(context: dict | None = None) -> bool:
    """NeMo action: True when the bot answer is below the configured floor.

    Used by the ``check grounding`` Colang flow so the refuse decision shares
    the same ``hallucination_threshold`` as the offline ``safe_generate`` path
    instead of a hardcoded ``0.18`` in rails.co.
    """
    ctx = context or {}
    return is_possible_hallucination(
        ctx.get("bot_message", ""),
        ctx.get("relevant_chunks", ""),
        _active_hallucination_threshold,
    )


def register_actions(
    rails: object,
    *,
    hallucination_threshold: float | None = None,
) -> int:
    """Register the soul/personality actions on a live ``LLMRails`` instance.

    When ``hallucination_threshold`` is provided it becomes the floor for the
    live ``is_ungrounded`` action (wired from ``config.yaml`` at engine build).

    Returns the number of actions registered. A no-op returning 0 when
    ``nemoguardrails`` is not installed (the decorators above are already shims),
    so callers can invoke it unconditionally.
    """
    if hallucination_threshold is not None:
        set_hallucination_threshold(hallucination_threshold)
    if not NEMO_AVAILABLE:
        return 0
    register = getattr(rails, "register_action", None)
    if register is None:  # pragma: no cover - defensive
        return 0
    register(_action_check_soul_mutation, name="check_soul_mutation")
    register(_action_check_injection, name="check_injection")
    register(_action_get_grounding_score, name="get_grounding_score")
    register(_action_is_ungrounded, name="is_ungrounded")
    return 4
