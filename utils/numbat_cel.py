"""CEL sanitizer backend for Numbat-shaped structured-field rules.

Optional, default-off, and monitor-only.  Rules evaluate over safe,
already-hashed/structured request fields (never raw prompt text) and emit a
low-confidence Numbat event on match.  They do NOT block ``/query``; the regex
banned_patterns list remains the fail-closed baseline.

The ``cel-python`` import is lazy and guarded by ``numbat.cel.enabled``.  When
disabled, this module never imports the optional dependency, so the core
request path stays free of it (I6 hygiene).
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("cyclaw.numbat_cel")


def _cel_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(cfg, dict):
        return {}
    block = (cfg.get("numbat") or {}).get("cel") or {}
    return block if isinstance(block, dict) else {}


def _is_literal_true(value: Any) -> bool:
    return value is True


def _compile_rules(rules: list[Any]) -> list[tuple[int, Any]]:
    """Compile CEL expression strings; skip bad rules with a warning."""
    try:
        import celpy
    except Exception as exc:  # noqa: BLE001 - fail-open: missing optional dep
        logger.warning("cel-python is not installed: %s", exc)
        return []

    env = celpy.Environment()
    compiled: list[tuple[int, Any]] = []
    for idx, expr in enumerate(rules):
        if not isinstance(expr, str) or not expr:
            logger.warning("numbat.cel.rules[%d] is not a string; skipping", idx)
            continue
        try:
            ast = env.compile(expr)
            compiled.append((idx, env.program(ast)))
        except Exception as exc:  # noqa: BLE001 - one bad rule must not break others
            logger.warning("numbat.cel.rules[%d] failed to compile: %s", idx, exc)
    return compiled


def _build_activation(fields: dict[str, Any]) -> dict[str, Any]:
    """Convert plain Python values to CEL-friendly values when possible."""
    try:
        import celpy

        return {k: celpy.json_to_cel(v) for k, v in fields.items()}
    except Exception:  # noqa: BLE001 - degrade to native values
        return fields


def evaluate_cel_monitor(
    *,
    query_hash: str | None = None,
    top_score: float | None = None,
    answer_model: str | None = None,
    guardrail_blocked: bool | None = None,
    guardrail_rails: list[str] | None = None,
    model_provider: str | None = None,
    source_hashes: list[str] | None = None,
    cfg: dict[str, Any] | None = None,
) -> list[int]:
    """Evaluate enabled CEL rules over structured fields.

    Returns a list of matched rule indices.  Never raises: a disabled config,
    missing optional dependency, or bad rule results in an empty list so the
    request path cannot be derailed by a policy-engine problem.
    """
    block = _cel_cfg(cfg)
    if not _is_literal_true(block.get("enabled", False)):
        return []

    rules = block.get("rules") or []
    if not isinstance(rules, list) or not rules:
        return []

    compiled = _compile_rules(rules)
    if not compiled:
        return []

    fields: dict[str, Any] = {
        "query_hash": query_hash or "",
        "top_score": top_score if top_score is not None else 0.0,
        "answer_model": answer_model or "",
        "guardrail_blocked": bool(guardrail_blocked),
        "guardrail_rails": list(guardrail_rails or []),
        "model_provider": model_provider or "",
        "source_hashes": list(source_hashes or []),
    }
    activation = _build_activation(fields)
    max_ms = block.get("max_rule_ms", 20)
    matches: list[int] = []

    for idx, prgm in compiled:
        start = time.monotonic()
        try:
            result = prgm.evaluate(activation)
            if bool(result):
                matches.append(idx)
        except Exception as exc:  # noqa: BLE001 - fail-open per rule
            logger.warning("numbat.cel.rules[%d] evaluation failed: %s", idx, exc)
        elapsed_ms = (time.monotonic() - start) * 1000
        if elapsed_ms > max_ms:
            logger.warning(
                "numbat.cel.rules[%d] exceeded %sms budget (%.2fms)",
                idx, max_ms, elapsed_ms,
            )

    return matches


def monitor_request(
    *,
    query_hash: str | None = None,
    top_score: float | None = None,
    answer_model: str | None = None,
    guardrail_blocked: bool | None = None,
    guardrail_rails: list[str] | None = None,
    model_provider: str | None = None,
    source_hashes: list[str] | None = None,
    cfg: dict[str, Any] | None = None,
) -> None:
    """Monitor-only CEL hook.  Emits a Numbat event on rule match; never blocks."""
    matches = evaluate_cel_monitor(
        query_hash=query_hash,
        top_score=top_score,
        answer_model=answer_model,
        guardrail_blocked=guardrail_blocked,
        guardrail_rails=guardrail_rails,
        model_provider=model_provider,
        source_hashes=source_hashes,
        cfg=cfg,
    )
    if not matches:
        return

    try:
        from utils.numbat_emitter import emit_numbat_event
    except Exception as exc:  # noqa: BLE001 - projection must not fail the caller
        logger.warning("numbat_cel could not load numbat_emitter: %s", exc)
        return

    reason = f"cel_rules_matched:{','.join(str(i) for i in matches)}"
    try:
        emit_numbat_event(
            "permission.denied",
            model=answer_model,
            model_provider=model_provider,
            tool_name="cel_monitor",
            decision="denied",
            approval_required=True,
            approval_decision="denied",
            approval_reason=reason,
            actor="system",
            entrypoint="cyclaw",
            tags=["cel_monitor", f"rules:{','.join(str(i) for i in matches)}"],
            confidence="low",
            cfg=cfg,
        )
    except Exception as exc:  # noqa: BLE001 - derived stream must never fail the caller
        logger.warning("numbat_cel emit failed: %s", exc)
