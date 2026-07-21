"""NeMo Guardrails integration wrapper for CyClaw -- skeleton, out-of-band.

This is the single seam between CyClaw and ``nemoguardrails``. It is designed to:

  * import cleanly WITHOUT ``nemoguardrails`` installed (soft import);
  * degrade to a transparent "guardrails skipped" path when the dependency or
    the NeMo config is absent, or when ``guardrails.enabled`` is false;
  * record every decision to the SEPARATE guardrail metrics stream;
  * expose a LangGraph-compatible node helper (:func:`guardrail_safety_node`)
    that is provided for FUTURE wiring only -- it is NOT imported by graph.py in
    this skeleton (see docs/NeMo/later_development_guideline.md for the plan).

Nothing here is imported by gate.py, graph.py, or mcp_hybrid_server.py. The
module-isolation rule (PROJECT_RULES.md) is preserved by construction.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from guardrails.config import GuardrailsConfig, load_guardrails_config
from guardrails.errors import GuardrailsDependencyError, RailsLoadError
from guardrails.metrics import GuardrailMetrics
from guardrails.rails import (
    detect_soul_mutation_intent,
    grounding_score,
    is_possible_hallucination,
    is_soul_topic,
    register_actions,
    scan_injection,
)

logger = logging.getLogger("cyclaw.guardrails")

# --- Soft import: nemoguardrails is optional -------------------------------
try:  # pragma: no cover - exercised only when the optional dep is installed
    from nemoguardrails import LLMRails, RailsConfig

    NEMO_AVAILABLE = True
except ImportError:  # pragma: no cover - default offline path
    LLMRails = None  # type: ignore[assignment,misc]
    RailsConfig = None  # type: ignore[assignment,misc]
    NEMO_AVAILABLE = False


class GuardResult(TypedDict, total=False):
    """Outcome of a guardrailed generation / check."""

    response: str
    blocked: bool
    reason: str | None
    rails_triggered: list[str]
    grounding_score: float | None
    soul_topic: bool
    guardrails_active: bool  # False => skipped (disabled / dep missing)


# Singleton so we don't recompile the rails config on every call.
# Referenced in reset_rails_singleton() and get_cyclaw_guardrails().
_rails_singleton: Any | None = None


def reset_rails_singleton() -> None:
    """Drop the cached ``LLMRails`` (tests / config reload)."""
    global _rails_singleton
    _rails_singleton = None


def _apply_guardrails_config(rails_config: Any, cfg: GuardrailsConfig) -> None:
    """Make ``config.yaml``'s guardrails: block authoritative over the static
    NeMo directory (codex P1).

    ``guardrails/config/config.yml`` is a template; the operator-facing source
    of truth is GuardrailsConfig. Without this override the live engine
    silently used whatever endpoint/model the static file named -- for a long
    time the RETIRED LM Studio port (1234) while the gateway ran Ollama
    (11434), so enabling live rails called a dead endpoint. NeMo Model
    entries expose .engine/.model/.parameters; override only what cfg
    explicitly carries.
    """
    for model in getattr(rails_config, "models", None) or []:
        if cfg.engine:
            model.engine = cfg.engine
        if cfg.model:
            model.model = cfg.model
        params = getattr(model, "parameters", None)
        if params is None:
            params = {}
            model.parameters = params
        if cfg.base_url:
            params["base_url"] = cfg.base_url


def get_cyclaw_guardrails(cfg: GuardrailsConfig | None = None) -> Any:
    """Build (once) and return the live ``LLMRails`` engine.

    Raises :class:`GuardrailsDependencyError` if ``nemoguardrails`` is not
    installed, and :class:`RailsLoadError` if the NeMo config directory cannot be
    loaded. Callers that want graceful degradation should use
    :func:`safe_generate` instead, which never raises for the missing-dep case.
    """
    global _rails_singleton
    if cfg is None:
        cfg = load_guardrails_config()
    if not NEMO_AVAILABLE:
        raise GuardrailsDependencyError(
            "nemoguardrails is not installed; install it to enable live rails "
            "(`pip install nemoguardrails`). The skeleton runs without it.",
            details={"degraded": True},
        )
    if _rails_singleton is not None:
        return _rails_singleton
    if not cfg.nemo_config_present:
        raise RailsLoadError(
            "NeMo config files not found",
            details={"expected": [str(cfg.config_yml_path), str(cfg.rails_co_path)]},
        )
    try:
        rails_config = RailsConfig.from_path(cfg.nemo_config_dir)
        _apply_guardrails_config(rails_config, cfg)
        rails = LLMRails(rails_config)
    except Exception as exc:  # noqa: BLE001 - surface any NeMo load failure as RailsLoadError
        raise RailsLoadError(f"failed to load NeMo rails: {exc}", details={"dir": cfg.nemo_config_dir}) from exc
    register_actions(rails)
    _rails_singleton = rails
    return rails


def _offline_checks(query: str, cfg: GuardrailsConfig) -> tuple[bool, list[str]]:
    """Run the model-free heuristic rails.

    Returns ``(blocked, rails_triggered)``. These are the offline floor that runs
    whether or not ``nemoguardrails`` is present, so the soul / personality and
    injection protections never depend on the heavy dependency.

    Grounding is intentionally NOT computed here: it is an *output*-side check that
    compares the model response against the retrieved context, and is evaluated
    later in ``safe_generate`` via ``grounding_score(response, context)``. The
    previous ``grounding_score(context, context)`` compared the context to itself
    (always ~1.0) and its result was discarded by the caller -- dead, misleading work.
    """
    triggered: list[str] = []
    if scan_injection(query):
        triggered.append("check_injection")
    if detect_soul_mutation_intent(query):
        triggered.append("check_soul_mutation")
    blocked = bool(triggered)
    return blocked, triggered


def check_input(
    query: str, *, cfg: GuardrailsConfig | None = None, metrics: GuardrailMetrics | None = None
) -> dict[str, Any]:
    """Phase 2 input rail -- the sync, offline-only entry point for graph.py.

    Unlike :func:`safe_generate`, this NEVER generates: it only runs the
    model-free heuristic floor (:func:`_offline_checks`), so wiring it into the
    graph as ``guardrail_input_node`` cannot double-generate an answer -- the
    disqualifier that keeps :func:`guardrail_safety_node` unwired (see
    docs/NeMo/later_development_guideline.md). ``utils/guardrail_bridge.py``
    is the only production caller and already short-circuits to ``None``
    before this is ever reached when guardrails are disabled, but this
    function is correct standalone too (e.g. from the CLI).

    Returns ``{"blocked": bool, "message": str, "rails": list[str]}``.
    """
    if cfg is None:
        cfg = load_guardrails_config()
    if metrics is None:
        metrics = GuardrailMetrics(cfg.metrics_path)

    if is_soul_topic(query, cfg.soul_topics):
        metrics.record_soul_topic(query=query)

    blocked, triggered = _offline_checks(query, cfg)
    if not blocked:
        metrics.record_allowed(stage="input", query=query)
        return {"blocked": False, "message": "", "rails": []}

    rail = triggered[0]
    metrics.record_blocked(stage="input", rail=rail, reason="offline heuristic", query=query)
    # A single input can trip more than one offline rail; mirrors safe_generate's
    # same fix -- record_blocked only counts the first rail, so record the rest.
    for extra_rail in triggered[1:]:
        metrics.record_rail(extra_rail, stage="input", query=query)
    return {"blocked": True, "message": cfg.block_message, "rails": triggered}


async def safe_generate(
    prompt: str,
    *,
    context: str = "",
    cfg: GuardrailsConfig | None = None,
    metrics: GuardrailMetrics | None = None,
) -> GuardResult:
    """Main integration point -- the guardrailed analogue of a raw LLM call.

    Behaviour matrix:

      * guardrails disabled OR nemoguardrails missing  -> offline heuristic rails
        only (injection + soul-mutation block), recorded as a "skipped" live-rails
        turn but still enforcing the offline floor;
      * guardrails enabled AND nemoguardrails present   -> offline floor first
        (fail fast, no LLM spend on an obvious block), then the live NeMo engine.

    Never raises for the missing-dependency case -- it degrades. It still records
    every decision to the guardrail metrics stream.
    """
    if cfg is None:
        cfg = load_guardrails_config()
    if metrics is None:
        metrics = GuardrailMetrics(cfg.metrics_path)

    soul = is_soul_topic(prompt, cfg.soul_topics)
    if soul:
        metrics.record_soul_topic(query=prompt)

    blocked, triggered = _offline_checks(prompt, cfg)
    if blocked:
        rail = triggered[0]
        metrics.record_blocked(stage="input", rail=rail, reason="offline heuristic", query=prompt)
        # A single input can trip more than one offline rail (e.g. injection AND
        # soul-mutation). record_blocked only counts the first rail, so record the
        # remaining firings explicitly -- otherwise the analyzer's rails_by_name
        # undercounts every rail past the first while rails_triggered lists them all.
        for extra_rail in triggered[1:]:
            metrics.record_rail(extra_rail, stage="input", query=prompt)
        return GuardResult(
            response=cfg.block_message,
            blocked=True,
            reason=f"input rail: {rail}",
            rails_triggered=triggered,
            grounding_score=None,
            soul_topic=soul,
            guardrails_active=cfg.enabled and NEMO_AVAILABLE,
        )

    # Degraded path: no live NeMo engine. Offline floor already passed.
    if not (cfg.enabled and NEMO_AVAILABLE):
        reason = "guardrails disabled" if not cfg.enabled else "nemoguardrails not installed"
        metrics.record_skipped(reason=reason, query=prompt)
        return GuardResult(
            response="",
            blocked=False,
            reason=reason,
            rails_triggered=triggered,
            grounding_score=None,
            soul_topic=soul,
            guardrails_active=False,
        )

    # Live path: hand off to NeMo. Kept defensive -- any failure degrades, never
    # crashes the caller.
    try:
        rails = get_cyclaw_guardrails(cfg)
        messages = [{"role": "user", "content": prompt}]
        # Retrieved context travels via NeMo's action/message context -- the
        # grounding action reads context["relevant_chunks"] (codex P1). The
        # previous system-message injection never reached that channel, so the
        # configured output rail could not see the evidence it scores against.
        gen_context = {"relevant_chunks": context} if context else None
        result = await rails.generate_async(messages=messages, context=gen_context)
        response = result.get("content", "") if isinstance(result, dict) else str(result)
    except (GuardrailsDependencyError, RailsLoadError) as exc:
        metrics.record_skipped(reason=f"rails unavailable: {exc.code}", query=prompt)
        return GuardResult(
            response="", blocked=False, reason=str(exc.message),
            rails_triggered=triggered, grounding_score=None, soul_topic=soul, guardrails_active=False,
        )
    except Exception as exc:  # noqa: BLE001 - live-provider failure must also degrade
        # Connect/timeout/5xx/Colang runtime errors from generate_async
        # previously PROPAGATED, contradicting the documented degrade-never-
        # crash contract (codex P2). Redact to the exception TYPE name only --
        # provider errors can echo URLs, headers, or request bodies.
        metrics.record_skipped(reason=f"rails provider error: {type(exc).__name__}", query=prompt)
        return GuardResult(
            response="", blocked=False, reason=f"rails provider error: {type(exc).__name__}",
            rails_triggered=triggered, grounding_score=None, soul_topic=soul, guardrails_active=False,
        )

    # Output rail: offline hallucination check against retrieved context.
    # Evaluate grounding UNCONDITIONALLY -- an empty/absent context is the case
    # most likely to produce an ungrounded answer, yet the previous
    # ``if context`` guard skipped the check exactly then and let every
    # no-context generation through. The Colang ``check grounding`` flow
    # (config/rails.co) always executes ``get_grounding_score`` and refuses below
    # the floor, so skipping it here drifted the offline floor from the live rail.
    # ``grounding_score`` already returns 1.0 for an empty answer (nothing to
    # flag) and 0.0 when there is content but no supporting context, so the
    # unconditional call is well-defined for every input.
    score = grounding_score(response, context)
    out_blocked = is_possible_hallucination(response, context, cfg.hallucination_threshold)
    if out_blocked:
        metrics.record_hallucination(score=score or 0.0, threshold=cfg.hallucination_threshold, query=prompt)
        metrics.record_blocked(stage="output", rail="check_grounding", reason="low grounding", query=prompt)
        return GuardResult(
            response=cfg.block_message, blocked=True, reason="output rail: check_grounding",
            rails_triggered=[*triggered, "check_grounding"], grounding_score=score,
            soul_topic=soul, guardrails_active=True,
        )

    metrics.record_allowed(score=score, query=prompt)
    return GuardResult(
        response=response, blocked=False, reason=None, rails_triggered=triggered,
        grounding_score=score, soul_topic=soul, guardrails_active=True,
    )


# --- LangGraph-compatible node helper (PROVIDED FOR FUTURE WIRING ONLY) ------
# This is intentionally NOT imported by graph.py in the skeleton. The wiring plan
# (a visible node + conditional edge, never hidden middleware -- topology=policy)
# is documented in docs/NeMo/later_development_guideline.md.


async def guardrail_safety_node(state: dict[str, Any], cfg: GuardrailsConfig | None = None) -> dict[str, Any]:
    """Example LangGraph node: run guardrails over the current state.

    Reads ``state['query']`` and ``state['retrieved_context']`` (or builds it from
    ``retrieved_docs``) and returns ONLY the new keys to merge -- it never mutates
    the input state in place, matching CyClaw's node contract.
    """
    if cfg is None:
        cfg = load_guardrails_config()
    query = state.get("query", "")
    context = state.get("retrieved_context", "")
    if not context and state.get("retrieved_docs"):
        context = "\n\n".join(d.get("text", "") for d in state["retrieved_docs"])

    result = await safe_generate(query, context=context, cfg=cfg)
    return {
        "guarded_response": result.get("response", ""),
        "safety_blocked": result.get("blocked", False),
        "safety_reason": result.get("reason"),
        "safety_rails_triggered": result.get("rails_triggered", []),
        "safety_grounding_score": result.get("grounding_score"),
        "safety_soul_topic": result.get("soul_topic", False),
    }
