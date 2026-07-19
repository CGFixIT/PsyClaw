"""utils/ops_runner.py – subprocess shim for the out-of-band ``sync`` / ``agentic`` CLIs.

The FastAPI gateway exposes ``POST /ops/sync`` and ``POST /ops/agentic`` so the
browser Soul Console can drive the two out-of-band subsystems. A browser cannot
spawn a subprocess, so the gateway must — but the gateway must NOT *import* those
packages, because architectural isolation of ``sync/`` and ``agentic/`` from
``gate.py`` / ``graph.py`` / ``mcp_hybrid_server.py`` is a hard CyClaw invariant.

This module is that boundary, and nothing more:

* It NEVER imports ``sync`` or ``agentic``. It only builds an argv list and runs
  it with ``subprocess.run([...])`` (list form, no shell) as
  ``python -m sync.cli`` / ``python -m agentic.cli``.
* It accepts only a whitelisted set of actions per subsystem. An unknown action
  raises :class:`OpsError`, which the route maps to HTTP 400 — a caller can never
  smuggle an arbitrary subcommand or flag through.
* User-supplied skill bodies are written to a ``NamedTemporaryFile`` and passed
  via ``--body-file``, never interpolated into argv.

Exit codes are translated to operator-meaningful labels (see the per-subsystem
maps below) so the UI can render failure states — a tripped ``--max-delete`` /
``--max-transfer`` safety fuse, an env/config error, or a refused write — without
re-deriving the meaning of each code.

Exit-code contract (mirrors the docstrings in ``sync/cli.py`` / ``agentic/cli.py``):

    sync:     0 ok · 10 ok+reindex-needed · 1 safety-abort · 2 failed · 3 env/config
    agentic:  0 ok · 2 failed · 3 env/config · 4 write-refused
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - list-form only, no shell, fixed interpreter + whitelisted argv
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.logger import _get_config, redact_sensitive

# Repo root = parent of utils/. The CLIs run as ``python -m sync.cli`` /
# ``agentic.cli``; running with cwd=repo-root puts the ``sync`` / ``agentic``
# packages on the import path without mutating PYTHONPATH for the gateway process.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _REPO_ROOT / "config.yaml"
# Default wall-clock for short ops (status/test/agentic/fsconnect/sqlconnect).
# The full Dropbox sync path uses config sync.sync_timeout_sec instead — see
# _sync_timeout_sec() — so POST /ops/sync does not kill rclone mid-transfer.
_TIMEOUT_SEC = 120

# action whitelists — the ONLY subcommands a caller may reach.
_SYNC_ACTIONS = frozenset({"status", "test", "sync", "schedule", "unschedule"})
_AGENTIC_ACTIONS = frozenset({"status", "test", "context", "propose-skill", "apply-skill"})
# agentic subcommands that emit JSON on stdout (vs. human text).
_AGENTIC_JSON_ACTIONS = frozenset({"context", "propose-skill", "apply-skill"})

# fsconnect read-only CLI subcommands exposed via /ops/fsconnect.
_FSCONNECT_ACTIONS = frozenset({"status", "test", "list", "read", "stat", "grep", "glob"})
_FSCONNECT_JSON_ACTIONS = frozenset({"list", "read", "stat", "grep", "glob"})

# sqlconnect read-only CLI subcommands exposed via /ops/sqlconnect.
_SQLCONNECT_ACTIONS = frozenset({"status", "test", "schema", "query"})
_SQLCONNECT_JSON_ACTIONS = frozenset({"schema", "query"})

# exit code -> (ok, label)
_SYNC_LABELS: dict[int, tuple[bool, str]] = {
    0: (True, "ok"),
    10: (True, "ok_reindex_needed"),
    1: (False, "safety_abort"),
    2: (False, "failed"),
    3: (False, "env_config"),
}
_AGENTIC_LABELS: dict[int, tuple[bool, str]] = {
    0: (True, "ok"),
    2: (False, "failed"),
    3: (False, "env_config"),
    4: (False, "write_refused"),
}
_FSCONNECT_LABELS: dict[int, tuple[bool, str]] = {
    0: (True, "ok"),
    2: (False, "failed"),
    3: (False, "env_config"),
    4: (False, "write_refused"),
}
_SQLCONNECT_LABELS: dict[int, tuple[bool, str]] = {
    0: (True, "ok"),
    2: (False, "failed"),
    3: (False, "env_config"),
}


class OpsError(ValueError):
    """A disallowed action or malformed request. The route maps this to HTTP 400."""


@dataclass
class OpsResult:
    """Normalized result of one CLI invocation, JSON-serializable for the route."""

    subsystem: str
    action: str
    exit_code: int
    ok: bool
    label: str
    stdout: str
    stderr: str
    parsed: Any = None

    def to_dict(self) -> dict[str, Any]:
        cfg = _get_config(str(_CONFIG_PATH))
        return {
            "subsystem": self.subsystem,
            "action": self.action,
            "exit_code": self.exit_code,
            "ok": self.ok,
            "label": self.label,
            "stdout": _redact_ops_value(self.stdout, cfg),
            "stderr": _redact_ops_value(self.stderr, cfg),
            "parsed": _redact_ops_value(self.parsed, cfg),
        }


def _redact_ops_value(value: Any, cfg: dict) -> Any:
    """Redact subprocess output before it reaches the browser ops console."""
    if isinstance(value, str):
        return redact_sensitive(value, cfg)
    if isinstance(value, dict):
        return {k: _redact_ops_value(v, cfg) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_ops_value(v, cfg) for v in value]
    return value


def _sync_timeout_sec() -> int:
    """Wall-clock budget for ``sync.cli sync`` launched via the ops shim.

    Aligns with ``sync.sync_timeout_sec`` (default 3600) so console-driven
    ``POST /ops/sync`` does not abort a legitimate long rclone transfer that
    the CLI path would complete. Adds a small overhead for Python startup and
    post-rclone bookkeeping. Config value ``0`` means unbounded in the CLI;
    the ops path still needs a finite ceiling (falls back to 3600).
    """
    try:
        cfg = _get_config(str(_CONFIG_PATH))
        sec = int((cfg.get("sync") or {}).get("sync_timeout_sec", 3600))
    except (OSError, TypeError, ValueError, KeyError):
        sec = 3600
    if sec <= 0:
        sec = 3600
    return sec + 60


def _run(argv: list[str], *, timeout_sec: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run a fully-formed, whitelisted argv list. No shell, fixed interpreter."""
    return subprocess.run(  # noqa: S603  # nosec B603 - list-form, no shell, fixed interpreter + whitelisted argv
        argv,
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SEC if timeout_sec is None else timeout_sec,
        check=False,
    )


