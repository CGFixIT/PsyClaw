"""Numbat NDJSON dual-write emitter — I6-clean forensic projection.

Two planes feed one stream, written to ``logs/numbat-events.ndjsonl``
alongside the authoritative ``audit.jsonl``:

* ACTION plane — CyClaw's existing action records (executor check runs,
  ops_runner subprocess invocations, real_repo_loop decisions,
  fsconnect/sqlconnect operations) emitted directly at the call site via
  ``emit_numbat_event`` / ``emit_numbat_command``.
* MAINLINE plane — every redacted ``audit.jsonl`` record, projected by
  ``project_audit_record`` (PR #1033). Events whose own code path already
  emitted directly are skipped here; see ``_AUDIT_ACTION_PLANE_EVENTS``.

Never imported at module scope by ``gate.py`` / ``graph.py`` /
``mcp_hybrid_server.py`` (I6). The mainline projection means this module is
lazy-imported and executed *inside* those processes on every ``audit_log``
call — utils/ is shared, so that is allowed, but it is why every entry point
here must stay fail-soft and stdlib-only.
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

import atexit
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
from typing import Any, TextIO

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


# The mainline plane projects EVERY audit record into the stream, so without a
# cap the file grows without bound on a busy instance. 50 MiB, single .1
# generation -- forensics that need more history archive the .1 themselves.
DEFAULT_MAX_BYTES = 52428800


def _max_bytes(cfg: dict[str, Any] | None) -> int:
    """numbat.max_bytes: rollover threshold; 0 (or negative/garbage) disables."""
    block = _numbat_cfg(cfg)
    try:
        value = int(block.get("max_bytes", DEFAULT_MAX_BYTES))
    except (TypeError, ValueError):
        return DEFAULT_MAX_BYTES
    return value if value > 0 else 0


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


# write_ndjson previously opened, wrote, and closed this file on every single
# call -- and the mainline plane reaches it on every audit_log() call, i.e.
# every /query. Mirrors utils/logger.py's _AUDIT_HANDLES: cache one append-mode
# handle per resolved path and reuse it; still flush() after every write so a
# reader (including a test that doesn't close explicitly) observes each event
# immediately -- only the repeated open/close is eliminated.
_NUMBAT_HANDLES: dict[str, TextIO] = {}


def _handle_still_points_at(handle: TextIO, path: Path) -> bool:
    """True when the cached handle still refers to the file living at ``path``.

    The cache is keyed on the path STRING, but a rename moves the name off the
    inode the handle holds. Another process rolls this same stream over -- the
    action plane runs in ops_runner children while a long-lived gate.py holds
    its handle open -- so after that child's os.replace, an unchecked cached
    handle keeps appending into the .1 generation, and the NEXT rollover
    deletes the whole backlog. Verified: without this check the live file stops
    existing and every later event lands in .1.

    This is the check logging.handlers.WatchedFileHandler makes, for exactly
    this reason. Fail-soft: any stat error answers "not current", which costs a
    reopen and never an exception (this module must never raise).
    """
    try:
        return os.fstat(handle.fileno()).st_ino == path.stat().st_ino
    except OSError:
        return False


def _numbat_handle(path: Path) -> TextIO:
    """Return the cached append-mode handle for path, opening it if needed.

    Caller must hold _WRITE_LOCK.
    """
    key = str(path)
    handle = _NUMBAT_HANDLES.get(key)
    if handle is not None and not handle.closed and not _handle_still_points_at(handle, path):
        # Someone rolled the stream over underneath us; drop the stale inode.
        try:
            handle.close()
        except OSError:
            # Losing the fd is acceptable -- the reopen below is what matters,
            # and this module's contract forbids raising from the write path.
            pass
        _NUMBAT_HANDLES.pop(key, None)
        handle = None
    if handle is None or handle.closed:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a", encoding="utf-8")  # noqa: SIM115  # codeql[py/file-not-closed] closed via close_numbat_handles() at exit
        _NUMBAT_HANDLES[key] = handle
    return handle


def close_numbat_handles() -> None:
    """Flush and close all cached Numbat NDJSON handles.

    Called automatically at process exit; also useful for tests that need to
    release file descriptors before deleting their tmp_path output files.
    """
    with _WRITE_LOCK:
        for handle in _NUMBAT_HANDLES.values():
            try:
                handle.close()
            except OSError:
                # Best-effort at process-exit/test-teardown -- _NUMBAT_HANDLES.clear()
                # below still drops our reference so a future write_ndjson() reopens.
                pass
        _NUMBAT_HANDLES.clear()


atexit.register(close_numbat_handles)


def _rollover_if_needed(path: Path, max_bytes: int) -> None:
    """Single-generation size rollover: rename to ``<name>.1``, start fresh.

    Caller must hold _WRITE_LOCK. The cached handle is closed and dropped
    BEFORE the rename -- on POSIX an open handle would keep appending to the
    renamed file, and on Windows renaming an open file fails outright. Every
    branch is fail-soft (the module's contract: it runs inside gate/graph on
    every audit_log and must never raise); on any OSError the stream simply
    keeps appending to the oversized file rather than dropping events.

    The size is re-stat'd per write rather than tracked in a counter on
    purpose: _WRITE_LOCK is per-process, but the action plane (agentic /
    ops_runner children) writes this same path from OTHER processes, and only
    the filesystem sees all of them. Two processes crossing the threshold
    together can still both rename -- the loser's events land in the .1
    generation instead of the fresh file. That is benign for a derived
    forensic stream (audit.jsonl stays authoritative) and is the reason this
    is a single-generation policy rather than a numbered-rotation one.
    """
    try:
        if path.stat().st_size < max_bytes:
            return
    except OSError as exc:
        # Rollover is now effectively off for this path (an unreadable parent, a
        # too-long name). Say so once at debug rather than growing unbounded in
        # total silence -- every other fail-soft path in this module logs.
        logger.debug("numbat rollover size check failed (%s); cap not enforced", type(exc).__name__)
        return
    handle = _NUMBAT_HANDLES.pop(str(path), None)
    if handle is not None:
        try:
            handle.close()
        except OSError:
            # Best-effort, same as close_numbat_handles(): the pop() above already
            # dropped our reference, so the next write reopens the path regardless
            # of whether this close succeeded. Raising here would break the
            # never-raise contract for a handle we are discarding anyway.
            pass
    try:
        os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        # Rename refused (read-only mount, a .1 held open by a reader on Windows,
        # a vanished parent). Falling through leaves the oversized file in place
        # and the next write reopens it: the stream keeps flowing, which is the
        # right trade for a derived forensic log -- audit.jsonl is authoritative.
        pass


def write_ndjson(record: dict[str, Any], path: Path, *, max_bytes: int = 0) -> None:
    """Append one JSON line. Caller holds no lock; this function does.

    ``max_bytes`` > 0 arms the size rollover above, checked before the write
    so no single append is split across generations.

    It defaults to 0 (uncapped) even though the module ships DEFAULT_MAX_BYTES:
    config.yaml is the single source of truth for tunables, and only
    emit_numbat_event() holds the cfg to read numbat.max_bytes from. A caller
    handed just a path therefore gets the old unbounded behaviour rather than a
    surprise rename it never configured. The shipped path always passes the
    configured value -- see the emit_numbat_event() call site.
    """
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
    with _WRITE_LOCK:
        if max_bytes:
            _rollover_if_needed(path, max_bytes)
        handle = _numbat_handle(path)
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
        write_ndjson(record, _output_path(cfg), max_bytes=_max_bytes(cfg))
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
# Audit events written by the out-of-band ACTION plane already have direct
# Numbat emits (e.g. agentic/executor/runner.py calls emit_numbat_command).
# Projecting them again from the mainline audit trail would double-write and
# reorder the NDJSON stream, so project_audit_record skips them.
#
# Membership rule: the event's own code path calls audit_log(...) AND an
# emit_numbat_* helper for the same action, so the stream already has a
# record. Each entry below is paired with the emit site that makes it a
# duplicate -- keep them in step when either side moves.
_AUDIT_ACTION_PLANE_EVENTS: frozenset[str] = frozenset({
    # agentic/executor/runner.py -- emit_numbat_command, same loop iteration
    "agentic_executor_check_result",
    # agentic/fsconnect/client.py::_audit -- emit_numbat_event("file.read")
    "fsconnect_read",
    # agentic/sqlconnect/client.py::_audit_sql -- emit_numbat_event("command.exec")
    "sqlconnect_read",
    # agentic/real_repo_loop.py -- emit_numbat_event("permission.approved"/"denied")
    "agentic_real_repo_change_decided",
    # agentic/real_repo_loop.py -- emit_numbat_event("command.exec", "git commit")
    "agentic_real_repo_change_approved",
})

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
        if event_name in _AUDIT_ACTION_PLANE_EVENTS:
            # Action-plane events are already emitted directly to Numbat.
            # Do not project the legacy audit record a second time.
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
