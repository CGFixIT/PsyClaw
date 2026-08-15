"""Ties utils/authn.py's primitives to utils/authn_store.py's tables.

Stage 2 of docs/AUTHENTICATION_DESIGN.md: session creation/validation,
login/logout, and per-device bearer tokens. Mirrors utils/personality.py's
shape (a manager class opened once from cfg, holding a shared DB connection
behind a threading.Lock because it is used from FastAPI's threadpool).

``AuthManager`` itself has no HTTP awareness -- gate_auth.py is the only
caller that knows about cookies, headers, or status codes. That split keeps
this module testable without a running app, the same reason
utils/personality.py knows nothing about /soul's HTTP layer.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from utils import authn, authn_store
from utils.errors import (
    AuthAccountLocked,
    AuthLastAdmin,
    AuthLoginFailed,
    AuthTokenLabelExists,
    AuthUserExists,
    AuthUserNotFound,
)

logger = logging.getLogger("cyclaw.authn_manager")

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Fixed username for the auto-generated first-run account
# (docs/AUTHENTICATION_DESIGN.md §10.4, bootstrap decision 2026-08-08, amended
# 2026-08-09): the account is seeded with an unusable placeholder hash and no
# credential ever reaches an output channel (see bootstrap_if_empty), and the
# username is not itself a secret -- so a stable, predictable "admin" is fine.
# The operator picks their own usernames for every account after this one via
# `cyclaw-user add`.
BOOTSTRAP_USERNAME = "admin"

# Default session bounds (docs/AUTHENTICATION_DESIGN.md §10.2, confirmed
# 2026-08-08): 12h idle, 7d absolute. Both configurable via auth.session.*.
_DEFAULT_IDLE_TIMEOUT_SEC = 12 * 3600
_DEFAULT_ABSOLUTE_TIMEOUT_SEC = 7 * 86400


def _anchor(path_str: str) -> Path:
    """Resolve path_str against the repo root when it isn't already absolute."""
    path = Path(path_str).expanduser()
    return path if path.is_absolute() else _REPO_ROOT / path


@dataclass
class LoginResult:
    username: str
    session_id: str
    csrf_token: str
    expires_ts: float


@dataclass
class SessionInfo:
    session_id: str
    username: str
    csrf_token: str


@dataclass
class UserSummary:
    username: str
    created_ts: float
    disabled: bool
    last_login_ts: float | None
    failed_count: int
    locked_until_ts: float | None
    role: str


@dataclass
class DeviceTokenSummary:
    label: str
    created_ts: float
    last_used_ts: float | None
    revoked: bool


# A syntactically valid record that no real password will ever match, built
# once at import time with the SAME cost parameters hash_password() currently
# uses. login() runs verify_password() against this for an unknown username so
# response timing does not disclose whether the username exists -- skipping
# the ~0.1s scrypt cost entirely for a nonexistent user would be a measurable
# side channel. The fixed salt is fine: this record is never meant to
# authenticate anything, only to cost the same as one that could.
#
# Known, accepted limitation: this equalizes cost against TODAY's policy
# constants, not against whatever a specific stored row actually used.
# verify_password() derives its cost from the n/r/p embedded IN the record
# (that is precisely what lets needs_rehash raise the work factor later
# without forcing a password reset) -- so the moment a future release raises
# _SCRYPT_N/_SCRYPT_R/_SCRYPT_P, any account that has not logged in since
# that bump (and so has not yet been transparently rehashed) becomes
# CHEAPER to verify than this freshly-recompiled dummy, reopening a
# username-timing gap for that account specifically until its next
# successful login. Not exploitable today (no bump has ever happened, so
# every stored record and this dummy use identical parameters) and not
# fixed here: doing so requires the dummy's cost to track the CURRENT
# minimum cost actually stored across the user table, which needs a
# DB read on every login attempt for a risk that does not exist yet. If
# _SCRYPT_N/R/P are ever raised, revisit this before shipping that change.
_DUMMY_RECORD = authn.hash_password("dummy-timing-equalization-password", salt=b"\x00" * 16)


