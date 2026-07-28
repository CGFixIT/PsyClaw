"""PersonalityManager — Lean soul layer for CyClaw.

Based on soul.md as file-as-truth with a shadow DB for version history
and interaction logging. SHA-256 drift detection on startup.

Database backend: SQLite by default (zero-config, offline-first). Switch to
Postgres by setting CYCLAW_DB_URL=postgresql://... or personality.database_url
in config.yaml. See utils/personality_db.py for the connection shim.

Security: proposed soul evolutions are scanned before any write using the SAME
banned-pattern set the query path uses (config.yaml policy.prompt_filter), unioned
with a legacy OWASP baseline — so the soul (prepended to every LLM system prompt)
is no longer guarded by a weaker list than user queries. apply_evolution requires
an explicit human reason.
"""

import difflib
import hashlib
import logging
import os
import re
import stat
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from utils import personality_db
from utils.errors import PromptInjectionError, SoulPersistenceError
from utils.logger import audit_log

logger = logging.getLogger("cyclaw.personality")

# Anchor relative personality paths to the repo root, mirroring utils/logger.py's
# _REPO_ROOT/_anchor and utils/sanitizer.py. config.yaml ships soul_path and
# db_path as relative values, so resolving them against the process CWD meant
# launching cyclaw-server from anywhere else made _load_soul() find no soul.md,
# silently write _DEFAULT_SOUL into a fresh tree, and open an empty version DB —
# drift detection then has nothing to compare against and the real identity is
# quietly replaced. Same CWD fragility gate.py's _BASE_DIR exists to prevent.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _atomic_write_owner_only(path: Path, content: str) -> None:
    """Replace ``path`` atomically using an owner-only temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            fd = -1
            tmp_file.write(content)
        os.replace(tmp_path, path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove failed soul temporary file: %s", tmp_path)


def _anchor(path_str: str) -> Path:
    """Resolve path_str against the repo root when it isn't already absolute."""
    path = Path(path_str).expanduser()
    return path if path.is_absolute() else _REPO_ROOT / path

# Memory-poisoning / instruction-override patterns shared by both lists below.
# Any pattern here must never appear in soul.md (write-boundary enforcement)
# and is also suspicious in propose_evolution advisory review.
_CORE_INJECTION_PATTERNS: list[str] = [
    r"ignore\s+(previous|all|prior)\s+instructions",
    r"disregard\s+(previous|all|prior)",
    r"forget\s+(previous|all|prior)\s+instructions",
    r"new\s+instructions\s*:",
    r"system\s+prompt\s*:",
    r"override\s+instructions",
    r"jailbreak",
    r"DAN\s+mode",
    r"developer\s+mode",
]

# Critical patterns enforced at the soul-write boundary (apply_evolution).
# soul.md is prepended to every LLM system prompt, so anything here reaching
# it would persist as a standing instruction to the LLM.
ENFORCED_SOUL_PATTERNS: list[str] = _CORE_INJECTION_PATTERNS

# Advisory patterns for propose_evolution: the core set plus constructs that
# are suspicious in arbitrary text but may be legitimate in author-controlled
# identity statements (e.g. "You are now CyClaw; act as...").
# These are surfaced for human review but are not enforced at the write boundary.
OWASP_INJECTION_PATTERNS: list[str] = _CORE_INJECTION_PATTERNS + [
    r"you\s+are\s+now",
    r"pretend\s+(you\s+are|to\s+be)",
    r"act\s+as",
    r"<\s*script\s*>",
]

_DEFAULT_SOUL = "# Soul\n\nDefault CyClaw soul. Replace this file with your own identity statement.\n"


class PersonalityManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        pers_cfg = cfg.get("personality", {})
        self.soul_path = _anchor(pers_cfg.get("soul_path", "data/personality/soul.md"))
        self.db_path = _anchor(pers_cfg.get("db_path", "data/personality/cyclaw_soul.db"))
        self.ttl_days = pers_cfg.get("interaction_ttl_days", 365)
        # Amortize TTL pruning: sweep once per this many inserts instead of on
        # every record_interaction() call (see record_interaction for rationale).
        self._prune_every = pers_cfg.get("interaction_prune_every", 100)
        self._inserts_since_prune = 0
        # Hard ceiling on the soul text that gets prepended to EVERY LLM system
        # prompt. Bounds prompt inflation (and the Ollama context budget) no
        # matter how soul.md was written/edited. The /soul/apply schema enforces
        # a matching outer cap at the HTTP boundary (SoulEvolutionRequest).
        self.soul_max_chars = pers_cfg.get("soul_max_chars", 8000)
        self.soul_core: str = ""
        # Compile the injection scanner once: config banned_patterns ∪ OWASP.
        # Enforced = critical/write-boundary set (never written to soul.md).
        # Advisory = broader set surfaced for propose_evolution human review.
        self._advisory_patterns = self._build_patterns(OWASP_INJECTION_PATTERNS)
        self._enforced_patterns = self._build_patterns(ENFORCED_SOUL_PATTERNS)
        self._lock = threading.Lock()
        self._init_db()
        self._load_soul()
        self.maintenance()

    def _init_db(self) -> None:
        pers_cfg = self.cfg.get("personality", {})
        self.conn, self._ph, self._backend = personality_db.connect(self.db_path, pers_cfg)
        # Build parameterized SQL templates for this backend.
        # sha256 stores a hash of soul file *content*, not of the timestamp — the two are independent columns.
        self._sql_insert_soul = (
            f"INSERT INTO soul_versions (sha256, content, reason, timestamp)"  # DevSkim: ignore DS197836
            f" VALUES ({self._ph}, {self._ph}, {self._ph}, {self._ph})"
        )
        self._sql_insert_interaction = (
            f"INSERT INTO interactions (query_hash, outcome, timestamp)"
            f" VALUES ({self._ph}, {self._ph}, {self._ph})"
        )
        self._sql_delete_old_interactions = (
            f"DELETE FROM interactions WHERE timestamp < {self._ph}"
        )
        self.conn.execute(personality_db.ddl_soul_versions(self._backend))
        self.conn.execute(personality_db.ddl_interactions(self._backend))
        for index_ddl in personality_db.ddl_indexes(self._backend):
            self.conn.execute(index_ddl)
        self.conn.commit()

    def _sha256(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _load_soul(self) -> None:
        if not self.soul_path.exists():
            self.soul_path.parent.mkdir(parents=True, exist_ok=True)
            # Create the live identity file owner-only from its first byte.
            # Path.write_text() follows the process umask and commonly produced
            # 0644 on shared POSIX hosts, exposing every future system prompt.
            fd = os.open(
                self.soul_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as soul_file:
                soul_file.write(_DEFAULT_SOUL)
            file_hash = self._sha256(_DEFAULT_SOUL)
            with self._lock:
                self.conn.execute(
                    self._sql_insert_soul,
                    (file_hash, _DEFAULT_SOUL, "initial_default", datetime.now(UTC).isoformat())
                )
                self.conn.commit()
            self.soul_core = _DEFAULT_SOUL
            return

        # Manual edits and older installations may leave soul.md readable by
        # other local accounts. Harden it before loading its contents — but
        # only when the mode actually needs tightening, and never let a
        # permission failure here crash startup: an unconditional chmod()
        # raises PermissionError when soul.md is owned by another account (a
        # root-installed service running as a lower-privileged user, or an
        # ops team that deliberately shipped it read-only), which would
        # otherwise turn every soul.md load into a boot crash instead of a
        # read.
        try:
            if stat.S_IMODE(self.soul_path.stat().st_mode) != 0o600:
                os.chmod(self.soul_path, 0o600)
        except OSError:
            logger.warning("Could not harden soul.md permissions to 0600: %s", self.soul_path)

        # Hold the lock across the read-then-conditional-write so a concurrent
        # apply_evolution()/reload() on another thread cannot interleave with
        # this check-and-insert on the shared connection (opened
        # check_same_thread=False and shared across FastAPI's threadpool). The
        # file read + hash must be INSIDE the lock, not just the SELECT: reading
        # soul.md before acquiring the lock left a TOCTOU where a concurrent
        # apply_evolution() could write a newer soul in between, making this
        # call's file_hash stale by the time it compares against the (by-then
        # newer) latest DB row — inserting a spurious DRIFT_RECOVERY version and
        # then publishing the stale content as soul_core, reverting the apply
        # that just correctly landed. Publishing soul_core is inside the lock for
        # the same reason. audit_log() writes to a separate file, so the drift
        # event is emitted after the lock is released to keep the critical
        # section tight.
        drift_expected: str | None = None
        with self._lock:
            content = self.soul_path.read_text(encoding="utf-8")
            file_hash = self._sha256(content)
            row = self.conn.execute(
                "SELECT sha256 FROM soul_versions ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row and row["sha256"] != file_hash:
                drift_expected = row["sha256"]
                self.conn.execute(
                    self._sql_insert_soul,
                    (file_hash, content, "DRIFT_RECOVERY: file hash mismatch on startup",
                     datetime.now(UTC).isoformat())
                )
                self.conn.commit()
            elif not row:
                self.conn.execute(
                    self._sql_insert_soul,
                    (file_hash, content, "initial_load", datetime.now(UTC).isoformat())
                )
                self.conn.commit()
            self.soul_core = self._bounded_soul(content)
        if drift_expected is not None:
            audit_log({
                "event": "soul_drift_detected",
                "expected": drift_expected,
                "actual": file_hash,
                "path": str(self.soul_path),
            })

    def _bounded_soul(self, content: str) -> str:
        """Cap the in-memory soul (what is injected into every prompt) at
        soul_max_chars. Truncation is logged loudly — it never silently drops
        identity — but it guarantees a hand-edited or oversized soul.md cannot
        inflate the prompt past the context budget and re-trigger a 0% stall."""
        if len(content) > self.soul_max_chars:
            logger.warning(
                "soul content (%d chars) exceeds soul_max_chars=%d; truncating the "
                "in-memory soul prepended to prompts (soul.md on disk is unchanged)",
                len(content), self.soul_max_chars,
            )
            return content[: self.soul_max_chars]
        return content

    def get_system_prompt_additive(self) -> str:
        return self.soul_core

    def _max_version_id(self) -> int:
        """Read the newest soul_versions id. Caller MUST already hold self._lock
        (this issues a query on the shared connection without acquiring the lock
        itself, so it can also be called from inside apply_evolution's existing
        critical section without deadlocking on the non-reentrant Lock)."""
        row = self.conn.execute(
            "SELECT MAX(id) AS max_id FROM soul_versions"
        ).fetchone()
        return int(row["max_id"]) if row and row["max_id"] is not None else 0

    def get_version(self) -> int:
        # Serialize this read through the same lock the writers hold: the
        # connection is shared across threads (check_same_thread=False), and
        # GET /soul reads the version on the event-loop thread while
        # /soul/apply writes from a threadpool thread. An unlocked read on the
        # shared connection can race a concurrent write (e.g. "recursive use of
        # cursors not allowed"); taking the lock keeps connection access uniform.
        with self._lock:
            return self._max_version_id()

    def _build_patterns(self, base: list[str]) -> list[tuple]:
        """Compile ``base`` + all config-specified banned patterns.

        Config patterns (admin-specified banned list) are trusted and appended
        to whichever base set the caller passes: ENFORCED_SOUL_PATTERNS for the
        critical write-boundary set, or OWASP_INJECTION_PATTERNS for the broader
        advisory set. Returns (source, compiled) pairs; invalid regexes are
        skipped.
        """
        sources: list[str] = list(base)
        pf = (self.cfg.get("policy") or {}).get("prompt_filter") or {}
        for p in (pf.get("banned_patterns") or []):
            if p not in sources:
                sources.append(p)
        compiled: list[tuple] = []
        for p in sources:
            try:
                compiled.append((p, re.compile(p, re.IGNORECASE)))
            except re.error:
                continue
        return compiled

    def _scan_enforced(self, text: str) -> list[str]:
        """Return critical patterns that must not be written to soul.md."""
        return [src for src, pat in self._enforced_patterns if pat.search(text)]

    def _scan_advisory(self, text: str) -> list[str]:
        """Return advisory patterns for human review (propose_evolution)."""
        return [src for src, pat in self._advisory_patterns if pat.search(text)]

    def propose_evolution(self, new_soul: str, reason: str) -> dict:
        """Preview a proposed soul change: compute the diff + advisory injection flags.

        This method NEVER writes. ``injection_flags`` / ``safe_to_apply`` are an
        advisory signal surfaced for the human reviewing the proposal. Uses the broader
        OWASP-informed advisory pattern set. Enforcement at the write boundary
        (:meth:`apply_evolution`) uses only critical patterns (memory-poisoning).
        """
        flags = self._scan_advisory(new_soul)
        # Diff and hash against the file on disk, NOT self.soul_core: soul_core
        # is bounded to soul_max_chars (8000) by _bounded_soul, while the HTTP
        # cap is 8192 — so for an oversized soul.md the diff and current_sha
        # would describe a truncated copy rather than the real current file.
        # This is the artifact the human governance gate (I5) reviews before
        # approving a write; it has to describe what is actually there.
        current = self.soul_path.read_text(encoding="utf-8") if self.soul_path.exists() else self.soul_core
        diff = list(difflib.unified_diff(
            current.splitlines(keepends=True),
            new_soul.splitlines(keepends=True),
            fromfile="soul.md (current)",
            tofile="soul.md (proposed)"
        ))
        return {
            "diff": "".join(diff),
            "injection_flags": flags,
            "injection_flag_count": len(flags),
            "reason": reason,
            "safe_to_apply": len(flags) == 0,
            "status": "proposed",
            "proposed_soul": new_soul,
            "current_sha": self._sha256(current),
            "proposed_sha": self._sha256(new_soul),
        }

    def apply_evolution(self, new_soul: str, reason: str, *, scan: bool = True) -> dict:
        """Atomically write a new soul, enforcing the injection gate at the boundary.

        Authority to change the soul is human-gated: an explicit ``reason`` string
        is required and there is no autonomous/graph path here. On top of that, the
        injection scan is ENFORCED at the write boundary (``scan=True``, default):
        a proposed soul containing OWASP injection patterns raises
        ``PromptInjectionError`` before any file/DB write, closing the
        soul-poisoning vector (a flagged soul would otherwise be prepended to every
        LLM system prompt). The trusted internal restore path
        (:meth:`restore_from_backup`, re-applying a previously vetted ``.bak``)
        passes ``scan=False``. The write itself is atomic (``tmp`` + ``os.replace``)
        so a crash cannot leave a half-written ``soul.md``.
        """
        # Enforce critical patterns at the write boundary. propose_evolution() uses
        # the broader advisory set; this enforcement uses only critical patterns
        # (memory-poisoning / instruction-override) that must never reach soul.md.
        # Broader patterns like "you are now" are advisory-only and don't block writes.
        # Trusted internal callers (restore_from_backup) pass scan=False.
        if not reason or not reason.strip():
            raise ValueError("reason must not be empty")
        if scan:
            flags = self._scan_enforced(new_soul)
            if flags:
                audit_log({"event": "soul_apply_injection_blocked",
                           "reason": reason, "injection_flag_count": len(flags)})
                raise PromptInjectionError(
                    "Proposed soul contains critical injection patterns; refusing to apply",
                    details={"injection_flags": flags, "injection_flag_count": len(flags)},
                )
        new_hash = self._sha256(new_soul)
        bak_path = self.soul_path.with_suffix(self.soul_path.suffix + ".bak")
        # Publishing soul_core and reading the version MUST stay inside this same
        # critical section: releasing the lock after the DB commit and only then
        # doing `self.soul_core = ...` / `self.get_version()` left a window where
        # a second, concurrent apply_evolution() could run its own write+commit+
        # publish entirely in between — this call would then overwrite the newer
        # soul_core with its own (now stale) content, and report a version number
        # that belongs to the OTHER call's write. Keeping the whole write-then-
        # publish sequence under one lock acquisition makes each apply atomic.
        with self._lock:
            previous_soul: str | None = None
            previous_backup_exists = bak_path.exists()
            previous_backup = (
                bak_path.read_text(encoding="utf-8") if previous_backup_exists else ""
            )
            backup_published = False
            soul_published = False
            db_write_started = False
            try:
                if self.soul_path.exists():
                    # Back up the raw soul.md on disk, NOT self.soul_core: the
                    # in-memory copy is bounded to soul_max_chars by
                    # _bounded_soul, so using it would lose overflow on restore.
                    previous_soul = self.soul_path.read_text(encoding="utf-8")
                    _atomic_write_owner_only(bak_path, previous_soul)
                    backup_published = True

                _atomic_write_owner_only(self.soul_path, new_soul)
                soul_published = True
                db_write_started = True
                self.conn.execute(
                    self._sql_insert_soul,
                    (new_hash, new_soul, reason, datetime.now(UTC).isoformat())
                )
                # Read the version inside the transaction. If this query fails,
                # the DB row and both files can still be rolled back together.
                new_version = self._max_version_id()
                self.conn.commit()
            except Exception as operation_exc:
                # File-as-truth and version history must advance together. A
                # failed DB write previously left an unversioned soul active;
                # a failed live-file publication could also overwrite the prior
                # recovery backup. Compensate every resource already published.
                rollback_attempted = db_write_started
                rollback_succeeded: bool | None = None
                if rollback_attempted:
                    try:
                        self.conn.rollback()
                        rollback_succeeded = True
                    except Exception:
                        rollback_succeeded = False
                        logger.exception("Soul history transaction rollback failed")

                live_restored = not soul_published
                backup_restored = not backup_published
                try:
                    if soul_published:
                        if previous_soul is None:
                            self.soul_path.unlink(missing_ok=True)
                        else:
                            _atomic_write_owner_only(self.soul_path, previous_soul)
                        live_restored = True

                    if backup_published:
                        if previous_backup_exists:
                            _atomic_write_owner_only(bak_path, previous_backup)
                        else:
                            bak_path.unlink(missing_ok=True)
                        backup_restored = True
                except Exception as restore_exc:
                    failure_details = {
                        "error_type": type(operation_exc).__name__,
                        "restore_error_type": type(restore_exc).__name__,
                        "rollback_attempted": rollback_attempted,
                        "rollback_succeeded": rollback_succeeded,
                        "previous_soul_restored": live_restored,
                        "previous_backup_restored": backup_restored,
                    }
                    audit_log(
                        {
                            "event": "soul_evolution_failed",
                            "reason": reason,
                            "sha256": new_hash,
                            **failure_details,
                        },
                        cfg=self.cfg,
                    )
                    raise SoulPersistenceError(
                        "Soul history update failed and the previous files "
                        "could not be fully restored",
                        details=failure_details,
                    ) from operation_exc

                audit_log(
                    {
                        "event": "soul_evolution_failed",
                        "reason": reason,
                        "sha256": new_hash,
                        "error_type": type(operation_exc).__name__,
                        "rollback_attempted": rollback_attempted,
                        "rollback_succeeded": rollback_succeeded,
                        "previous_soul_restored": live_restored,
                        "previous_backup_restored": backup_restored,
                    },
                    cfg=self.cfg,
                )
                raise
            self.soul_core = self._bounded_soul(new_soul)
        audit_log({"event": "soul_evolution_applied", "reason": reason, "version": new_version, "sha256": new_hash})
        return {"status": "applied", "version": new_version, "sha256": new_hash}

    def restore_from_backup(self) -> dict:
        bak_path = self.soul_path.with_suffix(self.soul_path.suffix + ".bak")
        if not bak_path.exists():
            raise FileNotFoundError("No .bak file found to restore from")
        backup_content = bak_path.read_text(encoding="utf-8")
        # Non-blocking re-scan (PR #99 #7): the restore path intentionally uses
        # scan=False (the .bak is previously-vetted content, and
        # test_scan_false_bypass_for_trusted_restore documents that contract).
        # We still scan (advisory) and audit-log any match — so if a .bak ever trips
        # the advisory pattern set it is visible — without refusing the restore.
        restore_flags = self._scan_advisory(backup_content)
        if restore_flags:
            audit_log({"event": "soul_restore_scan_flags",
                       "injection_flag_count": len(restore_flags)})
        result = self.apply_evolution(backup_content, "RESTORE: reverted to previous .bak", scan=False)
        audit_log({"event": "soul_restored_from_backup", "sha256": result["sha256"]})
        return result

    def reload(self) -> None:
        self._load_soul()

    def record_interaction(self, query_hash: str, outcome: str) -> None:
        with self._lock:
            self.conn.execute(
                self._sql_insert_interaction,
                (query_hash, outcome, datetime.now(UTC).isoformat())
            )
            # Amortize the TTL prune. The previous code ran a full
            # `DELETE FROM interactions WHERE timestamp < cutoff` on *every*
            # insert -- and this runs on the hot audit path
            # (audit_logger_node -> record_interaction per query). With the
            # default 365-day TTL that DELETE scans the table and matches
            # nothing on virtually every call, so it was pure write
            # amplification under the lock. maintenance() already prunes on
            # __init__, and now we also sweep once per `_prune_every` inserts
            # (mirroring utils/ratelimit.py's periodic _sweep) so a
            # long-running server still bounds the table without paying the
            # DELETE cost on each request.
            self._inserts_since_prune += 1
            if self._inserts_since_prune >= self._prune_every:
                cutoff = (datetime.now(UTC) - timedelta(days=self.ttl_days)).isoformat()
                self.conn.execute(self._sql_delete_old_interactions, (cutoff,))
                self._inserts_since_prune = 0
            self.conn.commit()

    def close(self) -> None:
        """Close the DB connection (SQLite or Postgres).

        Called by gate.py's lifespan shutdown so the OS reclaims file
        descriptors promptly on server restart. No-op if already closed.
        Acquires _lock to avoid racing with record_interaction/maintenance.
        """
        with self._lock:
            if self.conn is not None:
                try:
                    self.conn.close()
                finally:
                    self.conn = None

    def maintenance(self, ttl_days: int | None = None) -> int:
        if ttl_days is None:
            ttl_days = self.ttl_days
        cutoff = (datetime.now(UTC) - timedelta(days=ttl_days)).isoformat()
        with self._lock:
            cursor = self.conn.execute(self._sql_delete_old_interactions, (cutoff,))
            self.conn.commit()
            return cursor.rowcount
