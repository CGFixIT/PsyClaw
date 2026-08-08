"""Password hashing and lockout state for CyClaw's per-user authentication.

Stage 1 of docs/AUTHENTICATION_DESIGN.md. This module is deliberately inert:
nothing in the request path imports it yet, so it can land, be reviewed, and be
tested without changing how a single route behaves.

Two things live here and nothing else. Password verification, because that is
the primitive everything above it rests on and it deserves to be reviewed on its
own. And lockout arithmetic, because it is pure state-machine logic that should
be testable without a database, an HTTP client, or a clock.

The account/session STORE (SQLite via utils/personality_db's pattern) is Stage 1's
other half and lands beside this; sessions, routes, cookies and TLS are Stages
2-4. See the design doc for why the split is shaped this way.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time

# scrypt (RFC 7914), from the standard library. Chosen over argon2id because
# argon2-cffi would be a new RUNTIME dependency -- High-tier under CLAUDE.md §7,
# needing exact pins in both pyproject.toml and constraints.txt plus a dep-guard
# pass -- and stdlib-first is this repo's stated convention. argon2id is the
# stronger primitive in the abstract; at these parameters scrypt is not the weak
# link in this system, and the dependency cost is real.
#
# n=2**14, r=8, p=1 costs ~0.11s and ~16 MiB per verification here. That is the
# right order for an interactive login: slow enough to make offline cracking
# expensive, fast enough that a human does not notice, and memory-hard so GPU
# arrays lose most of their advantage.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16
# scrypt's own guard: it refuses parameters whose memory cost exceeds maxmem.
# 2**14 * 8 * 128 is ~16 MiB; the headroom multiplier keeps a future parameter
# bump from failing on an off-by-a-factor rather than on a deliberate decision.
_SCRYPT_MAXMEM = _SCRYPT_N * _SCRYPT_R * 128 * 4

_ALGO = "scrypt"
_FIELD_SEP = "$"
# Parameters are stored IN the record so they can be raised later without
# invalidating existing accounts: verify_password reports when a stored record
# used weaker parameters than current policy, and the caller re-hashes on the
# next successful login.
_RECORD_FIELDS = 6

# Usernames are an identity, an audit-log value, and a database key. Anchored to
# a conservative slug for the same reason agentic/registry.py anchors skill
# names: a leading '-' is an argv-flag shape and a leading '.' is a path shape,
# and this value will eventually be printed, logged, and compared.
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")

# Lockout: exponential backoff with a ceiling, per ACCOUNT rather than per IP.
# The existing per-IP limiter (gate.py's _enforce_rate_limit, which already runs
# BEFORE auth) throttles one source; this stops a distributed guess against one
# account, which is the shape the LAN case actually creates.
_LOCKOUT_THRESHOLD = 5  # consecutive failures before any delay is imposed
_LOCKOUT_BASE_SEC = 2.0
_LOCKOUT_CEILING_SEC = 900.0  # 15 min. A ceiling, not a permanent lock -- see
# the design doc §9: a permanent lock hands an attacker who merely knows a
# username a denial-of-service against its owner.


class PasswordPolicyError(ValueError):
    """A password or username was rejected before hashing."""


def validate_username(username: str) -> str:
    """Return the canonical form, or raise.

    Lowercased before the pattern check so 'Operator' and 'operator' can never
    become two accounts that look identical in an audit log.
    """
    if not isinstance(username, str):
        raise PasswordPolicyError("username must be a string")
    canonical = username.strip().lower()
    if not _USERNAME_RE.match(canonical):
        raise PasswordPolicyError(
            "username must be 1-32 chars, start alphanumeric, and contain only "
            "a-z 0-9 . _ -"
        )
    return canonical


# 12 rather than 8: this credential may be reachable from every device on a LAN
# and there is no MFA behind it. Length is the only knob that reliably helps
# against offline attack once the hash is memory-hard, and a composition rule
# ("one upper, one digit, one symbol") mostly produces P@ssw0rd1 -- so length is
# enforced and composition deliberately is not.
_MIN_PASSWORD_LEN = 12
# scrypt itself has no input length limit, but an unbounded password is an
# unbounded allocation on an unauthenticated route.
_MAX_PASSWORD_LEN = 1024


def validate_password(password: str) -> str:
    if not isinstance(password, str):
        raise PasswordPolicyError("password must be a string")
    if len(password) < _MIN_PASSWORD_LEN:
        raise PasswordPolicyError(f"password must be at least {_MIN_PASSWORD_LEN} characters")
    if len(password) > _MAX_PASSWORD_LEN:
        raise PasswordPolicyError(f"password must be at most {_MAX_PASSWORD_LEN} characters")
    return password


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Return a self-describing record: ``scrypt$n$r$p$salt_b64$hash_b64``.

    ``salt`` is a parameter only so tests can pin a known vector; production
    callers must let it default to os.urandom.
    """
    validate_password(password)
    if salt is None:
        salt = os.urandom(_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return _FIELD_SEP.join(
        (_ALGO, str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P), _b64(salt), _b64(derived))
    )