class AuthManager:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        auth_cfg = cfg.get("auth", {}) or {}
        # No self.enabled here on purpose. Nothing ever read it, and a
        # bool()-truthy copy of auth.enabled sitting on this object is an
        # invitation to read it: `enabled: "false"` (quoted, so a STRING) is
        # truthy, which is the exact bug already fixed twice in this
        # subsystem -- gate.py's _boot_auth_enabled and _flag_is_true, and
        # gate_auth.py's tls_enabled, all now demand the literal True.
        # Whether auth is on is gate.py's decision, made before this object
        # is ever constructed; the manager just does what it is asked.
        self.db_path = _anchor(auth_cfg.get("db_path", "data/auth/cyclaw_auth.db"))
        session_cfg = auth_cfg.get("session", {}) or {}
        self.idle_timeout_sec = session_cfg.get("idle_timeout_sec", _DEFAULT_IDLE_TIMEOUT_SEC)
        self.absolute_timeout_sec = session_cfg.get("absolute_timeout_sec", _DEFAULT_ABSOLUTE_TIMEOUT_SEC)
        # Guards every read/write below (login, session/token validation,
        # admin ops) through THIS instance. login() holds it for the full
        # ~0.1s duration of verify_password()'s scrypt cost -- an accepted,
        # documented tradeoff, not an oversight. See login()'s own comment
        # for why a lock-free version was designed and rejected.
        self._lock = threading.Lock()
        self.conn, self._ph, self.backend = authn_store.connect(self.db_path, auth_cfg)
        self._prepare_sql()
        self._ensure_schema()

    def _prepare_sql(self) -> None:
        # Parameterized SQL templates, built once per backend. `ph` is always
        # the literal placeholder character ('?' sqlite / '%s' postgres) from
        # authn_store.connect(), never request data -- every actual VALUE
        # below is a query parameter, never interpolated. Ruff's S608 can't
        # see that distinction (same false positive utils/personality.py's
        # identical pattern hits), so this module carries the same
        # per-file-ignore in pyproject.toml.
        ph = self._ph
        self._sql_count_users = "SELECT COUNT(*) AS n FROM users"
        self._sql_get_user = f"SELECT * FROM users WHERE username = {ph}"
        self._sql_insert_user = (
            "INSERT INTO users "
            "(username, password_hash, created_ts, disabled, last_login_ts, failed_count, locked_until_ts, role) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})"
        )
        self._sql_set_role = f"UPDATE users SET role = {ph} WHERE username = {ph}"
        self._sql_delete_user = f"DELETE FROM users WHERE username = {ph}"
        self._sql_count_enabled_admins = (
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled = 0"
        )
        self._sql_set_disabled = f"UPDATE users SET disabled = {ph} WHERE username = {ph}"
        self._sql_set_password = f"UPDATE users SET password_hash = {ph} WHERE username = {ph}"
        # A single conditional UPDATE, not a read-then-write pair: it both
        # verifies the row is still exactly what login() checked (password
        # hash, disabled, lockout) AND claims it -- in one statement, so
        # there is no gap between "check" and "act" for a concurrent writer
        # to land in. See login()'s own comment for why a bare re-SELECT
        # (the previous approach) still left a narrower but real window.
        self._sql_claim_login = (
            f"UPDATE users SET last_login_ts = {ph}, failed_count = 0, locked_until_ts = NULL "
            f"WHERE username = {ph} AND password_hash = {ph} AND disabled = 0 "
            f"AND (locked_until_ts IS NULL OR locked_until_ts <= {ph})"
        )
        self._sql_login_failure = (
            f"UPDATE users SET failed_count = {ph}, locked_until_ts = {ph} WHERE username = {ph}"
        )
        self._sql_reset_lockout = (
            f"UPDATE users SET failed_count = 0, locked_until_ts = NULL WHERE username = {ph}"
        )
        self._sql_list_users = "SELECT * FROM users ORDER BY username"
        self._sql_insert_session = (
            "INSERT INTO sessions (session_id, username, csrf_token, created_ts, last_seen_ts, expires_ts) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})"
        )
        # JOINed against users.disabled as defense-in-depth: a disabled
        # account's sessions are also bulk-revoked by _set_disabled below, but
        # this means a session/token cannot authenticate for a disabled user
        # through ANY path, including one that someday bypasses that cascade
        # (e.g. a row inserted directly, or a future code path that forgets
        # to call disable_user()). Belt and suspenders, not a replacement.
        self._sql_get_session = (
            "SELECT s.* FROM sessions s JOIN users u ON s.username = u.username "
            f"WHERE s.session_id = {ph} AND u.disabled = 0"
        )
        self._sql_touch_session = f"UPDATE sessions SET last_seen_ts = {ph} WHERE session_id = {ph}"
        self._sql_revoke_session = f"UPDATE sessions SET revoked = 1 WHERE session_id = {ph} AND revoked = 0"
        self._sql_revoke_sessions_for_user = (
            f"UPDATE sessions SET revoked = 1 WHERE username = {ph} AND revoked = 0"
        )
        self._sql_insert_token = (
            "INSERT INTO device_tokens (token_hash, username, label, created_ts, last_used_ts, revoked) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})"
        )
        self._sql_get_token = (
            "SELECT t.* FROM device_tokens t JOIN users u ON t.username = u.username "
            f"WHERE t.token_hash = {ph} AND u.disabled = 0"
        )
        self._sql_touch_token = f"UPDATE device_tokens SET last_used_ts = {ph} WHERE token_hash = {ph}"
        self._sql_list_tokens = f"SELECT * FROM device_tokens WHERE username = {ph} ORDER BY created_ts"
        self._sql_get_live_token_by_label = (
            f"SELECT token_hash FROM device_tokens WHERE username = {ph} AND label = {ph} AND revoked = 0"
        )
        self._sql_revoke_token = (
            f"UPDATE device_tokens SET revoked = 1 WHERE username = {ph} AND label = {ph} AND revoked = 0"
        )
        self._sql_revoke_tokens_for_user = (
            f"UPDATE device_tokens SET revoked = 1 WHERE username = {ph} AND revoked = 0"
        )

    def _ensure_schema(self) -> None:
        self.conn.execute(authn_store.ddl_users())
        self.conn.execute(authn_store.ddl_sessions())
        self.conn.execute(authn_store.ddl_device_tokens())
        for index_ddl in authn_store.ddl_indexes():
            self.conn.execute(index_ddl)
        authn_store.ensure_users_role_column(self.conn, self.backend)
        self.conn.execute(
            f"UPDATE users SET role = {self._ph} WHERE username = {self._ph} AND role = {self._ph}",
            (authn.validate_role("admin"), BOOTSTRAP_USERNAME, authn.DEFAULT_ROLE),
        )
        self.conn.commit()

    def _now(self) -> float:
        return time.time()

    def _end_read_txn(self) -> None:
        """Close the implicit transaction a read-only query opened.

        psycopg connects with autocommit=False (utils/authn_store.py, mirroring
        utils/personality_db.py), so the FIRST statement -- a bare SELECT
        included -- opens a transaction that stays open until commit or
        rollback. A read-only path that returns without either leaves this
        long-lived server connection "idle in transaction", pinning a snapshot
        that stops VACUUM reclaiming dead rows for as long as the process runs.
        commit() rather than rollback() because these paths wrote nothing and
        there is nothing to undo; on SQLite, where a SELECT opens no write
        transaction, it is a no-op.
        """
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            logger.warning("shutdown close failed for auth manager", exc_info=True)

    # -- accounts ----------------------------------------------------------

    def bootstrap_if_empty(self) -> bool:
        """Create BOOTSTRAP_USERNAME with an UNUSABLE placeholder if no user exists.

        Returns True when the account was created, False when at least one
        user already exists -- so this is safe to call on every boot: it only
        ever acts on a genuinely empty table.

        The placeholder is the hash of a random secret that is generated,
        hashed, and DISCARDED inside this call -- deliberately never returned,
        printed, logged, or written anywhere in plaintext. An earlier version
        returned the plaintext for the caller to print once to the console;
        CodeQL flagged that (clear-text logging of sensitive data,
        cgfixit/CyClaw alert #1057) and the deeper point stands: a service's
        stdout is not ephemeral -- systemd's journal, Docker's log driver, and
        anything shipping logs off-box all persist it. Until the operator runs
        `cyclaw-user passwd admin` (local-only, getpass, no echo -- the same
        recovery path docs/AUTHENTICATION_DESIGN.md §9 already relies on),
        the account exists but cannot be logged into, exactly like a Linux
        account whose password field is locked. No fixed default credential
        ever ships, and no credential ever reaches an output channel.
        """
        with self._lock:
            row = self.conn.execute(self._sql_count_users).fetchone()
            if row and int(row["n"]) > 0:
                self._end_read_txn()
                return False
            record = authn.hash_password(authn.generate_bootstrap_password())
            now = self._now()
            self.conn.execute(
                self._sql_insert_user,
                (BOOTSTRAP_USERNAME, record, now, 0, None, 0, None, "admin"),
            )
            self.conn.commit()
            return True

    def create_user(self, username: str, password: str, role: str = authn.DEFAULT_ROLE) -> str:
        """Validate, hash, and insert a new user. Returns the canonical username."""
        canonical = authn.validate_username(username)
        canonical_role = authn.validate_role(role)
        record = authn.hash_password(password)  # raises PasswordPolicyError if invalid
        now = self._now()
        with self._lock:
            existing = self.conn.execute(self._sql_get_user, (canonical,)).fetchone()
            if existing is not None:
                self._end_read_txn()
                raise AuthUserExists(f"user already exists: {canonical}", details={"username": canonical})
            self.conn.execute(
                self._sql_insert_user, (canonical, record, now, 0, None, 0, None, canonical_role)
            )
            self.conn.commit()
        return canonical

    def _summary_from_row(self, row: object) -> UserSummary:
        role = row["role"] if "role" in row.keys() and row["role"] else authn.DEFAULT_ROLE  # type: ignore[index]
        return UserSummary(
            username=row["username"],  # type: ignore[index]
            created_ts=row["created_ts"],  # type: ignore[index]
            disabled=bool(row["disabled"]),  # type: ignore[index]
            last_login_ts=row["last_login_ts"],  # type: ignore[index]
            failed_count=row["failed_count"],  # type: ignore[index]
            locked_until_ts=row["locked_until_ts"],  # type: ignore[index]
            role=str(role),
        )

    def get_user(self, username: str) -> UserSummary | None:
        canonical = username.strip().lower() if isinstance(username, str) else ""
        with self._lock:
            row = self.conn.execute(self._sql_get_user, (canonical,)).fetchone()
            self._end_read_txn()
        if row is None:
            return None
        return self._summary_from_row(row)

    def list_users(self) -> list[UserSummary]:
        with self._lock:
            rows = self.conn.execute(self._sql_list_users).fetchall()
            self._end_read_txn()
        return [self._summary_from_row(r) for r in rows]

    def count_enabled_admins(self) -> int:
        with self._lock:
            row = self.conn.execute(self._sql_count_enabled_admins).fetchone()
            self._end_read_txn()
        return int(row["n"]) if row else 0

    def _is_last_enabled_admin(self, username: str) -> bool:
        user = self.get_user(username)
        if user is None or user.role != "admin" or user.disabled:
            return False
        return self.count_enabled_admins() <= 1

    def set_role(self, username: str, role: str) -> None:
        canonical = username.strip().lower() if isinstance(username, str) else ""
        canonical_role = authn.validate_role(role)
        if canonical_role != "admin" and self._is_last_enabled_admin(canonical):
            raise AuthLastAdmin(details={"username": canonical, "action": "set_role"})
        with self._lock:
            cur = self.conn.execute(self._sql_set_role, (canonical_role, canonical))
            self.conn.commit()
            if not cur.rowcount:
                raise AuthUserNotFound(f"unknown user: {canonical}", details={"username": canonical})

    def delete_user(self, username: str) -> None:
        canonical = username.strip().lower() if isinstance(username, str) else ""
        if self._is_last_enabled_admin(canonical):
            raise AuthLastAdmin(details={"username": canonical, "action": "delete_user"})
        with self._lock:
            self.conn.execute(self._sql_revoke_sessions_for_user, (canonical,))
            self.conn.execute(self._sql_revoke_tokens_for_user, (canonical,))
            cur = self.conn.execute(self._sql_delete_user, (canonical,))
            self.conn.commit()
            if not cur.rowcount:
                raise AuthUserNotFound(f"unknown user: {canonical}", details={"username": canonical})

    def _set_disabled(self, username: str, disabled: bool) -> None:
        canonical = username.strip().lower() if isinstance(username, str) else ""
        with self._lock:
            cur = self.conn.execute(self._sql_set_disabled, (int(disabled), canonical))
            if disabled and cur.rowcount:
                # A disabled account must lose every live credential
                # immediately, not just future logins -- otherwise an
                # already-issued session keeps working for up to its own 7-day
                # absolute expiry, and a device token (which has no expiry at
                # all) keeps working forever. This is
                # docs/AUTHENTICATION_DESIGN.md §3's adversary #3 ("a device
                # that was trusted and no longer should be"); revocation
                # exists to answer it NOW, not eventually.
                self.conn.execute(self._sql_revoke_sessions_for_user, (canonical,))
                self.conn.execute(self._sql_revoke_tokens_for_user, (canonical,))
            elif cur.rowcount:
                # login() records a failed attempt for a disabled account
                # exactly like a wrong password (see login()'s
                # `if row["disabled"] or not ok:` branch), so a disabled
                # account can accrue its own lockout from attempts made while
                # it was disabled. Re-enabling it is a deliberate
                # administrative decision to make it usable again NOW --
                # leaving a stale lockout in place would have `cyclaw-user
                # enable` still return 423 until the ceiling drains, the same
                # bug set_password() closes for the password-reset path.
                self.conn.execute(self._sql_reset_lockout, (canonical,))
            self.conn.commit()
            if not cur.rowcount:
                raise AuthUserNotFound(f"unknown user: {canonical}", details={"username": canonical})

    def disable_user(self, username: str) -> None:
        if self._is_last_enabled_admin(username):
            raise AuthLastAdmin(details={"username": username, "action": "disable_user"})
        self._set_disabled(username, True)

    def enable_user(self, username: str) -> None:
        self._set_disabled(username, False)

    def set_password(self, username: str, password: str) -> None:
        canonical = username.strip().lower() if isinstance(username, str) else ""
        record = authn.hash_password(password)  # raises PasswordPolicyError if invalid
        with self._lock:
            cur = self.conn.execute(self._sql_set_password, (record, canonical))
            if cur.rowcount:
                # A password change is a strong signal to force
                # re-authentication everywhere: if it was prompted by a
                # suspected leak, an attacker holding an already-issued
                # session must not get to keep using it. Device tokens are
                # deliberately NOT revoked here -- they are an independent
                # credential the user created on purpose, not derived from
                # the password, so a password change alone is not evidence
                # they are compromised too.
                self.conn.execute(self._sql_revoke_sessions_for_user, (canonical,))
                # Clearing the lockout is what makes the CLI an actual recovery
                # path. login() checks is_locked() BEFORE verifying the
                # password, so without this a `cyclaw-user passwd` on a locked
                # account leaves the owner getting 423 with their brand-new
                # correct password until the 15-minute ceiling drains --
                # and docs/AUTHENTICATION_DESIGN.md §9 names exactly this local
                # CLI as the mitigation for lockout-as-denial-of-service.
                # It is also the right semantics on its own: the counter
                # measures failed guesses against a credential that no longer
                # exists, so carrying it forward only punishes the owner for
                # the attacker's guesses.
                self.conn.execute(self._sql_reset_lockout, (canonical,))
            self.conn.commit()
            if not cur.rowcount:
                raise AuthUserNotFound(f"unknown user: {canonical}", details={"username": canonical})

    # -- login / sessions ----------------------------------------------------

    def _record_failure_locked(self, username: str, failed_count: int, now: float) -> None:
        new_count = failed_count + 1
        locked_until = authn.next_lock_until(new_count, now=now)
        self.conn.execute(self._sql_login_failure, (new_count, locked_until, username))
        self.conn.commit()

    def _create_session_locked(self, username: str, now: float) -> LoginResult:
        session_id = authn.new_session_id()
        csrf_token = authn.new_csrf_token()
        expires_ts = now + self.absolute_timeout_sec
        self.conn.execute(
            self._sql_insert_session, (session_id, username, csrf_token, now, now, expires_ts)
        )
        return LoginResult(
            username=username, session_id=session_id, csrf_token=csrf_token, expires_ts=expires_ts
        )

    def login(self, username: str, password: str) -> LoginResult:
        """Verify credentials and, on success, create a session.

        Raises AuthLoginFailed for an unknown username, a wrong password, OR a
        disabled account -- deliberately the same error in all three cases, so
        a caller cannot distinguish "no such account" from "wrong password"
        from "account exists but is disabled". Raises AuthAccountLocked
        instead when the account has an active lockout; that IS a distinct,
        informative error, because the client needs the retry delay.
        """
        canonical = username.strip().lower() if isinstance(username, str) else ""
        password = password if isinstance(password, str) else ""
        now = self._now()
        # Concurrency note (accepted tradeoff, not a bug -- raised in PR #830's
        # review round, 2026-08-09, and re-affirmed rather than changed here).
        # This lock is held for the FULL duration of verify_password() below,
        # which costs ~0.1s of real scrypt work. That serializes every
        # concurrent login through THIS AuthManager instance, and also blocks
        # validate_session()/verify_device_token() calls made through the same
        # instance while a login is mid-hash, since they share this lock too.
        #
        # A lock-free version -- hash outside the lock, then re-enter and
        # recheck before writing -- was designed and rejected. The obvious
        # implementation reintroduces a WORSE bug on the lockout counter: two
        # concurrent wrong-password attempts against the same account would
        # both read failed_count=4 outside the lock, both independently
        # compute new_count=5, and both write 5 -- five real failures
        # recording as one increment, undercounting the lockout ceiling.
        # Fixing that correctly needs a compare-and-swap on the counter (a
        # SQL UPDATE ... WHERE failed_count = ? guard) or a per-account lock,
        # which is a real feature with its own tests, not a drive-by change
        # bolted onto this method.
        #
        # The exposure left by NOT fixing this is bounded and judged
        # acceptable for docs/THREAT_MODEL.md's single-operator, LAN-scale
        # deployment: the per-IP rate limiter (gate.py's _enforce_rate_limit,
        # 60/min) already runs before this method is ever reached, and
        # realistic concurrent-login volume on a home LAN is a handful of
        # devices, not a load-testing scenario. Serializing that traffic
        # through one ~0.1s hash at a time costs a second concurrent caller a
        # small, bounded latency hit -- correct, just not maximally
        # concurrent. Revisit only with a fix verified safe by
        # mutation-testing the LOCKOUT-ACCURACY property specifically (not
        # just the happy path) -- a naive fix that "passes" only because no
        # test races two failed attempts is exactly how the undercount above
        # would ship unnoticed.
        with self._lock:
            row = self.conn.execute(self._sql_get_user, (canonical,)).fetchone()
            if row is None:
                # Pay the same scrypt cost a real check would, so timing does
                # not disclose whether this username exists.
                authn.verify_password(password, _DUMMY_RECORD)
                self._end_read_txn()
                raise AuthLoginFailed()
            if authn.is_locked(row["locked_until_ts"], now=now):
                retry_after = max(row["locked_until_ts"] - now, 0.0)
                self._end_read_txn()
                raise AuthAccountLocked(
                    f"account temporarily locked, retry in {int(retry_after) + 1}s",
                    retry_after_sec=retry_after,
                    details={"username": canonical},
                )
            ok, needs_rehash = authn.verify_password(password, row["password_hash"])
            if row["disabled"] or not ok:
                self._record_failure_locked(canonical, row["failed_count"], now)
                raise AuthLoginFailed()
            # Claim the row atomically before minting a session -- a single
            # conditional UPDATE, not a read-then-write pair. verify_password()
            # above costs ~0.1s (scrypt), and self._lock only serializes calls
            # made through THIS AuthManager instance -- it does nothing against
            # a concurrent `cyclaw-user passwd`/`disable` from a SEPARATE
            # process, which opens its own AuthManager and its own DB
            # connection. A bare re-SELECT here (the previous approach) closes
            # most of that window but not all of it: a concurrent
            # set_password()'s revoke-sessions-for-user can still run, find
            # nothing (this call's session doesn't exist yet), and commit
            # BEFORE this call's session INSERT lands -- so the new session
            # would survive a password rotation that was specifically meant to
            # cut it off immediately, the same "revoke NOW, not eventually"
            # promise set_password()/_set_disabled() make for already-issued
            # sessions. A single UPDATE closes this for real: it is the first
            # write in this transaction, so whichever of this call or a
            # concurrent set_password()/_set_disabled() reaches the row's lock
            # first forces the other to wait until it commits -- the loser
            # then re-evaluates its own WHERE clause against the winner's
            # already-committed state, so a losing login() sees rowcount == 0
            # (fails closed) and a losing set_password() (should this call win
            # the race) sees the just-created session once it proceeds, and
            # revokes it as usual.
            claimed = self.conn.execute(
                self._sql_claim_login, (now, canonical, row["password_hash"], now)
            )
            if not claimed.rowcount:
                # Deliberately NOT routed through _record_failure_locked(): the
                # credential presented was correct at the moment it was
                # checked. This is "the account changed out from under us",
                # not "a wrong guess" -- counting it toward the lockout
                # ceiling would let a legitimate password rotation contribute
                # to locking the account that rotation was just used on. Raise
                # the generic error, not a distinct one, so this race is not
                # itself an oracle for "a concurrent change just happened".
                self._end_read_txn()
                raise AuthLoginFailed()
            if needs_rehash:
                self.conn.execute(self._sql_set_password, (authn.hash_password(password), canonical))
            result = self._create_session_locked(canonical, now)
            self.conn.commit()
            return result

    def validate_session(self, session_id: str) -> SessionInfo | None:
        """Return the session's identity if it is live, else None.

        Live means: exists, not revoked, within BOTH the absolute expiry and
        the idle window since it was last used. A valid lookup slides the idle
        window forward (last_seen_ts = now) -- this is what makes idle_timeout_sec
        a rolling timeout rather than a second fixed expiry.
        """
        if not isinstance(session_id, str) or not session_id:
            return None
        now = self._now()
        with self._lock:
            row = self.conn.execute(self._sql_get_session, (session_id,)).fetchone()
            if row is None or row["revoked"]:
                self._end_read_txn()
                return None
            if now >= row["expires_ts"] or now >= row["last_seen_ts"] + self.idle_timeout_sec:
                # Revoke, rather than merely refusing this one call. The idle
                # limit is the reason: last_seen_ts is stored per row, but
                # idle_timeout_sec is re-read from config on every call, so a
                # session already observed idle-expired under a 12h timeout
                # becomes valid AGAIN if the operator raises idle_timeout_sec
                # and restarts -- the browser still holds the cookie and the
                # row was never marked dead. Writing the observation down
                # makes expiry a one-way door. (The absolute limit cannot
                # resurrect that way, since expires_ts is frozen into the row
                # at creation, but it costs nothing to retire that row too
                # rather than re-evaluating it until it ages out.)
                self.conn.execute(self._sql_revoke_session, (session_id,))
                self.conn.commit()
                return None
            self.conn.execute(self._sql_touch_session, (now, session_id))
            self.conn.commit()
            return SessionInfo(session_id=session_id, username=row["username"], csrf_token=row["csrf_token"])

    def logout(self, session_id: str) -> bool:
        """Revoke a session. Returns False for an unknown/already-revoked id
        (not an error -- logging out twice, or after expiry, is not a fault)."""
        if not isinstance(session_id, str) or not session_id:
            return False
        with self._lock:
            cur = self.conn.execute(self._sql_revoke_session, (session_id,))
            self.conn.commit()
            return bool(cur.rowcount)

    # -- per-device bearer tokens --------------------------------------------

    def create_device_token(self, username: str, label: str) -> str:
        """Mint a named, revocable bearer token for `username`. Returns the
        plaintext token exactly once; only its SHA-256 hash is stored."""
        canonical = username.strip().lower() if isinstance(username, str) else ""
        label = (label or "").strip() or "unlabeled"
        with self._lock:
            row = self.conn.execute(self._sql_get_user, (canonical,)).fetchone()
            if row is None:
                self._end_read_txn()
                raise AuthUserNotFound(f"unknown user: {canonical}", details={"username": canonical})
            # revoke_device_token() matches on (username, label) and revokes
            # every match, so a second live token under the same label would
            # be un-targetable -- revoking either kills both. Refuse here so
            # "one label, one live token" holds; the label frees up again the
            # moment its token is revoked.
            if self.conn.execute(self._sql_get_live_token_by_label, (canonical, label)).fetchone():
                self._end_read_txn()
                raise AuthTokenLabelExists(
                    f"a live token labelled {label!r} already exists for {canonical}",
                    details={"username": canonical, "label": label},
                )
            token = authn.new_device_token()
            token_hash = authn.hash_token(token)
            now = self._now()
            self.conn.execute(self._sql_insert_token, (token_hash, canonical, label, now, None, 0))
            self.conn.commit()
            return token

    def verify_device_token(self, token: str) -> str | None:
        """Return the owning username if `token` is a live, unrevoked token."""
        if not isinstance(token, str) or not token:
            return None
        token_hash = authn.hash_token(token)
        now = self._now()
        with self._lock:
            row = self.conn.execute(self._sql_get_token, (token_hash,)).fetchone()
            if row is None or row["revoked"]:
                self._end_read_txn()
                return None
            self.conn.execute(self._sql_touch_token, (now, token_hash))
            self.conn.commit()
            return row["username"]

    def list_device_tokens(self, username: str) -> list[DeviceTokenSummary]:
        canonical = username.strip().lower() if isinstance(username, str) else ""
        with self._lock:
            rows = self.conn.execute(self._sql_list_tokens, (canonical,)).fetchall()
            self._end_read_txn()
        return [
            DeviceTokenSummary(
                label=r["label"], created_ts=r["created_ts"],
                last_used_ts=r["last_used_ts"], revoked=bool(r["revoked"]),
            )
            for r in rows
        ]

    def revoke_device_token(self, username: str, label: str) -> bool:
        canonical = username.strip().lower() if isinstance(username, str) else ""
        with self._lock:
            cur = self.conn.execute(self._sql_revoke_token, (canonical, label))
            self.conn.commit()
            return bool(cur.rowcount)
