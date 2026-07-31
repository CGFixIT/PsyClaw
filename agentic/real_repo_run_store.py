"""On-disk persistence for pending real-repo coding runs.

A CLI-subprocess-per-call model (the only way ``harness/server.py`` may reach
``agentic/`` at all -- see I6) can't hold a ``RealRepoLoopResult`` or an open
``RepoWorkspaceTools`` in memory between "start a run" and a later "check
status" or "decide" call -- each is a separate process that exits after doing
its one job. This module is what survives between them: one small JSON
record per run, written atomically, read back by whichever later call needs
it. Records live under ``<deepagent_github.workspace_root>/runs`` -- a
sibling of the clone directories themselves, not a new top-level config
field, since a run record is operational state describing a specific clone,
not a durable artifact in its own right.

Not a general-purpose job queue. A run's lifecycle is exactly these states,
and a config-off/no-real-workload console (the shipped default) never creates
one at all::

    running -> pending_decision -> approved | rejected -> discarded
            -> exhausted -> discarded
            -> failed -> discarded
            -> discarded  (the process died before reaching pending_decision)

``discarded`` is reached only through the explicit, separate
``real-repo-run-discard`` CLI action -- never automatically. ``approved`` and
``rejected`` clones are retained past their own decision on purpose (a future
push step would need the same clone still on disk), so nothing here assumes
"decided" implies "reclaimed"; an operator (or a future sweep) discards a run
once it genuinely no longer needs its clone.

Concurrency: a real git commit already provides natural mutual exclusion for
a double "approve" (the second ``git commit`` call fails outright -- nothing
was staged the second time). This module additionally checks the record's own
``status`` before acting on a decision, so a double decision gets a clear
"already decided" error instead of a raw git failure -- not a full
lock-directory mechanism like ``harness_optimizer/patching.py``'s (accepted
for now: this is a single-operator, loopback-only console, not a
multi-tenant service -- ``docs/THREAT_MODEL.md`` scopes the whole harness
that way already).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from utils.errors import AgenticError

# Matches uuid.uuid4().hex exactly -- also the argv-injection/path-traversal
# defense for a run_id that arrives as an HTTP path parameter: no slash, dot,
# or any other character an attacker could use to escape runs_dir survives
# this pattern.
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")

PENDING_DECISION = "pending_decision"
_TERMINAL_STATUSES = frozenset({"approved", "rejected", "exhausted", "failed"})


def new_run_id() -> str:
    return uuid.uuid4().hex


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class RealRepoRunRecord:
    """Persisted state for one real-repo coding run."""

    run_id: str
    repo: str
    dest: str
    status: str  # "running"|"pending_decision"|"approved"|"rejected"|"exhausted"|"failed"|"discarded"
    provider: str | None = None  # None => LocalProposerClient; "grok"/"claude" => the cloud provider that drove it
    branch_name: str | None = None
    commit_message: str | None = None
    changed_files: list[str] = field(default_factory=list)
    iterations: int = 0
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise AgenticError("invalid run_id", details={"run_id": run_id})


def _run_path(runs_dir: Path, run_id: str) -> Path:
    _validate_run_id(run_id)
    return runs_dir / f"{run_id}.json"


def save_run(runs_dir: Path, record: RealRepoRunRecord) -> None:
    """Write (or overwrite) a run record atomically, stamping ``updated_at``."""
    record.updated_at = datetime.now(UTC).isoformat()
    if not record.created_at:
        record.created_at = record.updated_at
    _atomic_write_json(_run_path(runs_dir, record.run_id), record.to_dict())


def load_run(runs_dir: Path, run_id: str) -> RealRepoRunRecord:
    """Read back a run record. Raises ``AgenticError`` if it does not exist or is malformed."""
    path = _run_path(runs_dir, run_id)
    if not path.is_file():
        raise AgenticError("run not found", details={"run_id": run_id})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RealRepoRunRecord(**data)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        # This file crosses a real process boundary -- written by the run
        # subprocess, read back by a later status/decide subprocess -- so a
        # truncated write, a hand-edited record, or a future field rename all
        # need to land here rather than escape as a bare traceback.
        # RealRepoRunRecord(**data) raises TypeError for every one of those
        # shapes (a non-dict `data`, an unexpected key, a missing required
        # one), and it used to run OUTSIDE this try: the error skipped past
        # every caller's `except AgenticError`, reached main() uncaught, and
        # exited 1 -- a code outside the documented 0/2/3/4 API, which
        # utils/ops_runner.py maps to an unclassifiable "unknown" result.
        raise AgenticError("run record is unreadable or corrupt", details={"run_id": run_id}) from exc


def require_pending_decision(record: RealRepoRunRecord) -> None:
    """Raise unless ``record`` is awaiting exactly one decision.

    The natural mutual exclusion ``git commit`` itself provides (see module
    docstring) is a backstop, not the primary defense -- this check is what
    turns a double decision into a clear, immediate error instead of a raw
    git failure reached after redundant work.
    """
    if record.status in _TERMINAL_STATUSES:
        raise AgenticError(
            f"run {record.run_id} was already decided ({record.status})",
            details={"run_id": record.run_id, "status": record.status},
        )
    if record.status != PENDING_DECISION:
        raise AgenticError(
            f"run {record.run_id} is not awaiting a decision (status: {record.status})",
            details={"run_id": record.run_id, "status": record.status},
        )


__all__ = [
    "PENDING_DECISION",
    "RUN_ID_RE",
    "RealRepoRunRecord",
    "load_run",
    "new_run_id",
    "require_pending_decision",
    "save_run",
]
