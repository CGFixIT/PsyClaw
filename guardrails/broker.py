"""NeMo ``check()`` around the existing CyClaw generation helper.

Phase 3 of issue #1134: NVIDIA's non-generating ``LLMRails.check`` wraps
``client.generate``. This module never grants I3 and never calls
``generate_async``. Graph sees it only via ``utils/guardrail_bridge``.
"""

from __future__ import annotations

import logging
from typing import Any

from guardrails.config import GuardrailsConfig
from guardrails.errors import GuardrailsDependencyError, RailsLoadError
from guardrails.integration import get_cyclaw_guardrails
from guardrails.metrics import GuardrailMetrics
from utils.errors import RAGError

logger = logging.getLogger("cyclaw.guardrails.broker")


def _status_blocked(result: object) -> bool:
    status = getattr(result, "status", None)
    return "BLOCKED" in str(getattr(status, "name", status)).upper()


def _live_check(rails: object, messages: list[dict[str, str]]) -> object | None:
    """Call NVIDIA ``check(messages=...)``. None on degrade."""
    check = getattr(rails, "check", None)
    if check is None:
        return None
    try:
        return check(messages=messages)
    except TypeError:
        return check(messages)


class GuardrailBroker:
    """Maps NVIDIA ``RailsResult`` to a block/allow decision. Never grants a route."""

    def __init__(self, cfg: GuardrailsConfig, metrics: GuardrailMetrics) -> None:
        self.cfg = cfg
        self.metrics = metrics
        self._rails: object | None = None

    def _engine(self) -> object | None:
        if self._rails is not None:
            return self._rails
        try:
            self._rails = get_cyclaw_guardrails(self.cfg)
        except (GuardrailsDependencyError, RailsLoadError) as exc:
            logger.warning("NeMo check engine unavailable (%s); degrade", type(exc).__name__)
            self.metrics.record_skipped(reason=type(exc).__name__)
            return None
        return self._rails

    def check_user(self, query: str) -> bool:
        """True when live input rails BLOCK. False = allow or degrade."""
        rails = self._engine()
        if rails is None:
            return False
        try:
            result = _live_check(rails, [{"role": "user", "content": query}])
        except Exception:
            logger.warning("NeMo check() input failed; degrade", exc_info=True)
            self.metrics.record_skipped(reason="check_input_error", query=query)
            return False
        if result is not None and _status_blocked(result):
            self.metrics.record_blocked(stage="input", rail="nemo_check", reason="blocked", query=query)
            return True
        return False

    def check_assistant(self, query: str, answer: str) -> bool:
        """True when live output rails BLOCK. False = allow or degrade."""
        rails = self._engine()
        if rails is None:
            return False
        try:
            result = _live_check(
                rails,
                [
                    {"role": "user", "content": query},
                    {"role": "assistant", "content": answer},
                ],
            )
        except Exception:
            logger.warning("NeMo check() output failed; degrade", exc_info=True)
            self.metrics.record_skipped(reason="check_output_error", query=query)
            return False
        if result is not None and _status_blocked(result):
            self.metrics.record_blocked(stage="output", rail="nemo_check", reason="blocked", query=query)
            return True
        return False


def guarded_generate(
    client: Any,
    prompt: str,
    *,
    query: str,
    label: str,
    spend_context: dict[str, object] | None,
    cfg: GuardrailsConfig,
    metrics: GuardrailMetrics,
) -> tuple[str, str | None]:
    """Input ``check()`` → existing ``client.generate`` → output ``check()``."""
    broker = GuardrailBroker(cfg, metrics)
    if broker.check_user(query or prompt):
        return cfg.block_message, None
    try:
        if spend_context is None:
            answer = client.generate(prompt)
        else:
            answer = client.generate(prompt, spend_context=spend_context)
    except RAGError as exc:
        return f"[{label} Error: {exc.message}]", f"{exc.code}: {exc.message}"
    if broker.check_assistant(query or prompt, answer):
        return cfg.block_message, None
    return answer, None
