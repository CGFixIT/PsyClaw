"""Gated write operations for the file-share (writes default OFF).

Every write op is:

  * config-gated: ``fsconnect.writes_enabled`` (and the ``FS_WRITE_HARD_DISABLE``
    env kill-switch) must allow it, else the op returns a dry-run plan;
  * intent-audited BEFORE execution (JSONL audit stream, per CLAUDE.md);
  * containment-checked: targets must resolve inside a WRITABLE root, via the
    shared ``pathsafe`` security core (realpath containment, per-component
    O_NOFOLLOW walk, no symlink escapes);
  * content-scanned with the same injection heuristics fs_read applies to file
    content (defense-in-depth: a malicious corpus file must not coax a dangerous
    write when the operator replays it);
  * destructive ops (move/delete) additionally require an explicit
    ``confirm=True`` plus (for delete) the ``allow_hard_delete`` config flag.

Never imported by gate.py / graph.py / mcp_hybrid_server.py.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from agentic.fsconnect.config import FsConnectConfig
from agentic.fsconnect.pathsafe import (
    FS_WRITE_HARD_DISABLE,
    PathSafetyError,
    ScopedRoots,
    human_write_denied_reason,
    scope_paths,
)
from agentic.fsconnect.trash import TrashManager
from utils.errors import FsWriteRefused
from utils.logger import audit_log


class FsWriter:
    """Executes gated write/move/delete ops against the file-share.

    Use as a context manager so held write-root fds are released:
    ``with FsWriter(cfg, fs_cfg) as w: ...``
    """

    def __init__(self, cfg: dict, fs_cfg: FsConnectConfig, config_path: str = "config.yaml") -> None:
        self.cfg = cfg
        self.fs_cfg = fs_cfg
        self.config_path = config_path
        self._roots_cm = None
        self._roots: ScopedRoots | None = None
        self._trash = TrashManager(fs_cfg)

    def __enter__(self) -> "FsWriter":
        roots = self.fs_cfg.write_root_strs
        if not roots:
            raise FsWriteRefused(
                "no writable roots configured",
                details={"hint": "Set fsconnect.writable_roots in config.yaml."},
            )
        self._roots_cm = ScopedRoots(roots, create=True, allow_unc=self.fs_cfg.allow_unc_roots)
        self._roots = self._roots_cm.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._roots_cm is not None:
            self._roots_cm.__exit__(exc_type, exc, tb)
        self._roots_cm = None
        self._roots = None

    # -- internal helpers --------------------------------------------------

    def _refuse(self, op: str, failed_gate: str, message: str, rule: str) -> None:
        """Audit + raise a FsWriteRefused through the shared helper."""
        human_write_denied_reason(
            self.config_path,
            op=op,
            failed_gate=failed_gate,
            message=message,
            rule_applied=rule,
        )

    def _require_roots(self) -> ScopedRoots:
        if self._roots is None:
            raise FsWriteRefused("FsWriter must be used as a context manager")
        return self._roots

    def _pick_root(self, target: str, roots: ScopedRoots) -> str:
        """Select the writable root that contains ``target`` (first match)."""
        for root in roots.root_strs:
            try:
                roots.ensure_in_root(target, root)
                return root
            except PathSafetyError:
                continue
        raise FsWriteRefused(
            f"target is outside every writable root: {target!r}",
            details={"target": target, "writable_roots": roots.root_strs},
        )

    def _executable(self, op: str, reason: str, confirm: bool, *, destructive: bool) -> bool:
        """Return True to execute, False for dry-run. Raise FsWriteRefused on a gate fail."""
        if FS_WRITE_HARD_DISABLE or not self.fs_cfg.writes_enabled:
            return False
        if os.name == "nt":
            # Hard-refuse (codex P1): the Windows fallback validates and
            # reparse-checks a path by NAME, then opens/writes/moves/deletes by
            # NAME -- a junction swapped into an in-root component between
            # validation and use redirects the write outside the configured
            # root. POSIX holds directory fds and descends no-follow; the
            # Windows path has no equivalent containment yet, and its branches
            # are excluded from test coverage. The security review checklist
            # already documents Windows write-enablement as refused -- this
            # gate now ENFORCES it instead of relying on operator discipline.
            self._refuse(
                op, "platform",
                "fsconnect writes are refused on Windows until handle-based containment is implemented",
                "denied: writes refused on Windows (name-based TOCTOU containment gap)",
            )
        if not reason or not reason.strip():
            self._refuse(op, "reason", "a non-empty human reason is required to write",
                         "denied: human reason missing")
        if destructive and (not confirm or not self.fs_cfg.require_confirm_destructive):
            self._refuse(op, "confirm", "destructive op needs confirm=true",
                         "denied: destructive op not confirmed")
        return True

    def _content_gate(self, op: str, data: bytes) -> None:
        if not self.fs_cfg.scan_content:
            return
        from agentic.fsconnect.client import FsClient  # local: reuse its scan

        client = FsClient(self.cfg, self.fs_cfg)
        flags = client.scan(data)
        if flags and self.fs_cfg.block_on_injection_flags:
            self._refuse(op, "content", f"content flagged by injection scan ({len(flags)} hits)",
                         "denied: injection-pattern content")

    def _dryrun(self, op: str, reason: str, extra: dict) -> dict:
        audit_log({"event": "fsconnect_write_dryrun", "op": op, "reason": reason,
                   "rule_applied": "dry-run: writes_enabled is false (or hard-disabled); nothing written",
                   **{k: v for k, v in extra.items() if isinstance(v, (str, int, bool))}},
                  self.config_path)
        return {"status": "dry_run_plan", "op": op, "executed": False,
                "note": "set fsconnect.writes_enabled: true and supply reason + confirm to execute",
                "reason": reason, **extra}

    def _audit_intent(self, op: str, reason: str, path: str, intent_id: str,
                      extra: dict | None = None) -> None:
        event = {"event": "fsconnect_write_intent", "op": op, "reason": reason,
                 "path": path, "intent_id": intent_id}
        if extra:
            event.update({k: v for k, v in extra.items() if isinstance(v, (str, int, bool))})
        audit_log(event, self.config_path)

    @staticmethod
    def _audit_executed(op: str, path: str, intent_id: str, extra: dict | None = None) -> None:
        event = {"event": "fsconnect_write_executed", "op": op,
                 "path": path, "intent_id": intent_id}
        if extra:
            event.update({k: v for k, v in extra.items() if isinstance(v, (str, int, bool))})
        audit_log(event)

    # -- public ops ---------------------------------------------------------

    def fs_write(self, target: str, data: bytes, *, reason: str = "") -> dict:
        """Write (or, when gated off, plan) a file inside a writable root."""
        roots = self._require_roots()
        if not self._executable("fs_write", reason, confirm=True, destructive=False):
            return self._dryrun("fs_write", reason, {"path": target, "bytes": len(data)})
        root = self._pick_root(target, roots)
        self._content_gate("fs_write", data)
        intent_id = uuid.uuid4().hex[:12]
        self._audit_intent("fs_write", reason, target, intent_id,
                           {"bytes": len(data), "root": root})
        roots.write_bytes(target, data, root=root)
        self._audit_executed("fs_write", target, intent_id, {"bytes": len(data)})
        return {"status": "ok", "op": "fs_write", "executed": True,
                "path": target, "bytes": len(data), "intent_id": intent_id}

    def fs_move(self, src: str, dest: str, *, reason: str = "", confirm: bool = False) -> dict:
        """Move/rename a file (or dir) between two points inside writable roots."""
        roots = self._require_roots()
        if not self._executable("fs_move", reason, confirm, destructive=True):
            return self._dryrun("fs_move", reason, {"src": src, "dest": dest})
        src_root = self._pick_root(src, roots)
        dest_root = self._pick_root(dest, roots)
        intent_id = uuid.uuid4().hex[:12]
        self._audit_intent("fs_move", reason, src, intent_id,
                           {"dest": dest, "src_root": src_root, "dest_root": dest_root})
        roots.move(src, dest, src_root=src_root, dest_root=dest_root)
        self._audit_executed("fs_move", src, intent_id, {"dest": dest})
        return {"status": "ok", "op": "fs_move", "executed": True,
                "src": src, "dest": dest, "intent_id": intent_id}

    def fs_delete(self, target: str, *, reason: str = "", confirm: bool = False,
                  recursive: bool = False, use_trash: bool = True) -> dict:
        """Delete a file/dir -- by default into the per-root trash zone.

        Hard delete additionally requires ``fsconnect.allow_hard_delete``.
        """
        roots = self._require_roots()
        if not self._executable("fs_delete", reason, confirm, destructive=True):
            return self._dryrun("fs_delete", reason, {"path": target, "recursive": recursive})
        root = self._pick_root(target, roots)
        intent_id = uuid.uuid4().hex[:12]
        if use_trash and self._trash.enabled:
            self._audit_intent("fs_delete", reason, target, intent_id, {"mode": "trash"})
            dest = self._trash.move_to_trash(roots, root, target)
            self._audit_executed("fs_delete", target, intent_id, {"mode": "trash", "trash_path": dest})
            return {"status": "ok", "op": "fs_delete", "executed": True, "mode": "trash",
                    "path": target, "trash_path": dest, "intent_id": intent_id}
        if not self.fs_cfg.allow_hard_delete:
            self._refuse("fs_delete", "hard_delete",
                         "hard delete requires fsconnect.allow_hard_delete: true",
                         "denied: hard delete not enabled")
        self._audit_intent("fs_delete", reason, target, intent_id,
                           {"mode": "hard", "recursive": recursive})
        roots.delete(target, recursive=recursive, root=root)
        self._audit_executed("fs_delete", target, intent_id, {"mode": "hard"})
        return {"status": "ok", "op": "fs_delete", "executed": True, "mode": "hard",
                "path": target, "intent_id": intent_id}

    # -- trash management ----------------------------------------------------

    def trash_restore(self, name: str, *, reason: str = "", confirm: bool = False) -> dict:
        roots = self._require_roots()
        if not self._executable("fs_delete", reason, confirm, destructive=True):
            return self._dryrun("trash_restore", reason, {"name": name})
        intent_id = uuid.uuid4().hex[:12]
        restored = self._trash.restore(roots, name)
        self._audit_executed("trash_restore", restored["restored_to"], intent_id, {"name": name})
        return {"status": "ok", "op": "trash_restore", "executed": True,
                "restored_to": restored["restored_to"], "intent_id": intent_id}

    def trash_list(self, root: str | None = None) -> dict:
        roots = self._require_roots()
        entries = self._trash.list_entries(roots, root)
        return {"op": "trash_list", "count": len(entries), "entries": entries}

    def trash_purge(self, older_than_days: int | None = None, *, reason: str = "",
                    confirm: bool = False) -> dict:
        roots = self._require_roots()
        if not self._executable("trash_purge", reason, confirm, destructive=True):
            return self._dryrun("trash_purge", reason, {"older_than_days": older_than_days})
        intent_id = uuid.uuid4().hex[:12]
        purged = self._trash.purge(roots, older_than_days=older_than_days)
        self._audit_executed("trash_purge", "<trash>", intent_id, {"count": len(purged)})
        return {"status": "ok", "op": "trash_purge", "executed": True,
                "purged": purged, "count": len(purged), "intent_id": intent_id}


__all__ = ["FsWriter"]
