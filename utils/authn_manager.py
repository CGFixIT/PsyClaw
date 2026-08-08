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
from utils.errors import AuthAccountLocked, AuthLoginFailed, AuthUserExists, AuthUserNotFound

logger = logging.getLogger("cyclaw.authn_manager")

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Fixed username for the auto-generated first-run account
# (docs/AUTHENTICATION_DESIGN.md §10.4, bootstrap decision 2026-08-08): a
# random one-time PASSWORD is generated at first boot, but the username is
# not itself a secret, so a stable, predictable "admin" is fine -- the
# operator picks their own usernames for every account after this one via
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
_DUMMY_RECORD = authn.hash_password("dummy-timing-equalization-password", salt=b"\x00" * 16)


class AuthManager:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        auth_cfg = cfg.get("auth", {}) or {}
        self.enabled = bool(auth_cfg.get("enabled", False))
        self.db_path = _anchor(auth_cfg.get("db_path", "data/auth/cyclaw_auth.db"))
        session_cfg = auth_cfg.get("session", {}) or {}
        self.idle_timeout_sec = session_cfg.get("idle_timeout_sec", _DEFAULT_IDLE_TIMEOUT_SEC)
        self.absolute_timeout_sec = session_cfg.get("absolute_timeout_sec", _DEFAULT_ABSOLUTE_TIMEOUT_SEC)
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
            "(username, password_hash, created_ts, disabled, last_login_ts, failed_count, locked_until_ts) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})"
        )
        self._sql_set_disabled = f"UPDATE users SET disabled = {ph} WHERE username = {ph}"
        self._sql_set_password = f"UPDATE users SET password_hash = {ph} WHERE username = {ph}"
        self._sql_login_success = (
            f"UPDATE users SET last_login_ts = {ph}, failed_count = 0, locked_until_ts = NULL "
            f"WHERE username = {ph}"
        )
        self._sql_login_failure = (
            f"UPDATE users SET failed_count = {ph}, locked_until_ts = {ph} WHERE username = {ph}"
        )
        self._sql_list_users = "SELECT * FROM users ORDER BY username"
        self._sql_insert_session = (
            "INSERT INTO sessions (session_id, username, csrf_token, created_ts, last_seen_ts, expires_ts) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})"
        )
        self._sql_get_session = f"SELECT * FROM sessions WHERE session_id = {ph}"
        self._sql_touch_session = f"UPDATE sessions SET last_seen_ts = {ph} WHERE session_id = {ph}"
        self._sql_revoke_session = f"UPDATE sessions SET revoked = 1 WHERE session_id = {ph} AND revoked = 0"
        self._sql_insert_token = (
            "INSERT INTO device_tokens (token_hash, username, label, created_ts, last_used_ts, revoked) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})"
        )
        self._sql_get_token = f"SELECT * FROM device_tokens WHERE token_hash = {ph}"
        self._sql_touch_token = f"UPDATE device_tokens SET last_used_ts = {ph} WHERE token_hash = {ph}"
        self._sql_list_tokens = f"SELECT * FROM device_tokens WHERE username = {ph} ORDER BY created_ts"
        self._sql_revoke_token = (
            f"UPDATE device_tokens SET revoked = 1 WHERE username = {ph} AND label = {ph} AND revoked = 0"
        )

    def _ensure_schema(self) -> None:
        self.conn.execute(authn_store.ddl_users())
        self.conn.execute(authn_store.ddl_sessions())
        self.conn.execute(authn_store.ddl_device_tokens())
        for index_ddl in authn_store.ddl_indexes():
            self.conn.execute(index_ddl)
        self.conn.commit()

    def _now(self) -> float:
        return time.time()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            logger.warning("shutdown close failed for auth manager", exc_info=True)

    # -- accounts ----------------------------------------------------------

    def bootstrap_if_empty(self) -> str | None:
        """Create BOOTSTRAP_USERNAME with a random password if no user exists.

        Returns the plaintext password exactly once, for the caller to print
        to the local console (never logged, never written to disk in
        plaintext -- only hash_password()'s output is persisted). Returns
        None when at least one user already exists, so this is safe to call
        on every boot: it only ever acts on a genuinely empty table.
        """
        with self._lock:
            row = self.conn.execute(self._sql_count_users).fetchone()
            if row and int(row["n"]) > 0:
                return None
            password = authn.generate_bootstrap_password()
            record = authn.hash_password(password)
            now = self._now()
            self.conn.execute(
                self._sql_insert_user, (BOOTSTRAP_USERNAME, record, now, 0, None, 0, None)
            )
            self.conn.commit()
            return password

    def create_user(self, username: str, password: str) -> str:
        """Validate, hash, and insert a new user. Returns the canonical username."""
        canonical = authn.validate_username(username)
        record = authn.hash_password(password)  # raises PasswordPolicyError if invalid
        now = self._now()
        with self._lock:
            existing = self.conn.execute(self._sql_get_user, (canonical,)).fetchone()
            if existing is not None:
                raise AuthUserExists(f"user already exists: {canonical}", details={"username": canonical})
            self.conn.execute(self._sql_insert_user, (canonical, record, now, 0, None, 0, None))
            self.conn.commit()
        return canonical

    def list_users(self) -> list[UserSummary]:
        with self._lock:
            rows = self.conn.execute(self._sql_list_users).fetchall()
        return [
            UserSummary(
                username=r["username"],
                created_ts=r["created_ts"],
                disabled=bool(r["disabled"]),
                last_login_ts=r["last_login_ts"],
                failed_count=r["failed_count"],
                locked_until_ts=r["locked_until_ts"],
            )
            for r in rows
        ]

    def _set_disabled(self, username: str, disabled: bool) -> None:
        canonical = username.strip().lower() if isinstance(username, str) else ""
        with self._lock:
            cur = self.conn.execute(self._sql_set_disabled, (int(disabled), canonical))
            self.conn.commit()
            if not cur.rowcount:
                raise AuthUserNotFound(f"unknown user: {canonical}", details={"username": canonical})

    def disable_user(self, username: str) -> None:
        self._set_disabled(username, True)

    def enable_user(self, username: str) -> None:
        self._set_disabled(username, False)

    def set_password(self, username: str, password: str) -> None:
        canonical = username.strip().lower() if isinstance(username, str) else ""
        record = authn.hash_password(password)  # raises PasswordPolicyError if invalid
        with self._lock:
            cur = self.conn.execute(self._sql_set_password, (record, canonical))
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
        with self._lock:
            row = self.conn.execute(self._sql_get_user, (canonical,)).fetchone()
            if row is None:
                # Pay the same scrypt cost a real check would, so timing does
                # not disclose whether this username exists.
                authn.verify_password(password, _DUMMY_RECORD)
                raise AuthLoginFailed()
            if authn.is_locked(row["locked_until_ts"], now=now):
                retry_after = max(row["locked_until_ts"] - now, 0.0)
                raise AuthAccountLocked(
                    f"account temporarily locked, retry in {int(retry_after) + 1}s",
                    retry_after_sec=retry_after,
                    details={"username": canonical},
                )
            ok, needs_rehash = authn.verify_password(password, row["password_hash"])
            if row["disabled"] or not ok:
                self._record_failure_locked(canonical, row["failed_count"], now)
                raise AuthLoginFailed()
            if needs_rehash:
                self.conn.execute(self._sql_set_password, (authn.hash_password(password), canonical))
            self.conn.execute(self._sql_login_success, (now, canonical))
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
                return None
            if now >= row["expires_ts"]:
                return None
            if now >= row["last_seen_ts"] + self.idle_timeout_sec:
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
                raise AuthUserNotFound(f"unknown user: {canonical}", details={"username": canonical})
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
                return None
            self.conn.execute(self._sql_touch_token, (now, token_hash))
            self.conn.commit()
            return row["username"]

    def list_device_tokens(self, username: str) -> list[DeviceTokenSummary]:
        canonical = username.strip().lower() if isinstance(username, str) else ""
        with self._lock:
            rows = self.conn.execute(self._sql_list_tokens, (canonical,)).fetchall()
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
