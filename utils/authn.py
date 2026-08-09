"""Password hashing, token minting, and lockout state for CyClaw's per-user
authentication.

Stage 1 of docs/AUTHENTICATION_DESIGN.md: the pure primitives, with no
database, no HTTP, and no clock of their own. Password verification lives here
because it is what everything above it rests on and deserves to be reviewed on
its own; lockout arithmetic lives here because it is state-machine logic that
should be testable without a database or a clock; the id/token generators live
here because they are one-line wrappers over `secrets` that every layer above
needs and none should re-implement.

Stage 2 builds on this and DOES reach the request path -- utils/authn_store.py
(the account/session store), utils/authn_manager.py (AuthManager), and
gate_auth.py (/auth/login, /auth/logout, /auth/whoami) all sit above it. This
module still imports none of them: the dependency runs one way only, which is
what keeps these primitives testable in isolation. Enforcing a credential on
/query is Stage 3 and has not landed; TLS on the socket is Stage 4.
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
    # Bound the stored parameters against CURRENT policy ceilings before ever
    # calling scrypt. hash_password() only ever writes n/r/p AT-OR-BELOW these
    # constants and dklen fixed at _SCRYPT_DKLEN -- a record can be weaker (that
    # is what needs_rehash exists to upgrade) but this codebase has never
    # written one stronger. A record claiming higher values, or a dklen that
    # does not match, is therefore forged or corrupted, not a legitimate legacy
    # row. Two distinct issues close here:
    #   - A short/truncated `expected` (e.g. dklen=1) previously let
    #     hmac.compare_digest match against a ~1/256-sized search space instead
    #     of the full 32-byte key -- reproduced directly: ~6/2000 wrong
    #     passwords collided against a hand-built dklen=1 record, matching the
    #     predicted rate. Requiring the exact current dklen closes it.
    #   - An inflated stored n/r (e.g. n=2**20) previously drove
    #     maxmem=max(_SCRYPT_MAXMEM, n*r*128*4) toward multiple GiB per attempt
    #     on an unauthenticated route. Capping n/r/p here means the fixed
    #     _SCRYPT_MAXMEM ceiling is always sufficient, so the dynamic expansion
    #     is removed below rather than kept as dead code that still looks
    #     load-bearing.
    if n > _SCRYPT_N or r > _SCRYPT_R or p > _SCRYPT_P or len(expected) != _SCRYPT_DKLEN:
        return (False, False)
    try:
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=_SCRYPT_DKLEN,
            maxmem=_SCRYPT_MAXMEM,
        )
    except (ValueError, MemoryError, OverflowError):
        # scrypt itself rejects n that is not a power of two greater than one
        # and r/p below one -- an explicit pre-check for those was verified
        # redundant in both directions and removed rather than left as dead
        # weight. n/r/p are now bounded above by the check just above, so the
        # OverflowError case (n/r/p crossing into C long territory on LLP64/
        # Windows before scrypt's own ValueError checks run -- confirmed by
        # this suite's own Windows CI leg) is no longer reachable through a
        # forged record; it stays here as defense-in-depth, not as the active
        # mitigation for that class of input anymore.
        #
        # The dklen=0 case is the one worth naming even though the bound check
        # above already rejects any expected length other than _SCRYPT_DKLEN.
        # compare_digest(b"", b"") is True, so a record with an empty stored
        # hash would authenticate any password IF the derived key could ever
        # also be empty -- it cannot, scrypt rejects dklen=0 outright. That is
        # a property of scrypt, not of code written here, which is exactly why
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


def new_csrf_token() -> str:
    """32 random bytes, base64url -- same shape as new_session_id/harness's CSRF
    token, kept as its own name because it is minted per-session, not per-id."""
    return secrets.token_urlsafe(32)


# Bearer device tokens are already high-entropy random values (unlike a
# human-chosen password), so they are stored as a plain SHA-256 hash rather
# than run through scrypt: there is no offline-guessing risk to slow down, and
# scrypt's ~0.1s cost per verification would needlessly tax every bearer-token
# request. This mirrors how GitHub/GitLab store personal access tokens.
def new_device_token() -> str:
    """32 random bytes, base64url. Returned to the caller ONCE at creation time;
    only its hash (see hash_token) is ever stored."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest of a bearer token, for at-rest storage and lookup.

    Unlike password verification, this is a plain hash-and-look-up rather than
    an ``hmac.compare_digest`` comparison: the token itself is 32 random bytes
    (256 bits of entropy), so the thing protecting it is the difficulty of
    guessing it in the first place, not the constant-time-ness of comparing a
    hash the caller must already possess the preimage of. This is the same
    approach GitHub/GitLab use for personal access tokens.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# 18 raw bytes -> 24 base64url chars: comfortably above _MIN_PASSWORD_LEN (12).
_BOOTSTRAP_PASSWORD_BYTES = 18


def generate_bootstrap_password() -> str:
    """A random secret seeding the first-run account's UNUSABLE placeholder hash.

    The caller (AuthManager.bootstrap_if_empty) hashes this and discards it --
    it is never returned to an operator, printed, logged, or stored in
    plaintext, so the bootstrap account cannot be logged into until
    `cyclaw-user passwd` sets a real password locally via getpass. This
    exists so CyClaw never ships a fixed, guessable default credential
    (docs/AUTHENTICATION_DESIGN.md §9/§10): every install's placeholder is
    unique and generated locally, on that machine, at first boot. (An earlier
    design returned this for a print-once console banner; CodeQL alert #1057
    flagged that, and the discard design replaced it -- a service's stdout is
    persisted by journald/Docker/log shippers, so "shown once" was never
    really once.)
    """
    return secrets.token_urlsafe(_BOOTSTRAP_PASSWORD_BYTES)
