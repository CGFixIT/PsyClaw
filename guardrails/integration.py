"""NeMo Guardrails integration wrapper for CyClaw.

This is the single seam between CyClaw and ``nemoguardrails``. It is designed to:

  * import cleanly WITHOUT ``nemoguardrails`` installed (soft import);
  * degrade to a transparent "guardrails skipped" path when the dependency or
    the NeMo config is absent, or when ``guardrails.enabled`` is false;
  * record every decision to the SEPARATE guardrail metrics stream;
  * export ``check_input`` / ``check_output`` for the live graph nodes
    (reached only via ``utils/guardrail_bridge.py``; ``graph.py`` never
    imports this module -- I6).

``guardrail_safety_node`` below is an unused example helper, not the live
path. ``gate.py`` / ``graph.py`` / ``mcp_hybrid_server.py`` must not import
this package (``tests/test_guardrails_isolation.py``).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any, TypedDict

from utils.telemetry_kill import apply_telemetry_kill

# Kill telemetry BEFORE the sibling guardrails imports below, not merely before
# the nemoguardrails soft import: guardrails/rails.py performs its own soft
# `import nemoguardrails` at module level, so importing it first would give the
# optional dependency an unkilled window. The package __init__ already applies
# the kill ahead of any submodule on the normal `import guardrails.*` path --
# this earlier position makes THIS module self-sufficient too (idempotent with
# guardrails/__init__.py), which is the property invariant-guard's G1 package
# rule checks. The # noqa: E402 comments below are load-bearing, not clutter.
apply_telemetry_kill()

from guardrails.config import GuardrailsConfig, load_guardrails_config  # noqa: E402
from guardrails.errors import GuardrailsDependencyError, RailsLoadError  # noqa: E402
from guardrails.metrics import GuardrailMetrics  # noqa: E402
from guardrails.rails import (  # noqa: E402
    detect_soul_leak,
    detect_soul_mutation_intent,
    grounding_score,
    is_possible_hallucination,
    is_soul_topic,
    register_actions,
    scan_injection,
)
from utils.onnx_telemetry import suppress_onnx_telemetry  # noqa: E402

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


# Engine cache keyed by (policy_fingerprint, provider, model, endpoint).
# A process-global unkeyed singleton ignored config drift (issue #1134 Phase 2a).
_rails_cache: dict[tuple[str, str, str, str], Any] = {}
_rails_lock = threading.Lock()
_rails_admit = threading.Semaphore(4)
_breaker_failures = 0
_BREAKER_LIMIT = 3
_MAIN_MODEL_TYPES = frozenset({"main", "", None})


def reset_rails_singleton() -> None:
    """Drop cached ``LLMRails`` engines (tests / config reload)."""
    global _breaker_failures
    with _rails_lock:
        _rails_cache.clear()
        _breaker_failures = 0


def _model_type(model: Any) -> str | None:
    raw = getattr(model, "type", None)
    if raw is None:
        return None
    return str(raw)


def _apply_guardrails_config(rails_config: Any, cfg: GuardrailsConfig) -> None:
    """Override generation (``type: main``) from ``config.yaml``.

    Self-check / facts / classifier entries keep the template's model tag so a
    dedicated check model is not smashed onto the generation tag (issue #1134).
    Untyped entries are treated as main (legacy templates).
    """
    for model in getattr(rails_config, "models", None) or []:
        if _model_type(model) not in _MAIN_MODEL_TYPES:
            continue
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
        # NeMo 0.24's default framework is its OpenAI-compatible HTTP client,
        # not LangChain. check_langchain_kwargs refuses a nested model_kwargs
        # key and tells the caller to unpack those fields into parameters
        # (issue #1338).
        nested = params.pop("model_kwargs", None)
        if isinstance(nested, dict):
            for key, value in nested.items():
                if key == "reasoning_effort" or key in params:
                    continue
                params[key] = value
        if cfg.reasoning_effort is not None:
            params["reasoning_effort"] = cfg.reasoning_effort


def policy_fingerprint(cfg: GuardrailsConfig) -> str:
    """SHA-256 of the NeMo policy bundle (config.yml + rails.co + sibling files)."""
    root = Path(cfg.nemo_config_dir)
    hasher = hashlib.sha256()
    if not root.is_dir():
        raise RailsLoadError(
            "NeMo config directory missing for fingerprint",
            details={"dir": str(root)},
        )
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def _cache_key(cfg: GuardrailsConfig) -> tuple[str, str, str, str]:
    return (policy_fingerprint(cfg), cfg.engine, cfg.model, cfg.base_url)


def _refuse_iorails() -> None:
    flag = os.environ.get("NEMO_GUARDRAILS_IORAILS_ENGINE", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        raise RailsLoadError(
            "IORails is not supported; unset NEMO_GUARDRAILS_IORAILS_ENGINE",
            details={"env": "NEMO_GUARDRAILS_IORAILS_ENGINE"},
        )


def get_cyclaw_guardrails(cfg: GuardrailsConfig | None = None) -> Any:
    """Build (once per policy/provider/model/endpoint) the live ``LLMRails`` engine.

    Raises :class:`GuardrailsDependencyError` if ``nemoguardrails`` is not
    installed, and :class:`RailsLoadError` if the NeMo config directory cannot be
    loaded. Callers that want graceful degradation should use
    :func:`safe_generate` instead, which never raises for the missing-dep case.
    """
    global _breaker_failures
    if cfg is None:
        cfg = load_guardrails_config()
    if not NEMO_AVAILABLE:
        raise GuardrailsDependencyError(
            "nemoguardrails is not installed; install it to enable live rails "
            "(`pip install nemoguardrails`). The skeleton runs without it.",
            details={"degraded": True},
        )
    if _breaker_failures >= _BREAKER_LIMIT:
        raise RailsLoadError(
            "NeMo engine circuit breaker open",
            details={"failures": _breaker_failures},
        )
    _refuse_iorails()
    if not cfg.nemo_config_present:
        raise RailsLoadError(
            "NeMo config files not found",
            details={"expected": [str(cfg.config_yml_path), str(cfg.rails_co_path)]},
        )
    key = _cache_key(cfg)
    with _rails_lock:
        cached = _rails_cache.get(key)
        if cached is not None:
            return cached
    admitted = _rails_admit.acquire(timeout=30)
    if not admitted:
        raise RailsLoadError("NeMo engine admission timeout", details={"timeout_s": 30})
    try:
        with _rails_lock:
            cached = _rails_cache.get(key)
            if cached is not None:
                return cached
        try:
            rails_config = RailsConfig.from_path(cfg.nemo_config_dir)
            _apply_guardrails_config(rails_config, cfg)
            # Post-import ONNX suppression, immediately before the engine that
            # constructs fastembed/ONNX sessions. force_import: ONNX use is
            # imminent here, so the API must run even if nothing imported
            # onnxruntime yet. The env half (ORT_DISABLE_TELEMETRY) was set by
            # apply_telemetry_kill() above, before any import.
            suppress_onnx_telemetry(force_import=True)
            rails = LLMRails(rails_config)
        except RailsLoadError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any NeMo load failure as RailsLoadError
            with _rails_lock:
                _breaker_failures += 1
            raise RailsLoadError(
                f"failed to load NeMo rails: {exc}", details={"dir": cfg.nemo_config_dir}
            ) from exc
        register_actions(rails, hallucination_threshold=cfg.hallucination_threshold)
        with _rails_lock:
            _rails_cache[key] = rails
            _breaker_failures = 0
        return rails
    finally:
        _rails_admit.release()


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
    if "check_injection" in cfg.input_rails and scan_injection(query):
        triggered.append("check_injection")
    if "check_soul_mutation" in cfg.input_rails and detect_soul_mutation_intent(query):
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


def check_output(
    answer: str,
    context: str,
    *,
    query: str = "",
    cfg: GuardrailsConfig | None = None,
    metrics: GuardrailMetrics | None = None,
) -> dict[str, Any]:
    """Phase 4 output rail -- sync, offline-only, NEVER generates.

    Mirrors check_input's non-generating guarantee in reverse. Grounding plus
    Phase 4b ``detect_soul_leak`` when that name is in ``output_rails``.
    Does not reuse ``scan_injection`` on the answer.

    Returns ``{"blocked": bool, "message": str, "rails": list[str]}``.
    """
    if cfg is None:
        cfg = load_guardrails_config()
    if metrics is None:
        metrics = GuardrailMetrics(cfg.metrics_path)

    triggered: list[str] = []
    if "check_soul_leak" in cfg.output_rails and detect_soul_leak(answer):
        triggered.append("check_soul_leak")
    score = grounding_score(answer, context)
    if "check_grounding" in cfg.output_rails and is_possible_hallucination(
        answer, context, cfg.hallucination_threshold
    ):
        triggered.append("check_grounding")
        metrics.record_hallucination(
            score=score, threshold=cfg.hallucination_threshold, query=query,
        )
    if not triggered:
        metrics.record_allowed(stage="output", score=score, query=query)
        return {"blocked": False, "message": "", "rails": []}

    rail = triggered[0]
    reason = "soul leak" if rail == "check_soul_leak" else "low grounding"
    metrics.record_blocked(stage="output", rail=rail, reason=reason, query=query)
    for extra_rail in triggered[1:]:
        metrics.record_rail(extra_rail, stage="output", query=query)
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
        # Retrieved context travels as a context-role message -- the ONLY
        # context channel the real LLMRails.generate_async supports. Its
        # signature (prompt/messages/options/state/streaming_handler) has no
        # ``context`` kwarg: passing one raises TypeError, which the degrade
        # handler below then masked as a "skipped" turn -- live rails looked
        # enabled while never running (review P1 on PR #590). A context-role
        # message's content dict becomes the runtime ``context`` the grounding
        # action reads as context["relevant_chunks"] (codex P1); the previous
        # system-message injection never reached that channel either (NeMo
        # docs: "System messages are not yet supported").
        messages: list[dict] = []
        if context:
            messages.append({"role": "context", "content": {"relevant_chunks": context}})
        messages.append({"role": "user", "content": prompt})
        result = await rails.generate_async(messages=messages)
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
    out_blocked = "check_grounding" in cfg.output_rails and is_possible_hallucination(
        response, context, cfg.hallucination_threshold
    )
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


# Unused example helper. Live graph nodes are guardrail_input / guardrail_output
# in graph.py; they call check_input / check_output via utils/guardrail_bridge.py.


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
