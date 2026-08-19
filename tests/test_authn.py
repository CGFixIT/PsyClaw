"""Tests for utils/authn.py — Stage 1 of docs/AUTHENTICATION_DESIGN.md.

Nothing in the request path imports this module yet, so these tests are the only
thing exercising it. They are written accordingly: the password primitive and
the lockout arithmetic are the two things everything above them will rest on.
"""

from __future__ import annotations

import base64
import hashlib
import time

import pytest

from utils.authn import (
    PasswordPolicyError,
    hash_password,
    is_locked,
    lockout_delay_sec,
    new_session_id,
    next_lock_until,
    validate_password,
    validate_username,
    verify_password,
)

_GOOD = "correct horse battery staple"


class TestHashAndVerify:
    def test_roundtrip(self):
        assert verify_password(_GOOD, hash_password(_GOOD)) == (True, False)

    def test_wrong_password_rejected(self):
        ok, _ = verify_password("not the password at all", hash_password(_GOOD))
        assert ok is False

    def test_salt_makes_identical_passwords_hash_differently(self):
        """Without a per-record salt, two users with the same password share a
        hash — which leaks that fact to anyone who reads the table, and lets one
        cracked hash open both accounts."""
        assert hash_password(_GOOD) != hash_password(_GOOD)

    def test_record_is_self_describing(self):
        algo, n, r, p, salt_b64, hash_b64 = hash_password(_GOOD).split("$")
        assert algo == "scrypt"
        assert int(n) >= 2**17 and int(r) >= 8 and int(p) >= 1
        # Parameters live in the record so they can be raised later without
        # invalidating existing accounts.
        assert len(base64.b64decode(salt_b64)) == 16
        assert len(base64.b64decode(hash_b64)) == 32

    @pytest.mark.parametrize(
        "record",
        [
            "",
            "not-a-record",
            "scrypt$16384$8$1$onlyfivefields",
            "bcrypt$16384$8$1$c2FsdA==$aGFzaA==",       # unknown algorithm
            "scrypt$notanint$8$1$c2FsdA==$aGFzaA==",
            "scrypt$16384$8$1$!!!notbase64!!!$aGFzaA==",
            "scrypt$0$8$1$c2FsdA==$aGFzaA==",           # n must be > 1
            "scrypt$16385$8$1$c2FsdA==$aGFzaA==",       # n must be a power of two
            "scrypt$16384$8$1$$aGFzaA==",               # empty salt
            # An empty stored HASH is the dangerous one: without scrypt raising
            # on dklen=0 it would reach compare_digest(b"", b""), which is True
            # and would authenticate any password at all.
            "scrypt$16384$8$1$c2FsdA==$",
        ],
    )
    def test_malformed_record_fails_closed_without_raising(self, record):
        """This runs on an unauthenticated route. A corrupt row must fail the
        login, not 500 the request — a 500 would confirm the row exists at all,
        and a raise from inside hashlib would do exactly that."""
        assert verify_password(_GOOD, record) == (False, False)

    def test_absurd_parameters_do_not_become_a_memory_lever(self):
        """A record claiming a huge n must not let an unauthenticated caller
        allocate gigabytes. scrypt raises rather than allocating; we swallow it."""
        record = f"scrypt${2**40}$8$1$c2FsdA==$aGFzaA=="
        assert verify_password(_GOOD, record) == (False, False)

    @pytest.mark.parametrize("bad", [None, 123, b"bytes"])
    def test_non_string_inputs_fail_closed(self, bad):
        assert verify_password(bad, hash_password(_GOOD)) == (False, False)
        assert verify_password(_GOOD, bad) == (False, False)

    def test_truncated_hash_fails_closed_even_for_the_correct_password(self):
        """A record whose stored hash is shorter than current policy's dklen
        must be rejected outright, not merely resistant to guessing. Before
        this check existed, verify_password used ``dklen=len(expected)`` --
        so a record truncated to e.g. 1 byte made hmac.compare_digest match
        against a ~1/256-sized search space instead of the full 32-byte key.
        The correct password against such a record must now fail closed."""
        salt = b"0123456789abcdef"
        derived = hashlib.scrypt(_GOOD.encode(), salt=salt, n=2**14, r=8, p=1, dklen=1)
        record = "$".join(
            ("scrypt", "16384", "8", "1", base64.b64encode(salt).decode(), base64.b64encode(derived).decode())
        )
        assert verify_password(_GOOD, record) == (False, False)

    def test_parameters_above_current_policy_fail_closed_even_for_the_correct_password(self):
        """A record claiming n/r/p ABOVE current policy must be rejected
        outright, not merely resistant to guessing -- hash_password() never
        writes ahead of current policy, so a stronger-than-policy record is
        forged or corrupted, and unbounded n/r previously let
        maxmem=max(_SCRYPT_MAXMEM, n*r*128*4) grow with the stored record."""
        salt = b"0123456789abcdef"
        derived = hashlib.scrypt(_GOOD.encode(), salt=salt, n=2**18, r=8, p=1, dklen=32, maxmem=2**30)
        record = "$".join(
            ("scrypt", str(2**18), "8", "1", base64.b64encode(salt).decode(), base64.b64encode(derived).decode())
        )
        assert verify_password(_GOOD, record) == (False, False)

    def test_weaker_stored_parameters_request_a_rehash(self):
        """Raising the work factor later must not force a password reset: a
        login against an outdated record succeeds AND reports needs_rehash."""
        # A deliberately weak but VALID record, built by hand.
        salt = b"0123456789abcdef"
        derived = hashlib.scrypt(_GOOD.encode(), salt=salt, n=2**10, r=8, p=1, dklen=32)
        record = "$".join(
            ("scrypt", "1024", "8", "1", base64.b64encode(salt).decode(), base64.b64encode(derived).decode())
        )
        assert verify_password(_GOOD, record) == (True, True)
        # ...and a wrong password against the same weak record still fails.
        assert verify_password("wrong wrong wrong wrong", record) == (False, False)