def verify_password(password: str, record: str) -> tuple[bool, bool]:
    """Return ``(ok, needs_rehash)``.

    ``needs_rehash`` is True when the stored record used weaker parameters than
    current policy, so a caller can transparently upgrade it on a successful
    login rather than forcing a password reset.

    A malformed or unknown-algorithm record returns ``(False, False)`` instead of
    raising: this runs on an unauthenticated path, and a corrupt row must fail
    the login rather than 500 the route and disclose that the row exists at all.
    The comparison is ``hmac.compare_digest`` on bytes -- a plain ``==`` on the
    derived key leaks its prefix through response timing.
    """
    if not isinstance(password, str) or not isinstance(record, str):
        return (False, False)
    parts = record.split(_FIELD_SEP)
    if len(parts) != _RECORD_FIELDS or parts[0] != _ALGO:
        return (False, False)
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, expected = _unb64(parts[4]), _unb64(parts[5])
    except (ValueError, TypeError, base64.binascii.Error):
        return (False, False)
    try:
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=max(_SCRYPT_MAXMEM, n * r * 128 * 4),
        )
    except (ValueError, MemoryError):
        # This is the ONLY validity check on the stored parameters, deliberately.
        # scrypt itself rejects n that is not a power of two greater than one,
        # r/p below one, and dklen of zero -- so an explicit pre-check ahead of
        # this was verified redundant in both directions and removed rather than
        # left as dead weight. It also stops a record claiming an absurd n from
        # becoming a memory-exhaustion lever on an unauthenticated route.
        #
        # The dklen=0 case is the one worth naming. compare_digest(b"", b"") is
        # True, so a record with an empty stored hash would authenticate any
        # password IF the derived key could ever also be empty. It cannot:
        # scrypt rejects dklen=0 outright, so that comparison is unreachable
        # rather than merely unlikely. That is a property of scrypt, not of code
        # written here, which is exactly why
        # test_malformed_record_fails_closed_without_raising pins the outcome
        # instead of trusting the reasoning.
        return (False, False)
    ok = hmac.compare_digest(derived, expected)
    needs_rehash = ok and (n < _SCRYPT_N or r < _SCRYPT_R or p < _SCRYPT_P)
    return (ok, needs_rehash)


def lockout_delay_sec(failed_count: int) -> float:
    """Seconds an account must wait after ``failed_count`` consecutive failures.

    Zero below the threshold, then doubling, then flat at the ceiling. Pure
    arithmetic so it can be tested without a clock or a database.
    """
    if failed_count < _LOCKOUT_THRESHOLD:
        return 0.0
    over = failed_count - _LOCKOUT_THRESHOLD
    # Cap the exponent before computing the power: 2.0 ** 10_000 raises
    # OverflowError, and failed_count is attacker-influenced.
    max_doublings = 32
    delay = _LOCKOUT_BASE_SEC * (2.0 ** min(over, max_doublings))
    return min(delay, _LOCKOUT_CEILING_SEC)


def is_locked(locked_until_ts: float | None, *, now: float | None = None) -> bool:
    """True while a lock is in force. ``now`` is injectable so tests need no sleep."""
    if not locked_until_ts:
        return False
    current = time.time() if now is None else now
    return current < float(locked_until_ts)


def next_lock_until(failed_count: int, *, now: float | None = None) -> float:
    """Absolute timestamp the account stays locked until, given the new count."""
    current = time.time() if now is None else now
    return current + lockout_delay_sec(failed_count)


def new_session_id() -> str:
    """32 random bytes, base64url. Same primitive and size the harness console
    already uses for its per-process CSRF token."""
    return secrets.token_urlsafe(32)