def _maybe_json(text: str) -> Any:
    """Parse JSON if the text is JSON, else return None (status/text output)."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _write_body(body: str) -> str:
    """Persist a skill body to a temp file so it is passed via --body-file, never argv."""
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix="cyclaw_skill_", delete=False, encoding="utf-8"
    )
    try:
        handle.write(body)
    finally:
        handle.close()
    return handle.name


def run_sync_op(action: str, *, dry_run: bool = False) -> OpsResult:
    """Invoke ``python -m sync.cli <action>`` and normalize the result.

    Only ``dry_run`` is honored, and only for the ``sync`` action (it maps to
    ``--dry-run``). Every other action takes no caller-controlled arguments, so
    there is no surface for argument injection.
    """
    if action not in _SYNC_ACTIONS:
        raise OpsError(f"Unknown sync action: {action!r}")

    argv = [sys.executable, "-m", "sync.cli", "--config", str(_CONFIG_PATH), action]
    if action == "sync" and dry_run:
        argv.append("--dry-run")

    # status/test/schedule stay on the short default; only the full transfer
    # needs the config-aligned ceiling (rclone can run for up to an hour).
    timeout = _sync_timeout_sec() if action == "sync" else _TIMEOUT_SEC
    proc = _run(argv, timeout_sec=timeout)
    ok, label = _SYNC_LABELS.get(proc.returncode, (False, "unknown"))
    return OpsResult("sync", action, proc.returncode, ok, label, proc.stdout, proc.stderr)


def run_agentic_op(
    action: str,
    *,
    pr: int | None = None,
    issue: int | None = None,
    no_diff: bool = False,
    name: str | None = None,
    desc: str | None = None,
    body: str | None = None,
    reason: str | None = None,
    confirm: bool = False,
) -> OpsResult:
    """Invoke ``python -m agentic.cli <action>`` and normalize the result.

    ``context`` takes an optional ``--pr`` / ``--issue`` selector (defaults to
    ``--repo``). ``propose-skill`` / ``apply-skill`` require ``name`` + ``desc``;
    ``apply-skill`` additionally requires a non-empty ``reason`` (the registry
    governance gate) and only adds ``--confirm`` when the caller set it — calling
    apply without confirm reaches the CLI's own refusal path (exit 4), which is
    surfaced verbatim rather than masked.

    Validation raises happen before the subprocess launch. All ``proc`` usage
    lives INSIDE the try so there is no post-``finally`` reference to an unbound
    name: if ``_run`` raises (e.g. ``subprocess.TimeoutExpired``), the ``finally``
    cleans up the temp body-file and the exception propagates before any result is
    read. The body-file is unlinked on every exit path (return or raise).
    """
    if action not in _AGENTIC_ACTIONS:
        raise OpsError(f"Unknown agentic action: {action!r}")
    if action in {"propose-skill", "apply-skill"} and (not name or not desc):
        raise OpsError(f"{action} requires both name and desc")
    if action == "apply-skill" and not (reason and reason.strip()):
        raise OpsError("apply-skill requires a non-empty reason")

    argv = [sys.executable, "-m", "agentic.cli", "--config", str(_CONFIG_PATH), action]
    body_file: str | None = None
    try:
        if action == "context":
            if pr is not None:
                argv += ["--pr", str(pr)]
            elif issue is not None:
                argv += ["--issue", str(issue)]
            else:
                argv.append("--repo")
            if no_diff:
                argv.append("--no-diff")
        elif action in {"propose-skill", "apply-skill"}:
            # name/desc validated above; both are required, so they are non-None here.
            # Use the --opt=value form (not two argv elements) so a value that
            # begins with '-' is bound to its option rather than being reparsed by
            # the child argparse as a separate flag. name is additionally slug-
            # validated in agentic.registry, but desc/reason are free text and can
            # legitimately start with '-'.
            argv += [f"--name={name}", f"--desc={desc}"]
            if body:
                body_file = _write_body(body)
                argv += ["--body-file", body_file]
            if reason:
                argv += [f"--reason={reason}"]
            if action == "apply-skill" and confirm:
                argv.append("--confirm")

        proc = _run(argv)
        ok, label = _AGENTIC_LABELS.get(proc.returncode, (False, "unknown"))
        parsed = _maybe_json(proc.stdout) if (ok and action in _AGENTIC_JSON_ACTIONS) else None
        return OpsResult("agentic", action, proc.returncode, ok, label, proc.stdout, proc.stderr, parsed)
    finally:
        if body_file:
            Path(body_file).unlink(missing_ok=True)


def run_fsconnect_op(
    action: str,
    *,
    root: str | None = None,
    path: str | None = None,
    pattern: str | None = None,
    regex: bool = False,
    recursive: bool = True,
) -> OpsResult:
    """Invoke ``python -m agentic.fsconnect.cli <action>`` and normalize the result.

    Read-only operations: status, test, list, read, stat, grep, glob. File-path
    arguments are passed as ``--root``/``--path``; pattern as ``--pattern``.
    Browser/API grep is literal-only; local CLI users can still run ``--regex``.
    No write operations are exposed via this route.
    """
    if action not in _FSCONNECT_ACTIONS:
        raise OpsError(f"Unknown fsconnect action: {action!r}")
    if regex:
        raise OpsError("fsconnect regex grep is CLI-only; /ops/fsconnect accepts literal grep only")

    argv = [sys.executable, "-m", "agentic.fsconnect.cli", "--config", str(_CONFIG_PATH), action]

    if action in {"list", "read", "stat", "grep", "glob"}:
        # Use the --opt=value form (not two argv elements) for every free-text
        # value, matching run_agentic_op above: a value that begins with '-'
        # (a grep for the literal string "--dry-run" or "-v", a path/glob with a
        # leading dash) binds to its option instead of being reparsed by the
        # child argparse as a separate flag — which otherwise fails the op with
        # "expected one argument".
        if root:
            argv.append(f"--root={root}")
        if path:
            argv.append(f"--path={path}")
        if pattern and action in {"grep", "glob"}:
            argv.append(f"--pattern={pattern}")
        if not recursive and action == "glob":
            argv.append("--no-recursive")

    proc = _run(argv)
    ok, label = _FSCONNECT_LABELS.get(proc.returncode, (False, "unknown"))
    parsed = _maybe_json(proc.stdout) if (ok and action in _FSCONNECT_JSON_ACTIONS) else None
    return OpsResult("fsconnect", action, proc.returncode, ok, label, proc.stdout, proc.stderr, parsed)


def run_sqlconnect_op(
    action: str,
    *,
    sql: str | None = None,
    table: str | None = None,
    explain: bool = False,
    count: bool = False,
    fmt: str = "json",
) -> OpsResult:
    """Invoke ``python -m agentic.sqlconnect.cli <action>`` and normalize the result.

    Read-only operations: status, test, schema, query. The ``query`` action
    dispatches on ``--sql`` vs ``--table`` (with optional ``--explain`` / ``--count``).
    """
    if action not in _SQLCONNECT_ACTIONS:
        raise OpsError(f"Unknown sqlconnect action: {action!r}")

    argv = [sys.executable, "-m", "agentic.sqlconnect.cli", "--config", str(_CONFIG_PATH), action]

    if action == "query":
        # --opt=value for the free-text values (see run_fsconnect_op): a --sql
        # or --table beginning with '-' must bind to its option, not be reparsed
        # as a flag. --format is a constrained enum so its two-element form is safe.
        if sql:
            argv.append(f"--sql={sql}")
            if explain:
                argv.append("--explain")
            if fmt and fmt != "json":
                argv += ["--format", fmt]
        elif table:
            argv.append(f"--table={table}")
            if count:
                argv.append("--count")
        else:
            raise OpsError("sqlconnect query requires --sql or --table")

    proc = _run(argv)
    ok, label = _SQLCONNECT_LABELS.get(proc.returncode, (False, "unknown"))
    parsed = _maybe_json(proc.stdout) if (ok and action in _SQLCONNECT_JSON_ACTIONS) else None
    return OpsResult("sqlconnect", action, proc.returncode, ok, label, proc.stdout, proc.stderr, parsed)