class TestPolicy:
    @pytest.mark.parametrize("name", ["operator", "cg", "a", "user.one", "user_1", "u-2", "9lives"])
    def test_valid_usernames(self, name):
        assert validate_username(name) == name

    def test_username_is_canonicalised_to_lowercase(self):
        """'Operator' and 'operator' must not become two rows that look
        identical in an audit log."""
        assert validate_username("  Operator  ") == "operator"

    @pytest.mark.parametrize(
        "name",
        ["", "-flag", ".hidden", "..evil", "has space", "toolong" * 10, "sym!bol", "-"],
    )
    def test_rejected_usernames(self, name):
        # A leading '-' is an argv-flag shape and a leading '.' is a path shape;
        # this value gets printed, logged, and used as a database key.
        with pytest.raises(PasswordPolicyError):
            validate_username(name)

    def test_short_password_rejected(self):
        with pytest.raises(PasswordPolicyError):
            validate_password("short")

    def test_oversized_password_rejected(self):
        """Unbounded input on an unauthenticated route is an unbounded allocation."""
        with pytest.raises(PasswordPolicyError):
            validate_password("x" * 2000)

    def test_hash_password_enforces_the_policy(self):
        with pytest.raises(PasswordPolicyError):
            hash_password("tooshort")


class TestLockout:
    def test_no_delay_below_the_threshold(self):
        assert [lockout_delay_sec(i) for i in range(5)] == [0.0] * 5

    def test_backoff_doubles_then_flattens_at_the_ceiling(self):
        assert lockout_delay_sec(5) == 2.0
        assert lockout_delay_sec(6) == 4.0
        assert lockout_delay_sec(7) == 8.0
        assert lockout_delay_sec(20) == 900.0

    def test_ceiling_is_not_a_permanent_lock(self):
        """A permanent lock hands anyone who merely knows a username a
        denial-of-service against its owner. The ceiling is the mitigation."""
        assert lockout_delay_sec(10_000) == 900.0

    def test_huge_failure_count_does_not_overflow(self):
        """failed_count is attacker-influenced; 2.0 ** 10_000 raises OverflowError."""
        assert lockout_delay_sec(10**9) == 900.0

    def test_is_locked_uses_the_injected_clock(self):
        # Injectable so the suite needs no real sleep (CLAUDE.md: deterministic,
        # no sleep racing a timeout).
        assert is_locked(1000.0, now=999.0) is True
        assert is_locked(1000.0, now=1000.0) is False
        assert is_locked(1000.0, now=1001.0) is False

    @pytest.mark.parametrize("empty", [None, 0, 0.0])
    def test_unset_lock_is_not_locked(self, empty):
        assert is_locked(empty) is False

    def test_next_lock_until_is_now_plus_the_delay(self):
        assert next_lock_until(5, now=100.0) == 102.0
        assert next_lock_until(0, now=100.0) == 100.0

    def test_next_lock_until_defaults_to_the_real_clock(self):
        before = time.time()
        got = next_lock_until(5)
        assert before + 2.0 <= got <= time.time() + 2.0


class TestSessionId:
    def test_ids_are_unique_and_urlsafe(self):
        ids = {new_session_id() for _ in range(200)}
        assert len(ids) == 200
        assert all(set(i) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_") for i in ids)

    def test_id_is_long_enough_to_be_unguessable(self):
        # 32 random bytes -> ~43 base64url chars, the same primitive and size the
        # harness console already uses for its per-process CSRF token.
        assert len(new_session_id()) >= 43


def test_verification_uses_a_timing_safe_compare():
    """A timing leak cannot be caught by behaviour, so pin it at the source.

    A plain `derived == expected` returns as soon as it hits a differing byte,
    leaking the derived key's prefix through response timing on an
    unauthenticated route. gate.py's require_api_key uses compare_digest for
    exactly this reason; so must this. Source-level assertion mirrors how
    test_terminal_contract.py pins console behaviour it cannot execute.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "utils" / "authn.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest(derived, expected)" in src
    assert "derived == expected" not in src
