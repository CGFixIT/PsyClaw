"""Numbat NDJSON dual-write emitter — I6-clean forensic projection.

Maps CyClaw's existing action records (executor check runs, ops_runner
subprocess invocations, real_repo_loop decisions, fsconnect/sqlconnect
operations) into Numbat events written to ``logs/numbat-events.ndjsonl``
alongside the authoritative ``audit.jsonl``.

Never imported by ``gate.py`` / ``graph.py`` / ``mcp_hybrid_server.py`` (I6).
Never raises — degrades to ``logger.warning`` on any failure.
The audit log stays authoritative; this is a projection, not a replacement.

Wire contract (Numbat CLI 0.2.0, which evaluates schema 0.3.0):

* ``schema_version`` is the constant ``\"0.3.0\"``.
* ``source_agent`` must be ``\"unknown\"`` — ``\"cyclaw\"`` is not a legal enum.
* Identify CyClaw via ``tags: [\"cyclaw\", ...]``.
* ``additionalProperties: false`` on the event and the ``endpoint`` object.
* ``rules test`` uses per-type allowlists stricter than the published JSON
  schema (Numbat v0.2.0 ``docs/rules.md`` Event-type fields table). Action
  fields not listed for a type are stripped — e.g. ``command.exec`` may not
  carry ``exit_code`` / ``file_path`` / ``duration_ms``.
* No hash chain — CyClaw hashes query text only (Rule 7).
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import platform
import socket
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from utils.logger import _anchor, _get_config

logger = logging.getLogger("cyclaw.numbat_emitter")

SCHEMA_VERSION = "0.3.0"
RECORD_TYPE = "event"
DEFAULT_SOURCE_AGENT = "unknown"
DEFAULT_SOURCE_TYPE = "hook"
DEFAULT_OUTPUT_PATH = "logs/numbat-events.ndjsonl"
CYCLAW_TAG = "cyclaw"

_EVENT_TYPES = frozenset({
    "session.start",
    "session.end",
    "prompt.user",
    "message.assistant",
    "tool.call",
    "tool.result",
    "command.exec",
    "command.result",
    "file.read",
    "file.write",
    "file.delete",
    "permission.requested",
    "permission.approved",
    "permission.denied",
    "config.agent",
    "config.mcp",
    "network.indicator",
    "message.reasoning",
})
_SOURCE_TYPES = frozenset({"artifact", "hook", "otel"})
_CONFIDENCE = frozenset({"high", "medium", "low"})
_DECISIONS = frozenset({"allowed", "denied", "asked"})
_ACTORS = frozenset({"user", "assistant", "system", "tool"})
_KNOWN_FIELDS = frozenset({
    "schema_version",
    "record_type",
    "run_id",
    "endpoint",
    "case_id",
    "event_id",
    "source_agent",
    "source_type",
    "timestamp",
    "project_path",
    "session_id",
    "actor",
    "event_type",
    "tool_name",
    "command",
    "file_path",
    "decision",
    "tool_call_id",
    "diff_sha256",
    "diff_bytes",
    "exit_code",
    "duration_ms",
    "approval_required",
    "approval_decision",
    "approval_reason",
    "mcp_server",
    "mcp_tool",
    "url",
    "model",
    "model_provider",
    "git_branch",
    "entrypoint",
    "cli_version",
    "sub_agent",
    "content_preview",
    "content_preview_truncated",
    "content",
    "content_bytes",
    "content_truncated",
    "tags",
    "confidence",
    "evidence",
})
# CLI 0.2.0 `rules test` rejects action fields the published JSON schema lists.
# Allowed columns are the v0.2.0 Event-type fields table; everything else in
# `_ACTION_FIELDS` is stripped. Context fields (actor, session, git_branch, …)
# are valid on every type and are not in this set.
_ACTION_FIELDS = frozenset({
    "command",
    "file_path",
    "exit_code",
    "duration_ms",
    "tool_name",
    "tool_call_id",
    "decision",
    "mcp_server",
    "mcp_tool",
    "url",
    "diff_sha256",
    "diff_bytes",
    "approval_required",
    "approval_decision",
    "approval_reason",
})
_NONE: frozenset[str] = frozenset()
_EVENT_TYPE_ALLOWED_ACTION_FIELDS: dict[str, frozenset[str]] = {
    "session.start": _NONE,
    "session.end": _NONE,
    "prompt.user": _NONE,
    "message.assistant": _NONE,
    "message.reasoning": _NONE,
    "config.agent": _NONE,
    "tool.call": frozenset({"tool_name", "tool_call_id", "mcp_server", "mcp_tool", "url", "file_path", "decision"}),
    "tool.result": frozenset({"tool_name", "tool_call_id", "mcp_server", "mcp_tool", "decision"}),
    "command.exec": frozenset({"command", "tool_name", "tool_call_id", "decision"}),
    "command.result": frozenset({"command", "tool_name", "tool_call_id", "exit_code", "duration_ms", "decision"}),
    "file.read": frozenset({"file_path", "tool_name", "tool_call_id", "decision"}),
    "file.write": frozenset({"file_path", "tool_name", "tool_call_id", "decision", "diff_sha256", "diff_bytes"}),
    "file.delete": frozenset({"file_path", "tool_name", "tool_call_id", "diff_sha256", "diff_bytes"}),
    "permission.requested": frozenset({
        "tool_name", "tool_call_id", "decision",
        "approval_required", "approval_decision", "approval_reason",
    }),
    "permission.approved": frozenset({
        "tool_name", "tool_call_id", "decision",
        "approval_required", "approval_decision", "approval_reason",
    }),
    "permission.denied": frozenset({
        "tool_name", "tool_call_id", "decision",
        "approval_required", "approval_decision", "approval_reason",
    }),
    "config.mcp": frozenset({"mcp_server", "mcp_tool"}),
    "network.indicator": frozenset({"url", "tool_name", "tool_call_id", "mcp_server", "mcp_tool", "decision"}),
}
if set(_EVENT_TYPE_ALLOWED_ACTION_FIELDS) != _EVENT_TYPES:
    raise RuntimeError("Numbat action-field map does not cover every event_type")
_EVENT_TYPE_FORBIDDEN_FIELDS: dict[str, frozenset[str]] = {
    event_type: _ACTION_FIELDS - allowed
    for event_type, allowed in _EVENT_TYPE_ALLOWED_ACTION_FIELDS.items()
}
_SENSITIVE_ARGV_PREFIXES = (
    "--reason=",
    "--instruction=",
    "--commit-message=",
    "--body=",
    "--sql=",
    "--name=",
    "--desc=",
    "--plan=",
)

_WRITE_LOCK = threading.Lock()
_PROCESS_RUN_ID = uuid.uuid4().hex


def redact_argv_for_numbat(argv: list[str]) -> str:
    """Join argv for a ``command`` field, redacting free-text option values.

    Numbat rules evaluate the ``command`` string. Operator-supplied
    ``--reason=`` / ``--instruction=`` values must not land in the projection.
    """
    redacted: list[str] = []
    skip_next = False
    sensitive_flags = {prefix.rstrip("=") for prefix in _SENSITIVE_ARGV_PREFIXES}
    for token in argv:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        matched_prefix = next((p for p in _SENSITIVE_ARGV_PREFIXES if token.startswith(p)), None)
        if matched_prefix:
            redacted.append(f"{matched_prefix}<redacted>")
            continue
        if token in sensitive_flags:
            redacted.append(token)
            skip_next = True
            continue
        redacted.append(token)
    return " ".join(redacted)


def posix_path(path: str | Path | None) -> str | None:
    """Normalize a path to ``/`` separators as Numbat's schema requires."""
    if path is None:
        return None
    text = str(path).strip()
    if not text:
        return None
    return text.replace("\\", "/")


