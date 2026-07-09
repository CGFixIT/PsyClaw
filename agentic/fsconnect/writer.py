"""Write operations for the filesystem connector -- fully built, gated, default-off.

Writes are confined to ``writable_roots`` (a SEPARATE list from the read
``allowed_roots``) via the same :class:`agentic.fsconnect.pathsafe.ScopedRoots`
security core, so a write can no more escape its zone than a read can. The connector
is **content-agnostic**: it never calls the LLM; callers supply bytes (e.g. an
operator pipes local-LLM/QWEN output into the CLI).

Gating (the careful guards), fail-closed and in order:

  1. ``writes_enabled`` (config, default False) -- while false, every op returns a
     DRY-RUN plan and nothing is written.
  2. a non-empty human ``reason`` -- required to execute (governance: no anonymous
     mutations), mirroring soul/registry governance.
  3. ``confirm=True`` -- required for DESTRUCTIVE ops (overwrite-existing, move,
     delete) when ``require_confirm_destructive`` is set. New-file writes use
     ``O_EXCL`` and refuse to clobber without ``overwrite``.
  4. purge (hard delete) adds a FIFTH gate: ``allow_hard_delete`` (config, global,
     default False). Trash is the default and needs only the four gates.

After the gates, two capacity/rate refusals may still fire (never relaxing a gate):
``quota`` (per-root byte/file ceiling) and ``rate_limit`` (per-root + global write
bandwidth). All refusals raise ``FsWriteRefused`` (exit 4) and are audited.

Two-phase audit (Phase 2, I3 hardening): a ``fsconnect_write_intent`` event is written
*before* the mutation and a ``fsconnect_write_applied`` event *after*, so a crash
between them leaves a detectable orphaned intent rather than an unaudited mutation.
Every event (intent/applied/refused/dryrun) carries a plain-language ``rule_applied``
string so an auditor can reconstruct *why* each op was allowed or denied.

``FS_WRITE_HARD_DISABLE`` is a module-level, code-level kill switch (default False):
flip it to True for the most locked-down sites to force dry-run regardless of config.

Never imported by gate.py / graph.py / mcp_hybrid_server.py.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from agentic.fsconnect import quota, trash
from agentic.fsconnect.client import build_injection_patterns
from agentic.fsconnect.config import FsConnectConfig
from agentic.fsconnect.pathsafe import SafeRoot, ScopedRoots, split_components
from utils.errors import FsConnectError, FsConnectRuntimeError, FsPathError, FsWriteRefused
from utils.logger import audit_log
from utils.ratelimit import RateLimiter

# Code-level kill switch (defense in depth). config.writes_enabled is the operator
# control; setting this True forces dry-run even if config enables writes.
FS_WRITE_HARD_DISABLE = False


class FsWriter:
    """Gated, confined writer bound to a config's ``writable_roots``.

    Use as a context manager so held write-root fds are released.
    """

    def __init__(
        self,
        cfg: dict,
        fs_cfg: FsConnectConfig,
        config_path: str = "config.yaml",
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cfg = cfg
        self.fs_cfg = fs_cfg
        self.config_path = config_path
        self._clock = clock
        # strict_roots + on_fallback complete Phase 2 item 2: a PermissionError on
        # root-prepare either fails closed (strict) or fires an audited
        # fsconnect_root_fallback event (lax) so silent write misdirection (R-7) is
        # impossible. config_path is set above so the callback can audit.
        self._roots = ScopedRoots(
            fs_cfg.write_root_strs,
            create=True,
            allow_unc=fs_cfg.allow_unc_roots,
            strict_roots=fs_cfg.strict_roots,
            on_fallback=self._audit_root_fallback,
        )
        self._patterns = build_injection_patterns(cfg) if fs_cfg.scan_content else []

    def close(self) -> None:
        self._roots.close()

    def __enter__(self) -> FsWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _now_dt(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), tz=UTC)

    def _audit_root_fallback(self, requested: str, fallback: str) -> None:
        audit_log(
            {"event": "fsconnect_root_fallback", "requested_root": requested,
             "fallback_root": fallback,
             "rule_applied": "root-prepare fell back to ~/CyClaw-FS (strict_roots is false); "
                             "investigate config drift"},
            self.config_path,
        )

    # --- gates ------------------------------------------------------------

    def _refuse(
        self, op: str, failed_gate: str, message: str, rule: str, *, extra: dict | None = None
    ) -> NoReturn:
        """Audit a gate failure (fsconnect_write_refused + rule_applied) then raise."""
        event = {"event": "fsconnect_write_refused", "op": op, "failed_gate": failed_gate,
                 "rule_applied": rule}
        if extra:
            event.update({k: v for k, v in extra.items() if isinstance(v, (str, int, bool))})
        audit_log(event, self.config_path)
        details: dict = {"op": op, "failed_gate": failed_gate}
        if extra:
            details.update(extra)
        raise FsWriteRefused(message, details=details)

    def _executable(self, op: str, reason: str, confirm: bool, *, destructive: bool) -> bool:
        """Return True to execute, False for dry-run. Raise FsWriteRefused on a gate fail."""
        if FS_WRITE_HARD_DISABLE or not self.fs_cfg.writes_enabled:
            return False
        if not (isinstance(reason, str) and reason.strip()):
            self._refuse(op, "reason", "a non-empty human reason is required to write",
                         "denied: human reason missing")
        if destructive and self.fs_cfg.require_confirm_destructive and confirm is not True:
            self._refuse(op, "confirm", "destructive op requires confirm=True",
                         "denied: confirm missing for destructive op")
        return True

    def _reserve_check(self, op: str, comps: list[str]) -> None:
        """Refuse ops targeting CyClaw's own metadata namespace (policy, not mechanism).

        Writing into ``.cyclaw-trash`` would forge deletion history; touching the quota
        ledger or an atomic-write tmp file would corrupt internal state. The trash and
        quota subcommands reach those paths through ``pathsafe`` directly, bypassing
        this public-op guard by design.
        """
        if not comps:
            return
        if comps[0] == trash.TRASH_DIR:
            self._refuse(op, "reserved_name",
                         f"{trash.TRASH_DIR!r} is reserved; use the trash-* subcommands",
                         "denied: reserved name .cyclaw-trash (use trash subcommands)",
                         extra={"component": comps[0]})
        leaf = comps[-1]
        if leaf == quota.QUOTA_FILE or leaf.endswith(".cyclaw-tmp"):
            self._refuse(op, "reserved_name",
                         f"{leaf!r} is a reserved internal filename",
                         "denied: reserved internal filename",
                         extra={"leaf": leaf})

    def _enforce_payload_cap(self, op: str, data: bytes) -> None:
        """Refuse the call BEFORE any subprocess/syscall when len(data) exceeds
        the configured ``max_write_bytes`` cap.

        FsConnectConfig validates that ``max_write_bytes`` is a positive int
        (config.py:_validate_caps), but the cap was never actually checked at
        the write/append site -- so the only operator-visible budget on a
        single payload size was whatever the filesystem itself enforced. Fail
        closed instead, before the bytes hit the disk, mirroring how the read
        path bounds ``max_file_bytes``. Raised as ``FsWriteRefused`` so the
        existing gate-failure plumbing surfaces it as a typed refusal rather
        than a bare OSError mid-write. This fires BEFORE ``_executable`` so an
        oversized payload can never leak its bytes/sha into a dry-run plan.
        """
        if len(data) > self.fs_cfg.max_write_bytes:
            raise FsWriteRefused(
                f"payload exceeds fsconnect.max_write_bytes "
                f"({len(data)} > {self.fs_cfg.max_write_bytes})",
                details={
                    "op": op,
                    "failed_gate": "max_write_bytes",
                    "bytes": len(data),
                    "max": self.fs_cfg.max_write_bytes,
                },
            )

    def _scan(self, data: bytes) -> list[str]:
        if not self._patterns:
            return []
        text = data.decode("utf-8", errors="replace")
        return [src for src, pat in self._patterns if pat.search(text)]

    def _check_injection(self, op: str, flags: list[str]) -> None:
        """Item 8: block a write whose advisory scan flagged content, if opted in.

        Default False preserves the content-agnostic contract; True is the recommended
        posture for CMMC-heavy deployments (documented in the playbook).
        """
        if flags and self.fs_cfg.block_on_injection_flags:
            self._refuse(
                op, "injection_scan",
                f"content flagged by the advisory injection scan ({len(flags)} pattern(s)) "
                "and block_on_injection_flags is set",
                f"denied: injection scan flagged {len(flags)} pattern(s) (block_on_injection_flags)",
                extra={"injection_flag_count": len(flags)},
            )

    # --- quota (item 6) ---------------------------------------------------

    def _quota_spec(self, sr: SafeRoot) -> object | None:
        spec = self.fs_cfg.write_root_quotas.get(sr.requested)
        if spec is None or (spec.quota_bytes is None and spec.max_files is None):
            return None
        return spec

    def _quota_ledger(self, op: str, sr: SafeRoot) -> quota.QuotaLedger:
        now = self._now_dt()
        ledger = quota.load(self._roots, sr.requested)
        if quota.is_stale(ledger, now, self.fs_cfg.quota_recompute_hours):
            gen = (ledger.generation + 1) if ledger is not None else 1
            try:
                ledger = quota.recompute(sr, now, gen)
            except OSError as exc:
                self._refuse(op, "quota",
                             "cannot determine quota usage (ledger stale/corrupt and recompute failed)",
                             "denied: quota usage indeterminate (fail closed)",
                             extra={"error": str(exc)})
            with suppress(FsConnectError, OSError):
                quota.save(self._roots, sr.requested, ledger)
        return ledger

    def _check_quota(self, op: str, sr: SafeRoot, delta_bytes: int, delta_files: int) -> str:
        spec = self._quota_spec(sr)
        if spec is None:
            return "quota unlimited"
        ledger = self._quota_ledger(op, sr)
        proj_bytes = ledger.used_bytes + delta_bytes
        proj_files = ledger.file_count + delta_files
        if spec.quota_bytes is not None and proj_bytes > spec.quota_bytes:  # type: ignore[attr-defined]
            self._refuse(op, "quota",
                         f"projected usage {proj_bytes} exceeds quota_bytes {spec.quota_bytes}",  # type: ignore[attr-defined]
                         f"denied: quota bytes {proj_bytes}/{spec.quota_bytes} exceeded",  # type: ignore[attr-defined]
                         extra={"used": ledger.used_bytes, "quota": spec.quota_bytes,  # type: ignore[attr-defined]
                                "requested": delta_bytes})
        if spec.max_files is not None and proj_files > spec.max_files:  # type: ignore[attr-defined]
            self._refuse(op, "quota",
                         f"projected file count {proj_files} exceeds max_files {spec.max_files}",  # type: ignore[attr-defined]
                         f"denied: quota files {proj_files}/{spec.max_files} exceeded",  # type: ignore[attr-defined]
                         extra={"used_files": ledger.file_count, "max_files": spec.max_files})  # type: ignore[attr-defined]
        limit = spec.quota_bytes if spec.quota_bytes is not None else "inf"  # type: ignore[attr-defined]
        return f"quota {proj_bytes}/{limit} bytes ok"

    def _update_ledger(self, sr: SafeRoot, delta_bytes: int, delta_files: int) -> None:
        if self._quota_spec(sr) is None:
            return
        now = self._now_dt()
        ledger = quota.load(self._roots, sr.requested)
        if ledger is None or quota.is_stale(ledger, now, self.fs_cfg.quota_recompute_hours):
            gen = (ledger.generation + 1) if ledger is not None else 1
            try:
                ledger = quota.recompute(sr, now, gen)
            except OSError:
                return
        else:
            ledger = quota.QuotaLedger(
                used_bytes=max(0, ledger.used_bytes + delta_bytes),
                file_count=max(0, ledger.file_count + delta_files),
                computed_at=ledger.computed_at,
                generation=ledger.generation + 1,
            )
        with suppress(FsConnectError, OSError):
            quota.save(self._roots, sr.requested, ledger)

    # --- rate limiting (item 7) -------------------------------------------

    def _check_rate(self, op: str, sr: SafeRoot) -> str:
        """Two ``allow()`` calls on a sqlite-persisted limiter: global ``fs:*`` first,
        then per-root ``fs:<normcase>``.

        The CLI is a short-lived subprocess, so ONLY the sqlite persistence backend
        makes cross-invocation limiting real (an in-memory limiter would reset each
        run). The two calls are not transactional (R-4): a cross-process race can
        overshoot by a small margin. Accepted per operator decision -- the limiter is
        an abuse brake, not a security boundary, and the failure mode is over-counting
        (fail-closed). ``allow()`` records a hit only when it returns True, so refusals
        and dry-runs (which never reach here) are not counted. This runs last, after
        every other gate, so an earlier refusal never burns rate budget.
        """
        settings = self.fs_cfg.rate_limit_settings
        if not settings["enabled"]:
            return "rate disabled"
        key = f"fs:{sr.normcase}"
        per_root = RateLimiter(max_requests=settings["max_ops"],
                               window_seconds=settings["window_seconds"],
                               clock=self._clock, db_path=settings["db_path"])
        global_lim = RateLimiter(max_requests=settings["global_max_ops"],
                                 window_seconds=settings["window_seconds"],
                                 clock=self._clock, db_path=settings["db_path"])
        try:
            if not global_lim.allow("fs:*"):
                self._refuse(op, "rate_limit",
                             f"global write rate limit exceeded "
                             f"({settings['global_max_ops']}/{settings['window_seconds']}s)",
                             "denied: global rate limit fs:* exceeded",
                             extra={"key": "fs:*", "max_ops": settings["global_max_ops"],
                                    "window_seconds": int(settings["window_seconds"])})
            if not per_root.allow(key):
                self._refuse(op, "rate_limit",
                             f"per-root write rate limit exceeded "
                             f"({settings['max_ops']}/{settings['window_seconds']}s)",
                             f"denied: per-root rate limit {key} exceeded",
                             extra={"key": key, "max_ops": settings["max_ops"],
                                    "window_seconds": int(settings["window_seconds"])})
        finally:
            per_root.close()
            global_lim.close()
        return f"rate ok ({settings['max_ops']}/{settings['window_seconds']}s)"

    # --- audit ------------------------------------------------------------

    def _dryrun(self, op: str, reason: str, extra: dict) -> dict:
        audit_log({"event": "fsconnect_write_dryrun", "op": op, "reason": reason,
                   "rule_applied": "dry-run: writes_enabled is false (or hard-disabled); nothing written",
                   **{k: v for k, v in extra.items() if isinstance(v, (str, int, bool))}},
                  self.config_path)
        return {"status": "dry_run_plan", "op": op, "executed": False,
                "note": "writes_enabled is false (or hard-disabled); nothing written.",
                "reason": reason, **extra}

    def _audit_intent(self, op: str, reason: str, path: str, intent_id: str,
                      extra: dict | None = None) -> None:
        event = {"event": "fsconnect_write_intent", "op": op, "reason": reason,
                 "path": path, "intent_id": intent_id}
        if extra:
            event.update({k: v for k, v in extra.items() if isinstance(v, (str, int, bool))})
        audit_log(event, self.config_path)

    def _audit_applied(self, op: str, reason: str, result: dict, flags: list[str],
                       intent_id: str, rule: str) -> None:
        path = str(result.get("path") or result.get("to") or result.get("created")
                   or result.get("removed") or "")
        audit_log({"event": "fsconnect_write_applied", "op": op, "reason": reason,
                   "path": path, "intent_id": intent_id, "rule_applied": rule,
                   "injection_flag_count": len(flags)}, self.config_path)

    def _allow_rule(self, *, destructive: bool, qnote: str, rnote: str,
                    flags: list[str], extra_parts: list[str] | None = None) -> str:
        parts = ["writes_enabled", "human reason"]
        if destructive:
            parts.append("confirm(destructive)")
        if extra_parts:
            parts.extend(extra_parts)
        parts.append(qnote)
        parts.append(rnote)
        if flags:
            parts.append(f"injection flags={len(flags)} (advisory)")
        return "allowed: " + " + ".join(parts)

    def _pre_size(self, target: str, root: str | None) -> tuple[bool, int, str | None]:
        try:
            st = self._roots.stat(target, root=root)
        except FsConnectError:
            return (False, 0, None)
        return (True, int(st.get("size", 0)), st.get("type"))

    # --- operations -------------------------------------------------------

    def fs_write(
        self, target: str, data: bytes, *, reason: str = "", confirm: bool = False,
        overwrite: bool = False, root: str | None = None,
    ) -> dict:
        comps = split_components(target)            # early path validation (pure)
        sr = self._roots.pick_root(root)            # root must be in the write allow-list
        self._reserve_check("fs_write", comps)
        self._enforce_payload_cap("fs_write", data)
        sha = hashlib.sha256(data).hexdigest()
        extra = {"target": target, "bytes": len(data), "sha256": sha, "overwrite": overwrite}
        if not self._executable("fs_write", reason, confirm, destructive=overwrite):
            return self._dryrun("fs_write", reason, extra)
        flags = self._scan(data)
        self._check_injection("fs_write", flags)
        existed, old_size, _ = self._pre_size(target, root)
        if overwrite and existed:
            d_bytes, d_files = len(data) - old_size, 0
        else:
            d_bytes, d_files = len(data), 1
        qnote = self._check_quota("fs_write", sr, d_bytes, d_files)
        rnote = self._check_rate("fs_write", sr)
        intent_id = uuid.uuid4().hex
        self._audit_intent("fs_write", reason, target, intent_id,
                           {"bytes": len(data), "overwrite": overwrite})
        result = self._roots.write_bytes(target, data, root=root, overwrite=overwrite)
        self._update_ledger(sr, d_bytes, d_files)
        rule = self._allow_rule(destructive=overwrite, qnote=qnote, rnote=rnote, flags=flags)
        self._audit_applied("fs_write", reason, result, flags, intent_id, rule)
        return {"status": "applied", "op": "fs_write", "executed": True, "reason": reason,
                "injection_flags": flags, "injection_flag_count": len(flags),
                "intent_id": intent_id, "rule_applied": rule, **result}

    def fs_append(
        self, target: str, data: bytes, *, reason: str = "", confirm: bool = False,
        root: str | None = None,
    ) -> dict:
        comps = split_components(target)
        sr = self._roots.pick_root(root)
        self._reserve_check("fs_append", comps)
        self._enforce_payload_cap("fs_append", data)
        extra = {"target": target, "bytes": len(data)}
        if not self._executable("fs_append", reason, confirm, destructive=False):
            return self._dryrun("fs_append", reason, extra)
        flags = self._scan(data)
        self._check_injection("fs_append", flags)
        existed, _old, _ = self._pre_size(target, root)
        d_bytes, d_files = len(data), (0 if existed else 1)
        qnote = self._check_quota("fs_append", sr, d_bytes, d_files)
        rnote = self._check_rate("fs_append", sr)
        intent_id = uuid.uuid4().hex
        self._audit_intent("fs_append", reason, target, intent_id, {"bytes": len(data)})
        result = self._roots.append_bytes(target, data, root=root)
        self._update_ledger(sr, d_bytes, d_files)
        rule = self._allow_rule(destructive=False, qnote=qnote, rnote=rnote, flags=flags)
        self._audit_applied("fs_append", reason, result, flags, intent_id, rule)
        return {"status": "applied", "op": "fs_append", "executed": True, "reason": reason,
                "injection_flags": flags, "injection_flag_count": len(flags),
                "intent_id": intent_id, "rule_applied": rule, **result}

    def fs_mkdir(
        self, target: str, *, reason: str = "", confirm: bool = False, root: str | None = None,
    ) -> dict:
        comps = split_components(target)
        sr = self._roots.pick_root(root)
        self._reserve_check("fs_mkdir", comps)
        extra = {"target": target}
        if not self._executable("fs_mkdir", reason, confirm, destructive=False):
            return self._dryrun("fs_mkdir", reason, extra)
        qnote = self._check_quota("fs_mkdir", sr, 0, 0)
        rnote = self._check_rate("fs_mkdir", sr)
        intent_id = uuid.uuid4().hex
        self._audit_intent("fs_mkdir", reason, target, intent_id)
        result = self._roots.mkdir(target, root=root)
        rule = self._allow_rule(destructive=False, qnote=qnote, rnote=rnote, flags=[])
        self._audit_applied("fs_mkdir", reason, result, [], intent_id, rule)
        return {"status": "applied", "op": "fs_mkdir", "executed": True, "reason": reason,
                "intent_id": intent_id, "rule_applied": rule, **result}

    def fs_move(
        self, src: str, dst: str, *, reason: str = "", confirm: bool = False,
        overwrite: bool = False, root: str | None = None,
    ) -> dict:
        scomps = split_components(src)
        dcomps = split_components(dst)
        sr = self._roots.pick_root(root)
        self._reserve_check("fs_move", scomps)
        self._reserve_check("fs_move", dcomps)
        extra = {"from": src, "to": dst, "overwrite": overwrite}
        if not self._executable("fs_move", reason, confirm, destructive=True):
            return self._dryrun("fs_move", reason, extra)
        qnote = self._check_quota("fs_move", sr, 0, 0)
        rnote = self._check_rate("fs_move", sr)
        intent_id = uuid.uuid4().hex
        self._audit_intent("fs_move", reason, dst, intent_id, {"from": src})
        result = self._roots.move(src, dst, root=root, overwrite=overwrite)
        rule = self._allow_rule(destructive=True, qnote=qnote, rnote=rnote, flags=[])
        self._audit_applied("fs_move", reason, result, [], intent_id, rule)
        return {"status": "applied", "op": "fs_move", "executed": True, "reason": reason,
                "intent_id": intent_id, "rule_applied": rule, **result}

    # --- fs_delete (item 4) -----------------------------------------------

    def fs_delete(
        self, target: str, *, reason: str = "", confirm: bool = False,
        purge: bool = False, root: str | None = None,
    ) -> dict:
        comps = split_components(target)
        if not comps:
            raise FsPathError("delete target must name a file/dir under the root, not the root")
        sr = self._roots.pick_root(root)
        self._reserve_check("fs_delete", comps)
        now = self._now_dt()
        mode = "purge" if purge else "trash"
        existed, size, kind = self._pre_size(target, root)
        if not self._executable("fs_delete", reason, confirm, destructive=True):
            plan: dict = {"target": target, "mode": mode}
            if not purge:
                plan["trash_entry"] = trash.entry_name(target, now)
                plan["retention_expires_at"] = trash.iso(
                    now + timedelta(days=self.fs_cfg.trash_retention_days))
            return self._dryrun("fs_delete", reason, plan)
        if purge and not self.fs_cfg.allow_hard_delete:
            self._refuse(
                "fs_delete", "allow_hard_delete",
                "--purge requires fsconnect.allow_hard_delete: true (config is false); "
                "trash mode remains available",
                "denied: --purge requires fsconnect.allow_hard_delete: true (config is false); "
                "trash mode remains available",
                extra={"mode": "purge"})
        if not existed:
            raise FsPathError(f"delete target does not exist: {target!r}", details={"target": target})
        if purge:
            d_bytes, d_files = (0, 0) if kind == "dir" else (-size, -1)
        else:
            d_bytes, d_files = 0, 0  # trash stays in-root: no net quota change
        qnote = self._check_quota("fs_delete", sr, d_bytes, d_files)
        rnote = self._check_rate("fs_delete", sr)
        intent_id = uuid.uuid4().hex
        self._audit_intent("fs_delete", reason, target, intent_id, {"mode": mode})
        if purge:
            result = self._purge(sr, target, kind, root)
            self._update_ledger(sr, d_bytes, d_files)
            extra_parts = ["purge (allow_hard_delete)"]
        else:
            result = self._to_trash(sr, target, kind, size, reason, now, root)
            extra_parts = ["trash-mode (allow_hard_delete not required)"]
        rule = self._allow_rule(destructive=True, qnote=qnote, rnote=rnote, flags=[],
                                extra_parts=extra_parts)
        self._audit_applied("fs_delete", reason, result, [], intent_id, rule)
        return {"status": "applied", "op": "fs_delete", "executed": True, "reason": reason,
                "mode": mode, "intent_id": intent_id, "rule_applied": rule, **result}

    def _purge(self, sr: SafeRoot, target: str, kind: str | None, root: str | None) -> dict:
        if kind == "dir":
            try:
                return self._roots.rmdir(target, root=root)
            except FsConnectRuntimeError as exc:
                if exc.details.get("non_empty"):
                    self._refuse(
                        "fs_delete", "non_empty_dir",
                        "cannot purge a non-empty directory; trash it or purge leaf-by-leaf",
                        "denied: non-empty directory purge refused (blast-radius control)",
                        extra={"target": target})
                raise
        return self._roots.unlink(target, root=root, sha_max_bytes=self.fs_cfg.max_file_bytes)

    def _ensure_trash(self, root: str | None) -> None:
        with suppress(FsConnectRuntimeError):
            self._roots.mkdir(trash.TRASH_DIR, root=root)

    def _content_sha(self, target: str, kind: str | None, size: int, root: str | None) -> str | None:
        if kind == "dir" or size > self.fs_cfg.max_file_bytes:
            return None
        try:
            data = self._roots.read_bytes(target, root=root, max_bytes=self.fs_cfg.max_file_bytes)
        except FsConnectError:
            return None
        return hashlib.sha256(data).hexdigest()

    def _to_trash(self, sr: SafeRoot, target: str, kind: str | None, size: int,
                  reason: str, now: datetime, root: str | None) -> dict:
        self._ensure_trash(root)
        sha = self._content_sha(target, kind, size, root)
        skipped = "size" if (kind != "dir" and size > self.fs_cfg.max_file_bytes) else None
        entry = trash.make_entry(
            target, now, reason=reason, sha256=sha, size=size,
            kind=("dir" if kind == "dir" else "file"),
            retention_days=self.fs_cfg.trash_retention_days, sha256_skipped=skipped)
        dst = f"{trash.TRASH_DIR}/{entry.name}"
        move_res = self._roots.move(target, dst, root=root, overwrite=False)
        self._roots.write_bytes(f"{dst}{trash.META_SUFFIX}", entry.meta_bytes(),
                                root=root, overwrite=False)
        return {"removed": move_res["from"], "trash_entry": entry.name,
                "retention_expires_at": entry.retention_expires_at,
                "sha256": entry.sha256, "size": size, "kind": entry.kind}

    # --- trash lifecycle (item 5) -----------------------------------------

    def trash_empty(self, *, reason: str = "", confirm: bool = False,
                    all_entries: bool = False, root: str | None = None) -> dict:
        sr = self._roots.pick_root(root)
        now = self._now_dt()
        entries = trash.list_entries(self._roots, root)
        targets = entries if all_entries else trash.expired(entries, now)
        if not self._executable("trash_empty", reason, confirm, destructive=True):
            return self._dryrun("trash_empty", reason,
                                 {"would_purge": [e.name for e in targets],
                                  "orphan_tmp": self._find_orphan_tmp(sr),
                                  "all": all_entries})
        if not self.fs_cfg.allow_hard_delete:
            self._refuse("trash_empty", "allow_hard_delete",
                         "trash-empty permanently purges entries and requires "
                         "fsconnect.allow_hard_delete: true",
                         "denied: trash-empty requires fsconnect.allow_hard_delete: true")
        rnote = self._check_rate("trash_empty", sr)
        purged: list[str] = []
        for e in targets:
            intent_id = uuid.uuid4().hex
            self._audit_intent("trash_empty", reason, e.name, intent_id, {"trash_entry": e.name})
            self._purge_trash_entry(sr, e, root)
            rule = ("allowed: writes_enabled + human reason + confirm(destructive) + "
                    f"trash-empty (allow_hard_delete) + {rnote}")
            self._audit_applied("trash_empty", reason, {"removed": e.name}, [], intent_id, rule)
            purged.append(e.name)
        swept = self._sweep_orphan_tmp(sr, root)
        self._update_ledger(sr, 0, 0)
        return {"status": "applied", "op": "trash_empty", "executed": True, "reason": reason,
                "purged": purged, "swept_tmp": swept}

    def _purge_trash_entry(self, sr: SafeRoot, entry: trash.TrashEntry, root: str | None) -> None:
        payload = f"{trash.TRASH_DIR}/{entry.name}"
        with suppress(FsConnectError):
            self._purge_tree(sr, payload, root)
        with suppress(FsConnectError):
            self._roots.unlink(f"{payload}{trash.META_SUFFIX}", root=root, sha_max_bytes=0)

    def _purge_tree(self, sr: SafeRoot, rel: str, root: str | None) -> None:
        """Recursively remove a trash payload (file or whole dir) via pathsafe.

        Confined to ``.cyclaw-trash`` (CyClaw-owned quarantine, already root-contained);
        this is the only recursive removal in Phase 2 and is deliberately NOT exposed as
        a public ``fs_delete --purge`` on arbitrary dirs (blast-radius control). Each hop
        re-descends from the held root fd with ``O_NOFOLLOW`` so containment still holds.
        """
        try:
            st = self._roots.stat(rel, root=root)
        except FsConnectError:
            return
        if st.get("type") == "dir":
            for item in self._roots.list_dir(rel, root=root):
                self._purge_tree(sr, f"{rel}/{item['name']}", root)
            self._roots.rmdir(rel, root=root)
        else:
            self._roots.unlink(rel, root=root, sha_max_bytes=0)

    def _find_orphan_tmp(self, sr: SafeRoot) -> list[str]:
        """Relative paths of ``*.cyclaw-tmp`` files older than 24 h (crash leftovers)."""
        cutoff = self._clock() - 24 * 3600
        base = str(sr.path)
        out: list[str] = []
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.endswith(".cyclaw-tmp"):
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    st = os.stat(full, follow_symlinks=False)
                except OSError:
                    continue
                if st.st_mtime < cutoff:
                    out.append(os.path.relpath(full, base))
        return sorted(out)

    def _sweep_orphan_tmp(self, sr: SafeRoot, root: str | None) -> list[str]:
        swept: list[str] = []
        for rel in self._find_orphan_tmp(sr):
            with suppress(FsConnectError):
                self._roots.unlink(rel, root=root, sha_max_bytes=0)
                swept.append(rel)
        return swept

    def trash_restore(self, entry: str, *, reason: str = "", confirm: bool = False,
                      overwrite: bool = False, root: str | None = None) -> dict:
        sr = self._roots.pick_root(root)
        entries = trash.list_entries(self._roots, root)
        te = trash.find_entry(entries, entry)  # FsConnectRuntimeError -> exit 2 if missing
        if not self._executable("trash_restore", reason, confirm, destructive=True):
            return self._dryrun("trash_restore", reason,
                                 {"entry": entry, "original_path": te.original_path,
                                  "overwrite": overwrite})
        rnote = self._check_rate("trash_restore", sr)
        intent_id = uuid.uuid4().hex
        self._audit_intent("trash_restore", reason, te.original_path, intent_id,
                           {"trash_entry": entry})
        payload = f"{trash.TRASH_DIR}/{entry}"
        result = self._roots.move(payload, te.original_path, root=root, overwrite=overwrite)
        with suppress(FsConnectError):
            self._roots.unlink(f"{payload}{trash.META_SUFFIX}", root=root, sha_max_bytes=0)
        rule = ("allowed: writes_enabled + human reason + confirm(destructive) + "
                f"trash-restore + {rnote}")
        self._audit_applied("trash_restore", reason, result, [], intent_id, rule)
        return {"status": "applied", "op": "trash_restore", "executed": True, "reason": reason,
                "restored": te.original_path, "entry": entry,
                "intent_id": intent_id, "rule_applied": rule}

    # --- quota-status (item 6, read-only) ---------------------------------

    def quota_status(self, *, root: str | None = None, recompute: bool = False) -> dict:
        sr = self._roots.pick_root(root)
        now = self._now_dt()
        ledger = quota.load(self._roots, sr.requested)
        if recompute or quota.is_stale(ledger, now, self.fs_cfg.quota_recompute_hours):
            gen = (ledger.generation + 1) if ledger is not None else 1
            ledger = quota.recompute(sr, now, gen)
            with suppress(FsConnectError, OSError):
                quota.save(self._roots, sr.requested, ledger)
        spec = self.fs_cfg.write_root_quotas.get(sr.requested)
        return {"root": sr.requested, "used_bytes": ledger.used_bytes,
                "file_count": ledger.file_count, "computed_at": ledger.computed_at,
                "generation": ledger.generation,
                "quota_bytes": spec.quota_bytes if spec else None,
                "max_files": spec.max_files if spec else None,
                "trash_entries": len(trash.list_entries(self._roots, root))}


__all__ = ["FsWriter", "FS_WRITE_HARD_DISABLE"]
