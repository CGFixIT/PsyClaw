"""Synchronous pre-action hook runner for external LLM fallbacks.

CyClaw invokes the configured command before any call to Grok or Claude.
The command receives a JSON payload on stdin describing the proposed action
(provider, model, query_hash) and signals its decision via exit code:

  * exit 0  -> allow (proceed to the provider)
  * exit 2  -> deny (route to audit_logger instead)
  * any other exit, crash, or timeout -> fail-closed deny + audit

This module is intentionally isolated from the request path's optional layers:
it does not import agentic, sync, guardrails, harness, telegram, opentweet, or
numbat_emitter.  The external command itself (often a Numbat hook) is
responsible for emitting any network.indicator events it wants to record.
"""

from __future__ import annotations

import json
import logging
import subprocess  # nosec B404 - list-form only, no shell, operator-configured argv
from typing import Any

logger = logging.getLogger("cyclaw.external_pre_hook")

DEFAULT_TIMEOUT_SEC = 5
MIN_TIMEOUT_SEC = 1
MAX_TIMEOUT_SEC = 30

# Only the literal Python True arms the hook / emission. A YAML string such as
# "false" or "true" must not be treated as a security-enabling boolean.


def _is_literal_true(value: Any) -> bool:
    return value is True


# Provider string -> Numbat-friendly model_provider value. Keep in sync with
# utils/numbat_emitter._AUDIT_MODEL_PROVIDERS where possible.
_PROVIDER_TO_VENDOR = {
    "grok": "xai",
    "claude": "anthropic",
}


def _hook_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Return the policy.fallback.pre_action_hook block, if any."""
    if not isinstance(cfg, dict):
        return {}
    fallback = cfg.get("policy", {}).get("fallback", {})
    if not isinstance(fallback, dict):
        return {}
    block = fallback.get("pre_action_hook", {})
    return block if isinstance(block, dict) else {}


def _normalize_timeout(raw: Any) -> int:
    """Coerce timeout to an integer inside [1, 30]; default to 5 on bad input."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SEC
    if value < MIN_TIMEOUT_SEC:
        return MIN_TIMEOUT_SEC
    if value > MAX_TIMEOUT_SEC:
        return MAX_TIMEOUT_SEC
    return value


def _emit_hook_verdict(
    *,
    provider: str,
    model: str,
    query_hash: str,
    event_type: str,
    reason_code: str,
    confidence: str,
    cfg: dict[str, Any] | None,
    decision: str | None = None,
) -> None:
    """Project a hook verdict into the Numbat stream, fail-soft.

    Lazy-imports utils.numbat_emitter so this module stays free of a module-
    scope emitter import (I6 hygiene) and so a projection failure cannot change
    the hook's graph verdict.
    """
    try:
        from utils.numbat_emitter import emit_numbat_event
    except Exception as exc:  # noqa: BLE001 - projection must not break the hook
        logger.warning("pre_action_hook could not load numbat_emitter: %s", exc)
        return

    try:
        emit_numbat_event(
            event_type,
            model=model,
            model_provider=_PROVIDER_TO_VENDOR.get(provider, provider),
            tool_name="external_llm_call",
            decision=decision,
            approval_required=True if event_type == "permission.denied" else None,
            approval_decision="denied" if event_type == "permission.denied" else None,
            approval_reason=reason_code,
            actor="system",
            entrypoint="cyclaw",
            tags=["pre_action_hook", reason_code],
            confidence=confidence,
            cfg=cfg,
        )
    except Exception as exc:  # noqa: BLE001 - derived stream must never fail the caller
        logger.warning("pre_action_hook numbat emit failed: %s", exc)


def run_pre_action_hook(
    provider: str,
    model: str,
    query_hash: str,
    cfg: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the configured pre-action hook and return a verdict.

    Returns one of:
      {"verdict": "allow"}
      {"verdict": "deny", "reason": "..."}

    The hook is disabled by default and when no command is configured, in
    which case this returns allow immediately so existing deployments are
    unaffected.
    """
    block = _hook_cfg(cfg)

    if not _is_literal_true(block.get("enabled", False)):
        return {"verdict": "allow"}

    command = block.get("command")
    if not command:
        return {"verdict": "allow"}

    if not isinstance(command, list) or not all(isinstance(c, str) for c in command):
        logger.warning("pre_action_hook command is not a list of strings; denying")
        return {"verdict": "deny", "reason": "invalid hook command configuration"}

    # Only "enforce" is a legal enabled mode in this PR. "monitor" is a policy
    # flip that still allows the provider call on exit 2; it must not ship
    # until a separate dual-run observation issue is filed (I3).
    fail_mode = block.get("fail_mode", "enforce")
    if fail_mode != "enforce":
        logger.warning(
            "pre_action_hook fail_mode=%r is not supported; using enforce",
            fail_mode,
        )

    timeout = _normalize_timeout(block.get("timeout_sec", DEFAULT_TIMEOUT_SEC))
    emit_verdict = _is_literal_true(block.get("emit_verdict", False))

    payload = {
        "action": "external_llm_call",
        "provider": provider,
        "model": model,
        "query_hash": query_hash,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    try:
        proc = subprocess.run(  # noqa: S603  # nosec B603 - list-form, no shell, operator-configured argv
            command,
            input=payload_bytes,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("pre_action_hook timed out after %ss; denying", timeout)
        if emit_verdict:
            _emit_hook_verdict(
                provider=provider,
                model=model,
                query_hash=query_hash,
                event_type="network.indicator",
                reason_code="hook_timeout",
                confidence="low",
                cfg=cfg,
            )
        return {"verdict": "deny", "reason": f"hook timed out after {timeout}s"}
    except (OSError, ValueError) as exc:
        logger.warning("pre_action_hook failed to run: %s; denying", exc)
        if emit_verdict:
            _emit_hook_verdict(
                provider=provider,
                model=model,
                query_hash=query_hash,
                event_type="network.indicator",
                reason_code="hook_error",
                confidence="low",
                cfg=cfg,
            )
        return {"verdict": "deny", "reason": f"hook execution failed: {exc}"}

    if proc.returncode == 0:
        return {"verdict": "allow"}

    if proc.returncode == 2:
        stderr_text = proc.stderr.decode("utf-8", errors="replace").strip() if proc.stderr else ""
        reason = stderr_text or "hook returned exit code 2 (deny)"
        logger.warning("pre_action_hook denied %s: %s", provider, reason)
        if emit_verdict:
            _emit_hook_verdict(
                provider=provider,
                model=model,
                query_hash=query_hash,
                event_type="permission.denied",
                reason_code="hook_denied",
                confidence="high",
                cfg=cfg,
                decision="denied",
            )
        return {"verdict": "deny", "reason": reason}

    # Any other non-zero exit is treated as a failure and fails closed.
    stdout_text = proc.stdout.decode("utf-8", errors="replace").strip() if proc.stdout else ""
    stderr_text = proc.stderr.decode("utf-8", errors="replace").strip() if proc.stderr else ""
    detail = stderr_text or stdout_text or f"exit code {proc.returncode}"
    logger.warning("pre_action_hook failed for %s: %s; denying", provider, detail)
    if emit_verdict:
        _emit_hook_verdict(
            provider=provider,
            model=model,
            query_hash=query_hash,
            event_type="network.indicator",
            reason_code="hook_failure",
            confidence="low",
            cfg=cfg,
        )
    return {"verdict": "deny", "reason": f"hook failure: {detail}"}