def _numbat_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    block = (cfg or {}).get("numbat") or {}
    return block if isinstance(block, dict) else {}


def _is_enabled(cfg: dict[str, Any] | None) -> bool:
    block = _numbat_cfg(cfg)
    return bool(block.get("enabled", True))


def _output_path(cfg: dict[str, Any] | None) -> Path:
    block = _numbat_cfg(cfg)
    raw = block.get("output_path") or DEFAULT_OUTPUT_PATH
    return _anchor(str(raw))


def _source_agent(cfg: dict[str, Any] | None) -> str:
    block = _numbat_cfg(cfg)
    value = str(block.get("source_agent") or DEFAULT_SOURCE_AGENT)
    return value if value == DEFAULT_SOURCE_AGENT else DEFAULT_SOURCE_AGENT


def _source_type(cfg: dict[str, Any] | None) -> str:
    block = _numbat_cfg(cfg)
    value = str(block.get("source_type") or DEFAULT_SOURCE_TYPE)
    return value if value in _SOURCE_TYPES else DEFAULT_SOURCE_TYPE


def build_endpoint() -> dict[str, str]:
    """Host metadata for the required ``endpoint`` object."""
    system = platform.system().lower()
    os_name = {"darwin": "darwin", "linux": "linux", "windows": "windows"}.get(system, system or "unknown")
    machine = platform.machine().lower() or "unknown"
    if machine in {"x86_64", "amd64"}:
        arch = "amd64"
    elif machine in {"aarch64", "arm64"}:
        arch = "arm64"
    else:
        arch = machine
    try:
        username = getpass.getuser() or "unknown"
    except (OSError, KeyError):
        username = "unknown"
    try:
        uid = str(os.getuid())
    except AttributeError:
        uid = "N/A"
    endpoint = {
        "hostname": socket.gethostname() or "unknown",
        "os": os_name,
        "arch": arch,
        "username": username,
        "uid": uid,
    }
    device_id = os.environ.get("NUMBAT_DEVICE_ID", "").strip()
    if device_id:
        endpoint["device_id"] = device_id
    return endpoint


