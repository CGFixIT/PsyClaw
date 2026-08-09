"""Tests for utils/authn_manager.py -- Stage 2 of docs/AUTHENTICATION_DESIGN.md.

AuthManager has no HTTP awareness, so these tests exercise it directly
against a real SQLite DB in tmp_path rather than mocking the store: the
login/lockout/session-expiry logic is exactly the part that must not be
faked out, since a mock would hide the DB round-trip bugs a real backend
would catch.
"""

from __future__ import annotations

import pytest

from utils.authn import PasswordPolicyError
from utils.authn_manager import AuthManager, BOOTSTRAP_USERNAME
from utils.errors import (
    AuthAccountLocked,
    AuthLoginFailed,
    AuthTokenLabelExists,
    AuthUserExists,
    AuthUserNotFound,
)

_GOOD_PASSWORD = "correct horse battery staple"


@pytest.fixture
def manager(tmp_path):
    m = AuthManager({"auth": {"enabled": True, "db_path": str(tmp_path / "auth.db")}})
    yield m
    m.close()


@pytest.fixture
def fast_manager(tmp_path):
    """A manager with a short idle timeout, for expiry tests without a real sleep."""
    m = AuthManager({
        "auth": {
            "enabled": True,
            "db_path": str(tmp_path / "auth_fast.db"),
            "session": {"idle_timeout_sec": 5, "absolute_timeout_sec": 1000},
        }
    })
    yield m
    m.close()


