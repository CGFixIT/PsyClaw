"""Synchronous pre-action hook runner for external LLM fallbacks.

CyClaw invokes the configured command before any call to Grok or Claude.
The command receives a JSON payload on stdin describing the proposed action
(provider, model, query_hash) and signals its decision via exit code:

  * exit 0  -> allow (proceed to the provider)
  * exit 2  -> deny (route to audit_logger instead)
  * any other exit, crash, or timeout -> fail-closed deny + audit

This module is intentionally isolated from the request path's optional layers:
it does not import agentic, sync, guardrails, harness, telegram, or
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

    if not block.get("enabled", False):
        return {"verdict": "allow"}

    command = block.get("command")
    if not command:
        return {"verdict": "allow"}

    if not isinstance(command, list) or not all(isinstance(c, str) for c in command):
        logger.warning("pre_action_hook command is not a list of strings; denying")
        return {"verdict": "deny", "reason": "invalid hook command configuration"}

    timeout = _normalize_timeout(block.get("timeout_sec", DEFAULT_TIMEOUT_SEC))

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
        return {"verdict": "deny", "reason": f"hook timed out after {timeout}s"}
    except (OSError, ValueError) as exc:
        logger.warning("pre_action_hook failed to run: %s; denying", exc)
        return {"verdict": "deny", "reason": f"hook execution failed: {exc}"}

    if proc.returncode == 0:
        return {"verdict": "allow"}

    if proc.returncode == 2:
        stderr_text = proc.stderr.decode("utf-8", errors="replace").strip() if proc.stderr else ""
        reason = stderr_text or "hook returned exit code 2 (deny)"
        logger.warning("pre_action_hook denied %s: %s", provider, reason)
        return {"verdict": "deny", "reason": reason}

    # Any other non-zero exit is treated as a failure and fails closed.
    stdout_text = proc.stdout.decode("utf-8", errors="replace").strip() if proc.stdout else ""
    stderr_text = proc.stderr.decode("utf-8", errors="replace").strip() if proc.stderr else ""
    detail = stderr_text or stdout_text or f"exit code {proc.returncode}"
    logger.warning("pre_action_hook failed for %s: %s; denying", provider, detail)
    return {"verdict": "deny", "reason": f"hook failure: {detail}"}