def _unique_tags(tags: list[str] | None) -> list[str]:
    seen: list[str] = []
    for tag in [CYCLAW_TAG, *(tags or ())]:
        cleaned = str(tag).strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen


def build_event(
    event_type: str,
    *,
    run_id: str | None = None,
    command: str | None = None,
    file_path: str | None = None,
    exit_code: int | None = None,
    duration_ms: int | None = None,
    tool_name: str | None = None,
    decision: str | None = None,
    approval_required: bool | None = None,
    approval_decision: str | None = None,
    approval_reason: str | None = None,
    git_branch: str | None = None,
    tags: list[str] | None = None,
    confidence: str = "high",
    actor: str | None = None,
    session_id: str | None = None,
    project_path: str | None = None,
    artifact_type: str = "audit_log",
    model: str | None = None,
    model_provider: str | None = None,
    entrypoint: str | None = None,
    content_preview: str | None = None,
    mcp_server: str | None = None,
    mcp_tool: str | None = None,
    url: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one CLI-legal Numbat event record (schema 0.3.0)."""
    if event_type not in _EVENT_TYPES:
        raise ValueError(f"unsupported event_type: {event_type!r}")
    if confidence not in _CONFIDENCE:
        confidence = "high"
    if decision is not None and decision not in _DECISIONS:
        decision = None
    if approval_decision is not None and approval_decision not in _DECISIONS:
        approval_decision = None
    if actor is not None and actor not in _ACTORS:
        actor = None

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "record_type": RECORD_TYPE,
        "run_id": run_id or _PROCESS_RUN_ID,
        "endpoint": build_endpoint(),
        "event_id": uuid.uuid4().hex,
        "source_agent": _source_agent(cfg),
        "source_type": _source_type(cfg),
        "timestamp": datetime.now(UTC).isoformat(),
        "event_type": event_type,
        "confidence": confidence,
        "tags": _unique_tags(tags),
        "evidence": {"artifact_type": artifact_type, "local_path": str(_output_path(cfg))},
    }
    optional = {
        "command": command,
        "file_path": posix_path(file_path),
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "tool_name": tool_name,
        "decision": decision,
        "approval_required": approval_required,
        "approval_decision": approval_decision,
        "approval_reason": approval_reason,
        "git_branch": git_branch,
        "actor": actor,
        "session_id": session_id,
        "project_path": posix_path(project_path),
        # Context fields (valid on every event_type; never in _ACTION_FIELDS):
        "model": model,
        "model_provider": model_provider,
        "entrypoint": entrypoint,
        "content_preview": content_preview,
        # Action fields (stripped per event-type allowlist below):
        "mcp_server": mcp_server,
        "mcp_tool": mcp_tool,
        "url": url,
    }
    for key, value in optional.items():
        if value is not None:
            record[key] = value
    for key in _EVENT_TYPE_FORBIDDEN_FIELDS.get(event_type, ()):
        record.pop(key, None)
    extras = set(record) - _KNOWN_FIELDS
    if extras:
        raise ValueError(f"schema extras rejected: {sorted(extras)}")
    return record


def write_ndjson(record: dict[str, Any], path: Path) -> None:
    """Append one JSON line. Caller holds no lock; this function does."""
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()


def emit_numbat_event(
    event_type: str,
    *,
    command: str | None = None,
    file_path: str | None = None,
    exit_code: int | None = None,
    duration_ms: int | None = None,
    tool_name: str | None = None,
    decision: str | None = None,
    approval_required: bool | None = None,
    approval_decision: str | None = None,
    approval_reason: str | None = None,
    git_branch: str | None = None,
    tags: list[str] | None = None,
    confidence: str = "high",
    actor: str | None = None,
    session_id: str | None = None,
    project_path: str | None = None,
    artifact_type: str = "audit_log",
    run_id: str | None = None,
    model: str | None = None,
    model_provider: str | None = None,
    entrypoint: str | None = None,
    content_preview: str | None = None,
    mcp_server: str | None = None,
    mcp_tool: str | None = None,
    url: str | None = None,
    config_path: str = "config.yaml",
    cfg: dict[str, Any] | None = None,
) -> None:
    """Emit one Numbat event. Never raises."""
    try:
        if cfg is None:
            cfg = _get_config(config_path)
        if not _is_enabled(cfg):
            return
        record = build_event(
            event_type,
            run_id=run_id,
            command=command,
            file_path=file_path,
            exit_code=exit_code,
            duration_ms=duration_ms,
            tool_name=tool_name,
            decision=decision,
            approval_required=approval_required,
            approval_decision=approval_decision,
            approval_reason=approval_reason,
            git_branch=git_branch,
            tags=tags,
            confidence=confidence,
            actor=actor,
            session_id=session_id,
            project_path=project_path,
            artifact_type=artifact_type,
            model=model,
            model_provider=model_provider,
            entrypoint=entrypoint,
            content_preview=content_preview,
            mcp_server=mcp_server,
            mcp_tool=mcp_tool,
            url=url,
            cfg=cfg,
        )
        write_ndjson(record, _output_path(cfg))
    except Exception as exc:  # noqa: BLE001 - forensic projection must never fail the caller
        logger.warning("numbat emit failed for %s: %s", event_type, exc)


def emit_numbat_command(
    command: str,
    *,
    exit_code: int | None = None,
    duration_ms: int | None = None,
    tool_name: str | None = None,
    tags: list[str] | None = None,
    actor: str | None = None,
    git_branch: str | None = None,
    artifact_type: str = "audit_log",
    run_id: str | None = None,
    config_path: str = "config.yaml",
    cfg: dict[str, Any] | None = None,
) -> None:
    """Emit ``command.exec`` plus ``command.result`` when an outcome exists.

    Numbat's CLI forbids ``exit_code`` / ``duration_ms`` on ``command.exec``.
    Never raises.
    """
    shared: dict[str, Any] = {
        "command": command,
        "tool_name": tool_name,
        "tags": tags,
        "actor": actor,
        "git_branch": git_branch,
        "artifact_type": artifact_type,
        "run_id": run_id,
        "config_path": config_path,
        "cfg": cfg,
    }
    emit_numbat_event("command.exec", **shared)
    if exit_code is not None or duration_ms is not None:
        emit_numbat_event("command.result", exit_code=exit_code, duration_ms=duration_ms, **shared)


# ---------------------------------------------------------------------------
# Mainline audit-trail projection (utils/logger.audit_log -> Numbat NDJSON)
# ---------------------------------------------------------------------------
#
# The emitters above cover the out-of-band ACTION plane (agentic/ops). The
# request path's append-only audit trail (rag_query, soul governance, MCP,
# guardrails) also projects here so ONE ndjson file describes CyClaw activity
# end to end. audit.jsonl stays authoritative; this remains a derived,
# fail-soft stream. Imported lazily by utils/logger.py at call time only, so
# gate.py/graph.py gain no import-time surface.

AUDIT_ARTIFACT_TYPE = "cyclaw_audit_jsonl"
_AUDIT_ENTRYPOINT = "cyclaw"
_AUDIT_PREVIEW_CAP = 2000

# CyClaw audit ``event`` name -> (numbat event_type, actor, confidence).
# Unknown events fall back to ("tool.call", "tool", "low") -- an audit line
# is never dropped, just downgraded in confidence.
_AUDIT_EVENT_MAP: dict[str, tuple[str, str, str]] = {
    "rag_query": ("prompt.user", "user", "high"),
    "user_gate_pause": ("permission.requested", "system", "high"),
    "prompt_injection_blocked": ("permission.denied", "system", "high"),
    "rate_limit_exceeded": ("permission.denied", "system", "high"),
    "soul_drift_detected": ("config.agent", "system", "high"),
    "soul_evolution_applied": ("config.agent", "system", "high"),
    "soul_evolution_failed": ("config.agent", "system", "high"),
    "soul_evolution_proposed": ("config.agent", "system", "high"),
    "soul_apply_injection_blocked": ("permission.denied", "system", "high"),
    "soul_apply_rejected": ("permission.denied", "system", "high"),
    "soul_read": ("config.agent", "system", "medium"),
    "soul_restored_from_backup": ("config.agent", "system", "medium"),
    "soul_restore_failed": ("config.agent", "system", "medium"),
    "soul_restore_scan_flags": ("config.agent", "system", "medium"),
    "mcp_rag_query": ("tool.call", "tool", "high"),
    "mcp_rag_error": ("tool.result", "tool", "high"),
    "retrieval_degraded": ("tool.result", "tool", "high"),
    "mcp_manifest_drift": ("config.mcp", "system", "medium"),
    "grok_prompt_truncated": ("message.assistant", "assistant", "high"),
    "claude_prompt_truncated": ("message.assistant", "assistant", "high"),
    "graph_error": ("tool.result", "tool", "medium"),
    "graph_timeout": ("tool.result", "tool", "medium"),
    "skipped_sources": ("tool.result", "tool", "medium"),
}

# model_used role -> model_provider. The role vocabulary is frozen by
# metrics.py, so this mapping is stable.
_AUDIT_MODEL_PROVIDERS = {
    "grok": "xai",
    "claude": "anthropic",
}


def _audit_content_preview(record: dict[str, Any]) -> str | None:
    """Pack CyClaw-only forensics into one JSON string under the length cap.

    ``record`` is the ALREADY redacted/hashed legacy audit record, so raw
    query text cannot leak through here by construction. Numbat's
    additionalProperties:false means these fields cannot be top-level.
    """
    preview: dict[str, Any] = {}
    for key, value in record.items():
        if key == "timestamp" or value is None:
            continue
        # Rename the legacy "event" tag so the preview is self-describing.
        preview["cyclaw_event" if key == "event" else key] = value
    if not preview:
        return None
    text = json.dumps(preview, default=str)
    if len(text) <= _AUDIT_PREVIEW_CAP:
        return text
    for bulky in ("sources", "errors", "details"):
        if bulky in preview:
            del preview[bulky]
            text = json.dumps(preview, default=str)
            if len(text) <= _AUDIT_PREVIEW_CAP:
                return text
    return text[:_AUDIT_PREVIEW_CAP]


def project_audit_record(
    record: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
) -> None:
    """Project one redacted legacy audit record into the Numbat NDJSON stream.

    Called by utils/logger.audit_log() AFTER the legacy write. Never raises,
    never writes the legacy file, and returns silently when numbat.enabled is
    false or the record carries no usable event identity.
    """
    try:
        if not _is_enabled(cfg):
            return
        event_name = record.get("event")
        if not isinstance(event_name, str) or not event_name:
            return
        event_type, actor, confidence = _AUDIT_EVENT_MAP.get(
            event_name, ("tool.call", "tool", "low"),
        )
        tags = ["cyclaw", event_name]
        if record.get("guardrail_blocked"):
            tags.append("guardrail_blocked")

        role = record.get("model_used")
        model_provider = None
        if isinstance(role, str) and role:
            model_provider = _AUDIT_MODEL_PROVIDERS.get(
                role, (cfg or {}).get("models", {}).get("local_llm", {}).get("provider", "ollama"),
            )
        model = record.get("llm_model")
        if not isinstance(model, str) or not model:
            model = None

        # decision: permission.* types carry it natively; for prompt.user the
        # CLI allowlist strips it, so the guardrail verdict rides in
        # content_preview instead (guardrail_blocked stays in the preview).
        decision = "denied" if event_type == "permission.denied" else None
        if event_type == "permission.requested":
            decision = "asked"

        mcp_server = mcp_tool = tool_name = None
        if event_name in ("mcp_rag_query", "mcp_rag_error"):
            mcp_server, mcp_tool, tool_name = "cyclaw-hybrid-rag", "hybrid_search", "hybrid_search"
        elif event_name == "retrieval_degraded":
            tool_name = "hybrid_search"
        elif confidence in ("medium", "low"):
            tool_name = event_name

        emit_numbat_event(
            event_type,
            actor=actor,
            confidence=confidence,
            decision=decision,
            tool_name=tool_name,
            mcp_server=mcp_server,
            mcp_tool=mcp_tool,
            model=model,
            model_provider=model_provider,
            entrypoint=_AUDIT_ENTRYPOINT,
            content_preview=_audit_content_preview(record),
            tags=tags,
            artifact_type=AUDIT_ARTIFACT_TYPE,
            cfg=cfg,
        )
    except Exception as exc:  # noqa: BLE001 - projection must never break audit
        logger.warning("numbat audit projection failed for %r: %s",
                       record.get("event"), exc)


__all__ = [
    "AUDIT_ARTIFACT_TYPE",
    "SCHEMA_VERSION",
    "build_endpoint",
    "build_event",
    "emit_numbat_command",
    "emit_numbat_event",
    "posix_path",
    "project_audit_record",
    "redact_argv_for_numbat",
]
