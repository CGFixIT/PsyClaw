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
from collections.abc import Iterable, Sequence
from functools import lru_cache
from typing import Any

# Reuse the soul scanner's OWASP baseline so the scanners never drift. Imported
# rather than moved: utils/personality.py enforces ENFORCED_SOUL_PATTERNS at the
# soul-write boundary, and relocating the constant out of that module would put
# an invariant-I5 scan behind a cross-module import for no gain.
from utils.personality import OWASP_INJECTION_PATTERNS

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

# Light injection markers (defense-in-depth; the authoritative 39-pattern filter
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
    """Return the list of light injection markers found in ``text`` (may be empty).

    Deliberately the SMALL scan, and deliberately not the full pattern set built
    by :func:`build_injection_patterns` below. Two reasons, both load-bearing:

      * Its only production caller is the query-path input rail
        (``integration.check_input`` via ``_offline_checks``), which sits BEHIND
        ``utils/sanitizer.py``'s 39-pattern fail-closed filter -- a query
        carrying an obvious payload never reaches it.
      * ``OWASP_INJECTION_PATTERNS`` is documented in ``utils/personality.py`` as
        the ADVISORY set (it carries ``act as`` / ``you are now`` / ``pretend to
        be``, "legitimate in author-controlled identity statements"), which is
        why the soul write boundary enforces the narrower
        ``ENFORCED_SOUL_PATTERNS`` instead. Blocking a live query on an advisory
        pattern is a category error; scanning untrusted third-party content with
        it is exactly what it is for.

    Out-of-band callers scanning content the operator did not author (skill
    definitions from GitHub, files from shares, harness-optimizer candidates)
    want :func:`build_injection_patterns` / :func:`scan_injection_patterns`.
    """
    low = text.lower()
    return [marker for marker in _INJECTION_MARKERS if marker in low]


# --- Shared full-strength scanner (out-of-band callers) ---------------------
# Single home for the OWASP-union scan that agentic/registry.py,
# agentic/fsconnect/client.py, and agentic/harness_optimizer/governance.py each
# used to rebuild independently. Consolidated here so the pattern set, the
# dedup order, the IGNORECASE flag, and the drop-on-uncompilable behaviour
# cannot drift between the three call sites. Callers keep their OWN policy on
# top of it (fsconnect stays advisory, the skills registry stays fail-closed) --
# only the pattern construction is shared, never the enforcement decision.


def build_injection_pattern_sources(cfg: dict[str, Any]) -> list[str]:
    """Return ``OWASP_INJECTION_PATTERNS`` ∪ ``policy.prompt_filter.banned_patterns``.

    Order-preserving (OWASP baseline first, then config entries not already
    present) so the compiled tuple is stable and the ``lru_cache`` in
    :func:`compile_injection_patterns` keys deterministically.

    Non-string config entries are skipped: ``re.compile`` raises ``TypeError``
    (not ``re.error``) on one, which would escape the compile loop's handler and
    crash the caller. Dropping them here means a malformed ``banned_patterns``
    entry degrades the scan by one pattern instead of taking down the operation.
    """
    sources: list[str] = list(OWASP_INJECTION_PATTERNS)
    pf = (cfg.get("policy") or {}).get("prompt_filter") or {}
    for pattern in pf.get("banned_patterns") or []:
        if isinstance(pattern, str) and pattern not in sources:
            sources.append(pattern)
    return sources


@lru_cache(maxsize=8)
def compile_injection_patterns(sources: tuple[str, ...]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Compile (and memoize) a pattern-source tuple. Pure: same sources -> same result.

    Memoized because the agentic CLI builds short-lived objects per op and each
    construction otherwise recompiles the whole set. Keyed on the source tuple,
    so a config change with different patterns yields a fresh entry.

    An uncompilable pattern is skipped rather than raised. Callers that treat the
    scan as an enforced gate must check for a shrunken/empty result themselves --
    ``agentic/registry.py`` does exactly that and refuses to construct rather than
    operate with a defeated gate.
    """
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for pattern in sources:
        try:
            compiled.append((pattern, re.compile(pattern, re.IGNORECASE)))
        except re.error:
            continue
    return tuple(compiled)


def build_injection_patterns(cfg: dict[str, Any]) -> list[tuple[str, re.Pattern[str]]]:
    """Compile ``OWASP ∪ policy.prompt_filter.banned_patterns`` for ``cfg``."""
    return list(compile_injection_patterns(tuple(build_injection_pattern_sources(cfg))))


def scan_injection_patterns(
    text: str, patterns: Sequence[tuple[str, re.Pattern[str]]]
) -> list[str]:
    """Return the pattern SOURCES matching ``text`` (may be empty).

    Returns sources rather than match objects so callers can audit-log which
    rule fired without echoing the matched text itself.
    """
    return [src for src, pat in patterns if pat.search(text)]


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