class TestBootstrap:
    def test_bootstrap_creates_the_first_account(self, manager):
        password = manager.bootstrap_if_empty()
        assert password is not None
        assert len(password) >= 20
        users = manager.list_users()
        assert [u.username for u in users] == [BOOTSTRAP_USERNAME]

    def test_bootstrap_is_a_noop_once_any_user_exists(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        assert manager.bootstrap_if_empty() is None
        # Still just the one account -- bootstrap did not also create "admin".
        assert [u.username for u in manager.list_users()] == ["alice"]

    def test_bootstrap_password_actually_logs_in(self, manager):
        password = manager.bootstrap_if_empty()
        result = manager.login(BOOTSTRAP_USERNAME, password)
        assert result.username == BOOTSTRAP_USERNAME

    def test_two_bootstrap_passwords_are_different(self, tmp_path):
        """A fixed default password would be exactly the shortcut
        docs/AUTHENTICATION_DESIGN.md §9/§10 was written to avoid."""
        m1 = AuthManager({"auth": {"enabled": True, "db_path": str(tmp_path / "a.db")}})
        m2 = AuthManager({"auth": {"enabled": True, "db_path": str(tmp_path / "b.db")}})
        try:
            assert m1.bootstrap_if_empty() != m2.bootstrap_if_empty()
        finally:
            m1.close()
            m2.close()


class TestAccounts:
    def test_create_and_list(self, manager):
        canonical = manager.create_user("Alice", _GOOD_PASSWORD)
        assert canonical == "alice"  # canonicalized to lowercase
        users = manager.list_users()
        assert len(users) == 1
        assert users[0].username == "alice"
        assert users[0].disabled is False
        assert users[0].failed_count == 0

    def test_duplicate_create_is_refused(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        with pytest.raises(AuthUserExists):
            manager.create_user("alice", "a different password entirely")

    def test_duplicate_create_is_case_insensitive(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        with pytest.raises(AuthUserExists):
            manager.create_user("Alice", "a different password entirely")

    def test_create_enforces_password_policy(self, manager):
        with pytest.raises(PasswordPolicyError):
            manager.create_user("alice", "short")

    def test_create_enforces_username_policy(self, manager):
        with pytest.raises(PasswordPolicyError):
            manager.create_user("-flag", _GOOD_PASSWORD)

    def test_disable_and_enable(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        manager.disable_user("alice")
        assert manager.list_users()[0].disabled is True
        manager.enable_user("alice")
        assert manager.list_users()[0].disabled is False

    def test_disable_unknown_user_raises(self, manager):
        with pytest.raises(AuthUserNotFound):
            manager.disable_user("nobody")

    def test_disable_revokes_the_users_live_session(self, manager):
        """docs/AUTHENTICATION_DESIGN.md §3 adversary #3: a device that was
        trusted and no longer should be. Disable must kill the session NOW,
        not merely block future logins."""
        manager.create_user("alice", _GOOD_PASSWORD)
        result = manager.login("alice", _GOOD_PASSWORD)
        assert manager.validate_session(result.session_id) is not None
        manager.disable_user("alice")
        assert manager.validate_session(result.session_id) is None

    def test_disable_revokes_the_users_device_tokens(self, manager):
        """Device tokens have no expiry column -- without an explicit
        cascade a disabled user's token would authenticate forever."""
        manager.create_user("alice", _GOOD_PASSWORD)
        token = manager.create_device_token("alice", "laptop")
        assert manager.verify_device_token(token) == "alice"
        manager.disable_user("alice")
        assert manager.verify_device_token(token) is None

    def test_disable_does_not_touch_another_users_session_or_token(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        manager.create_user("bob", "another good password entirely")
        alice_session = manager.login("alice", _GOOD_PASSWORD)
        bob_session = manager.login("bob", "another good password entirely")
        bob_token = manager.create_device_token("bob", "phone")
        manager.disable_user("alice")
        assert manager.validate_session(bob_session.session_id) is not None
        assert manager.verify_device_token(bob_token) == "bob"
        assert manager.validate_session(alice_session.session_id) is None

    def test_re_enabling_does_not_resurrect_a_revoked_session(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        result = manager.login("alice", _GOOD_PASSWORD)
        manager.disable_user("alice")
        manager.enable_user("alice")
        assert manager.validate_session(result.session_id) is None

    def test_disabled_users_session_check_survives_bypassing_the_cascade(self, manager):
        """Defense in depth: even if a session row existed for a disabled
        user WITHOUT going through disable_user()'s cascade (e.g. a future
        code path that forgets to call it, or a row inserted directly),
        validate_session must still refuse it via the users.disabled join."""
        manager.create_user("alice", _GOOD_PASSWORD)
        result = manager.login("alice", _GOOD_PASSWORD)
        with manager._lock:
            manager.conn.execute(manager._sql_set_disabled, (1, "alice"))
            manager.conn.commit()
        assert manager.validate_session(result.session_id) is None

    def test_set_password_then_login_with_new_password(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        manager.set_password("alice", "a brand new password here")
        result = manager.login("alice", "a brand new password here")
        assert result.username == "alice"
        with pytest.raises(AuthLoginFailed):
            manager.login("alice", _GOOD_PASSWORD)

    def test_set_password_unknown_user_raises(self, manager):
        with pytest.raises(AuthUserNotFound):
            manager.set_password("nobody", _GOOD_PASSWORD)

    def test_set_password_revokes_the_users_live_session(self, manager):
        """A password change is a re-authenticate-everywhere signal -- if it
        was prompted by a suspected leak, an old session must not survive."""
        manager.create_user("alice", _GOOD_PASSWORD)
        result = manager.login("alice", _GOOD_PASSWORD)
        manager.set_password("alice", "a completely different password")
        assert manager.validate_session(result.session_id) is None

    def test_set_password_does_not_revoke_device_tokens(self, manager):
        """Device tokens are an independent credential, not derived from the
        password -- a password change alone is not evidence they leaked too."""
        manager.create_user("alice", _GOOD_PASSWORD)
        token = manager.create_device_token("alice", "laptop")
        manager.set_password("alice", "a completely different password")
        assert manager.verify_device_token(token) == "alice"


class TestLogin:
    def test_success_creates_a_session(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        result = manager.login("alice", _GOOD_PASSWORD)
        assert result.username == "alice"
        assert result.session_id
        assert result.csrf_token
        assert result.session_id != result.csrf_token

    def test_wrong_password_raises_login_failed(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        with pytest.raises(AuthLoginFailed):
            manager.login("alice", "definitely the wrong password")

    def test_unknown_username_raises_the_same_error_as_wrong_password(self, manager):
        """Unknown-user and wrong-password must be indistinguishable to the
        caller -- distinguishing them would let a caller enumerate valid
        usernames. Same exception TYPE, same generic message.

        pytest.raises, not try/except: a bare try/except only assigns the
        message INSIDE the except block, so if login() ever stopped raising
        (e.g. a bug let a wrong password through), the test would crash with
        an unrelated NameError on the final assert instead of clearly
        reporting that the expected exception never came.
        """
        manager.create_user("alice", _GOOD_PASSWORD)
        with pytest.raises(AuthLoginFailed) as wrong_password_exc:
            manager.login("alice", "wrong password entirely here")
        with pytest.raises(AuthLoginFailed) as unknown_user_exc:
            manager.login("nosuchuser", "wrong password entirely here")
        assert str(wrong_password_exc.value) == str(unknown_user_exc.value)

    def test_disabled_account_raises_login_failed_not_a_distinct_error(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        manager.disable_user("alice")
        with pytest.raises(AuthLoginFailed):
            manager.login("alice", _GOOD_PASSWORD)

    def test_successful_login_resets_failure_count(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        for _ in range(3):
            with pytest.raises(AuthLoginFailed):
                manager.login("alice", "wrong")
        assert manager.list_users()[0].failed_count == 3
        manager.login("alice", _GOOD_PASSWORD)
        assert manager.list_users()[0].failed_count == 0

    def test_username_is_case_insensitive_at_login(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        result = manager.login("ALICE", _GOOD_PASSWORD)
        assert result.username == "alice"

    @pytest.mark.parametrize("bad", [None, 123, ["a", "b"]])
    def test_non_string_password_fails_closed_without_raising_typeerror(self, manager, bad):
        manager.create_user("alice", _GOOD_PASSWORD)
        with pytest.raises(AuthLoginFailed):
            manager.login("alice", bad)

    def test_login_transparently_rehashes_a_weak_stored_record(self, manager):
        """Raising the work factor later must not force a password reset: a
        login against an outdated record succeeds AND the row is upgraded to
        current parameters on that same login, matching utils/authn.py's
        verify_password contract at the manager level, not just the
        primitive's own return value."""
        import base64
        import hashlib

        salt = b"0123456789abcdef"
        weak_derived = hashlib.scrypt(_GOOD_PASSWORD.encode(), salt=salt, n=2**10, r=8, p=1, dklen=32)
        weak_record = "$".join((
            "scrypt", "1024", "8", "1",
            base64.b64encode(salt).decode(), base64.b64encode(weak_derived).decode(),
        ))
        now = manager._now()
        with manager._lock:
            manager.conn.execute(
                manager._sql_insert_user, ("alice", weak_record, now, 0, None, 0, None)
            )
            manager.conn.commit()

        manager.login("alice", _GOOD_PASSWORD)

        row = manager.conn.execute(manager._sql_get_user, ("alice",)).fetchone()
        assert row["password_hash"] != weak_record
        assert row["password_hash"].split("$")[1] == "16384"  # current _SCRYPT_N
        # And the upgraded record still authenticates.
        result = manager.login("alice", _GOOD_PASSWORD)
        assert result.username == "alice"


class TestLockout:
    def test_locks_after_five_consecutive_failures(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        for _ in range(5):
            with pytest.raises(AuthLoginFailed):
                manager.login("alice", "wrong")
        with pytest.raises(AuthAccountLocked) as excinfo:
            manager.login("alice", _GOOD_PASSWORD)
        assert excinfo.value.retry_after_sec > 0

    def test_correct_password_is_still_refused_while_locked(self, manager):
        """The lockout must not be bypassable by finally guessing right --
        that would make the lockout decorative."""
        manager.create_user("alice", _GOOD_PASSWORD)
        for _ in range(6):
            with pytest.raises((AuthLoginFailed, AuthAccountLocked)):
                manager.login("alice", "wrong")
        with pytest.raises(AuthAccountLocked):
            manager.login("alice", _GOOD_PASSWORD)

    def test_set_password_clears_an_active_lockout(self, manager):
        """docs/AUTHENTICATION_DESIGN.md §9 names the local `cyclaw-user` CLI
        as the mitigation for lockout-as-denial-of-service. login() checks
        is_locked() BEFORE verifying the password, so unless a password reset
        also clears the lockout that mitigation does not actually work -- the
        owner keeps getting 423 with their brand-new correct password until
        the 15-minute ceiling drains."""
        manager.create_user("alice", _GOOD_PASSWORD)
        for _ in range(5):
            with pytest.raises(AuthLoginFailed):
                manager.login("alice", "wrong")
        with pytest.raises(AuthAccountLocked):
            manager.login("alice", _GOOD_PASSWORD)
        manager.set_password("alice", "a completely different password")
        assert manager.login("alice", "a completely different password").username == "alice"

    def test_enable_user_clears_a_lockout_accrued_while_disabled(self, manager):
        """login() rejects a disabled account through the SAME failure-recording
        branch as a wrong password (`if row["disabled"] or not ok:`), so a
        disabled account can accrue its own lockout from attempts made while it
        was disabled. cyclaw-user enable is a deliberate administrative decision
        to make the account usable again NOW -- without clearing the lockout, the
        newly re-enabled account would still return 423 until the ceiling drains,
        the same bug set_password() closes for the password-reset path above."""
        manager.create_user("alice", _GOOD_PASSWORD)
        manager.disable_user("alice")
        for _ in range(5):
            with pytest.raises(AuthLoginFailed):
                manager.login("alice", _GOOD_PASSWORD)
        manager.enable_user("alice")
        assert manager.login("alice", _GOOD_PASSWORD).username == "alice"

    def test_lockout_clears_after_the_delay_elapses(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        for _ in range(5):
            with pytest.raises(AuthLoginFailed):
                manager.login("alice", "wrong")
        real_now = manager._now
        manager._now = lambda: real_now() + 3.0  # past the 2s delay at failure #5
        try:
            result = manager.login("alice", _GOOD_PASSWORD)
            assert result.username == "alice"
        finally:
            manager._now = real_now


class TestSessions:
    def test_validate_session_returns_the_username(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        result = manager.login("alice", _GOOD_PASSWORD)
        info = manager.validate_session(result.session_id)
        assert info is not None
        assert info.username == "alice"
        assert info.csrf_token == result.csrf_token

    def test_unknown_session_id_is_invalid(self, manager):
        assert manager.validate_session("not-a-real-session-id") is None

    @pytest.mark.parametrize("bad", [None, "", 123])
    def test_malformed_session_id_fails_closed(self, manager, bad):
        assert manager.validate_session(bad) is None

    def test_logout_revokes_the_session(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        result = manager.login("alice", _GOOD_PASSWORD)
        assert manager.logout(result.session_id) is True
        assert manager.validate_session(result.session_id) is None

    def test_logout_is_not_an_error_when_already_revoked(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        result = manager.login("alice", _GOOD_PASSWORD)
        manager.logout(result.session_id)
        assert manager.logout(result.session_id) is False

    def test_logout_of_unknown_session_returns_false_not_raise(self, manager):
        assert manager.logout("not-a-real-session-id") is False

    @pytest.mark.parametrize("bad", [None, "", 123])
    def test_logout_of_a_malformed_id_fails_closed_without_a_db_round_trip(self, manager, bad):
        assert manager.logout(bad) is False

    def test_session_expires_after_the_idle_window(self, fast_manager):
        fast_manager.create_user("alice", _GOOD_PASSWORD)
        result = fast_manager.login("alice", _GOOD_PASSWORD)
        real_now = fast_manager._now
        fast_manager._now = lambda: real_now() + 10  # idle_timeout_sec is 5
        try:
            assert fast_manager.validate_session(result.session_id) is None
        finally:
            fast_manager._now = real_now

    def test_an_idle_expired_session_cannot_be_resurrected_by_raising_the_timeout(self, fast_manager):
        """Observing idle expiry must REVOKE the row, not merely refuse the call.

        last_seen_ts is stored per row, but idle_timeout_sec is re-read from
        config on every call -- so without a revoke, a session already seen
        idle-expired under a short timeout becomes valid again the moment the
        operator raises idle_timeout_sec and restarts. The browser still holds
        the cookie, and nothing ever marked the row dead. Raising the timeout
        in place here stands in for that config change plus restart.
        """
        fast_manager.create_user("alice", _GOOD_PASSWORD)
        result = fast_manager.login("alice", _GOOD_PASSWORD)
        real_now = fast_manager._now
        fast_manager._now = lambda: real_now() + 10  # idle_timeout_sec is 5
        try:
            assert fast_manager.validate_session(result.session_id) is None
            # The operator widens the idle window and restarts; last_seen_ts
            # now sits comfortably inside it. The session must stay dead.
            fast_manager.idle_timeout_sec = 100000
            assert fast_manager.validate_session(result.session_id) is None
        finally:
            fast_manager._now = real_now

    def test_valid_use_slides_the_idle_window_forward(self, fast_manager):
        """The idle timeout is a ROLLING window: touching the session inside
        the window must push the deadline forward, not just check it once."""
        fast_manager.create_user("alice", _GOOD_PASSWORD)
        result = fast_manager.login("alice", _GOOD_PASSWORD)
        real_now = fast_manager._now
        # Touch at t+3 (inside the 5s idle window) -- resets last_seen_ts to t+3.
        fast_manager._now = lambda: real_now() + 3
        assert fast_manager.validate_session(result.session_id) is not None
        # Now at t+7: 4s since the touch at t+3, still inside 5s -> still valid.
        fast_manager._now = lambda: real_now() + 7
        try:
            assert fast_manager.validate_session(result.session_id) is not None
        finally:
            fast_manager._now = real_now

    def test_session_expires_at_the_absolute_ceiling_even_with_activity(self, tmp_path):
        """absolute_timeout_sec must fire even if the session is kept alive
        by continuous activity -- otherwise it is not actually an absolute
        ceiling, just a second idle timeout."""
        m = AuthManager({
            "auth": {
                "enabled": True,
                "db_path": str(tmp_path / "abs.db"),
                "session": {"idle_timeout_sec": 100000, "absolute_timeout_sec": 5},
            }
        })
        try:
            m.create_user("alice", _GOOD_PASSWORD)
            result = m.login("alice", _GOOD_PASSWORD)
            real_now = m._now
            m._now = lambda: real_now() + 10  # past the 5s absolute ceiling
            assert m.validate_session(result.session_id) is None
        finally:
            m.close()


class TestDeviceTokens:
    def test_create_and_verify(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        token = manager.create_device_token("alice", "laptop")
        assert manager.verify_device_token(token) == "alice"

    def test_token_for_unknown_user_raises(self, manager):
        with pytest.raises(AuthUserNotFound):
            manager.create_device_token("nobody", "laptop")

    def test_bad_token_verifies_to_none(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        manager.create_device_token("alice", "laptop")
        assert manager.verify_device_token("not-a-real-token-at-all") is None

    @pytest.mark.parametrize("bad", [None, "", 123])
    def test_malformed_token_fails_closed(self, manager, bad):
        assert manager.verify_device_token(bad) is None

    def test_revoked_token_no_longer_verifies(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        token = manager.create_device_token("alice", "laptop")
        assert manager.revoke_device_token("alice", "laptop") is True
        assert manager.verify_device_token(token) is None

    def test_revoking_twice_returns_false_the_second_time(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        manager.create_device_token("alice", "laptop")
        manager.revoke_device_token("alice", "laptop")
        assert manager.revoke_device_token("alice", "laptop") is False

    def test_list_device_tokens_never_exposes_the_token_or_its_hash(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        token = manager.create_device_token("alice", "laptop")
        listed = manager.list_device_tokens("alice")
        assert len(listed) == 1
        assert listed[0].label == "laptop"
        assert listed[0].revoked is False
        rendered = repr(listed[0])
        assert token not in rendered

    def test_a_second_live_token_cannot_reuse_a_label(self, manager):
        """A label is the only handle `cyclaw-user token revoke` offers, and
        it revokes every (username, label) match -- so two live tokens sharing
        a label would both die on one revoke, with no way to target either."""
        manager.create_user("alice", _GOOD_PASSWORD)
        manager.create_device_token("alice", "laptop")
        with pytest.raises(AuthTokenLabelExists):
            manager.create_device_token("alice", "laptop")

    def test_a_label_is_reusable_once_its_token_is_revoked(self, manager):
        """The constraint is on LIVE tokens, not on history: replacing a lost
        device's token with a new one under the same name is the normal case."""
        manager.create_user("alice", _GOOD_PASSWORD)
        first = manager.create_device_token("alice", "laptop")
        manager.revoke_device_token("alice", "laptop")
        second = manager.create_device_token("alice", "laptop")
        assert manager.verify_device_token(first) is None
        assert manager.verify_device_token(second) == "alice"

    def test_two_users_tokens_do_not_collide(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        manager.create_user("bob", "another good password here")
        token_a = manager.create_device_token("alice", "laptop")
        token_b = manager.create_device_token("bob", "laptop")
        assert token_a != token_b
        assert manager.verify_device_token(token_a) == "alice"
        assert manager.verify_device_token(token_b) == "bob"
        # Revoking bob's "laptop" token must not touch alice's identically-labelled one.
        manager.revoke_device_token("bob", "laptop")
        assert manager.verify_device_token(token_a) == "alice"
        assert manager.verify_device_token(token_b) is None
