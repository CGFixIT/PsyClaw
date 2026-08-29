"""Tests for utils/authn_manager.py -- Stage 2 of docs/AUTHENTICATION_DESIGN.md.

AuthManager has no HTTP awareness, so these tests exercise it directly
against a real SQLite DB in tmp_path rather than mocking the store: the
login/lockout/session-expiry logic is exactly the part that must not be
faked out, since a mock would hide the DB round-trip bugs a real backend
would catch.
"""

from __future__ import annotations

import importlib

import pytest

from utils.authn import PasswordPolicyError, hash_token
from utils.authn_manager import AuthManager, BOOTSTRAP_USERNAME, _dummy_record
from utils.errors import (
    AuthAccountLocked,
    AuthBootstrapComplete,
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
        # `is True`, not truthiness: bootstrap_if_empty used to return the
        # plaintext password (a non-empty str, also truthy), and returning
        # the secret again is exactly the regression this pins against --
        # CodeQL alert #1057 (clear-text logging of sensitive data) was the
        # print-once banner built on that return value.
        assert manager.bootstrap_if_empty() is True
        users = manager.list_users()
        assert [u.username for u in users] == [BOOTSTRAP_USERNAME]

    def test_bootstrap_is_a_noop_once_any_user_exists(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        assert manager.bootstrap_if_empty() is False
        # Still just the one account -- bootstrap did not also create "admin".
        assert [u.username for u in manager.list_users()] == ["alice"]

    def test_bootstrap_hash_is_marked_pending(self, manager):
        assert manager.bootstrap_if_empty() is True
        assert manager.needs_password_setup() is True
        row = manager.get_user(BOOTSTRAP_USERNAME)
        assert row is not None

    def test_bootstrap_set_password_then_login_works(self, manager):
        assert manager.bootstrap_if_empty() is True
        result = manager.bootstrap_set_password(_GOOD_PASSWORD)
        assert result.username == BOOTSTRAP_USERNAME
        assert manager.needs_password_setup() is False
        with pytest.raises(AuthBootstrapComplete):
            manager.bootstrap_set_password(_GOOD_PASSWORD)
        assert manager.login(BOOTSTRAP_USERNAME, _GOOD_PASSWORD).username == BOOTSTRAP_USERNAME

    def test_bootstrap_set_password_claim_loses_to_a_second_manager(self, tmp_path, monkeypatch):
        """Two AuthManagers (gateway vs harness, or HTTP vs CLI) both see
        pending. Username-only UPDATE let the later writer overwrite the
        password and revoke the first session. The claim matches the pending
        hash, so the loser is AuthBootstrapComplete and the first session
        stays live.

        Hashing is stubbed so this pins the SQL race, not scrypt cost.
        """
        monkeypatch.setattr(
            "utils.authn.hash_pending_placeholder",
            lambda: "pending$scrypt$placeholder",
        )
        monkeypatch.setattr(
            "utils.authn.hash_password",
            lambda password, salt=None: "scrypt$claimed$" + password.replace(" ", "_"),
        )
        monkeypatch.setattr(
            "utils.authn.is_pending_password_record",
            lambda rec: isinstance(rec, str) and rec.startswith("pending$"),
        )
        db_path = str(tmp_path / "claim.db")
        first = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
        second = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
        other = "a different password entirely"
        try:
            assert first.bootstrap_if_empty() is True
            won = first.bootstrap_set_password(_GOOD_PASSWORD)
            with pytest.raises(AuthBootstrapComplete):
                second.bootstrap_set_password(other)
            row = first.conn.execute(first._sql_get_user, (BOOTSTRAP_USERNAME,)).fetchone()
            assert row["password_hash"] == "scrypt$claimed$correct_horse_battery_staple"
            assert first.validate_session(won.session_id) is not None
        finally:
            first.close()
            second.close()

    def test_bootstrap_if_empty_unique_violation_is_a_noop(self, tmp_path, monkeypatch):
        """If the empty-table COUNT is stale (the other process already
        inserted admin), INSERT hits the username PK. That must return False
        rather than raising, so harness/gateway startup does not abort."""
        monkeypatch.setattr(
            "utils.authn.hash_pending_placeholder",
            lambda: "pending$scrypt$placeholder",
        )
        db_path = str(tmp_path / "boot-race.db")
        first = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
        second = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
        try:
            assert first.bootstrap_if_empty() is True
            second._sql_count_users = "SELECT 0 AS n"
            assert second.bootstrap_if_empty() is False
            assert [u.username for u in first.list_users()] == [BOOTSTRAP_USERNAME]
        finally:
            first.close()
            second.close()

    def test_bootstrap_account_is_unusable_until_a_password_is_set(self, manager):
        """The placeholder hash is of a secret that was discarded inside
        bootstrap_if_empty -- nobody, operator included, can log in as admin
        until `cyclaw-user passwd admin` sets a real password. This is the
        design that keeps any credential off stdout/logs entirely (CodeQL
        #1057): there is no secret to disclose because none survives the
        call. Obvious guesses must fail like any wrong password."""
        assert manager.bootstrap_if_empty() is True
        for guess in ("", "admin", "password", "cyclaw", BOOTSTRAP_USERNAME):
            with pytest.raises(AuthLoginFailed):
                manager.login(BOOTSTRAP_USERNAME, guess)

    def test_bootstrap_account_works_after_passwd_sets_a_real_password(self, manager):
        """`cyclaw-user passwd admin` (which calls set_password) is the ONLY
        path that makes the bootstrap account usable -- the same local-only
        recovery path docs/AUTHENTICATION_DESIGN.md §9 already relies on."""
        assert manager.bootstrap_if_empty() is True
        manager.set_password(BOOTSTRAP_USERNAME, _GOOD_PASSWORD)
        assert manager.login(BOOTSTRAP_USERNAME, _GOOD_PASSWORD).username == BOOTSTRAP_USERNAME


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

    def test_raced_duplicate_create_is_still_auth_user_exists(self, manager):
        # The pre-check and the INSERT are not one atomic step, and the lock
        # guarding them orders only threads sharing this manager -- gateway,
        # harness and the CLI each hold their own. Neutering the pre-check SELECT
        # is what a writer committing in that window looks like from in here, and
        # it leaves the DB constraint as the only remaining defence. Before the
        # fix this raised the backend's raw error (sqlite3.IntegrityError, or
        # psycopg's UniqueViolation) instead, which is not an AuthError, so
        # gate_auth 500'd it and the CLI exited 1 rather than its documented 2.
        manager.create_user("alice", _GOOD_PASSWORD)
        manager._sql_get_user += " AND 0 = 1"
        with pytest.raises(AuthUserExists):
            manager.create_user("alice", "a different password entirely")

    def test_raced_duplicate_create_leaves_the_connection_usable(self, manager):
        # The rollback() in the handler is what keeps this true on Postgres,
        # where connect() sets autocommit=False and a failed INSERT leaves the
        # long-lived connection raising InFailedSqlTransaction until something
        # else clears it. SQLite has no equivalent sticky state, so this asserts
        # the contract rather than reproducing the Postgres symptom.
        manager.create_user("alice", _GOOD_PASSWORD)
        real_sql = manager._sql_get_user
        manager._sql_get_user = real_sql + " AND 0 = 1"
        with pytest.raises(AuthUserExists):
            manager.create_user("alice", "a different password entirely")
        manager._sql_get_user = real_sql
        assert manager.get_user("alice") is not None
        assert manager.create_user("bob", _GOOD_PASSWORD) == "bob"

    def test_raced_duplicate_token_label_is_still_auth_token_label_exists(self, manager):
        # create_device_token has the identical check-then-act shape, against
        # the partial unique index idx_device_tokens_live_label rather than the
        # users PK.
        manager.create_user("alice", _GOOD_PASSWORD)
        manager.create_device_token("alice", "laptop")
        manager._sql_get_live_token_by_label += " AND 0 = 1"
        with pytest.raises(AuthTokenLabelExists):
            manager.create_device_token("alice", "laptop")

    def test_duplicate_create_closes_its_read_transaction(self, manager, monkeypatch):
        """create_user()'s existing-user rejection used to raise AuthUserExists
        without closing the implicit transaction its existence-check SELECT
        opened (Postgres, autocommit=False -- see _end_read_txn's docstring),
        leaving the long-lived server connection idle-in-transaction after
        every rejected `cyclaw-user add` of an already-existing username. Same
        class of gap as login()'s unknown-username/locked-account paths."""
        manager.create_user("alice", _GOOD_PASSWORD)
        calls = []
        monkeypatch.setattr(manager, "_end_read_txn", lambda: calls.append(1))
        with pytest.raises(AuthUserExists):
            manager.create_user("alice", "a different password entirely")
        assert calls == [1]

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
                manager._sql_insert_user, ("alice", weak_record, now, 0, None, 0, None, "operator")
            )
            manager.conn.commit()

        manager.login("alice", _GOOD_PASSWORD)

        row = manager.conn.execute(manager._sql_get_user, ("alice",)).fetchone()
        assert row["password_hash"] != weak_record
        assert row["password_hash"].split("$")[1] == "131072"  # current _SCRYPT_N (2**17)
        # And the upgraded record still authenticates.
        result = manager.login("alice", _GOOD_PASSWORD)
        assert result.username == "alice"


class TestLoginTransactionAndRaceSafety:
    def test_unknown_username_closes_its_read_transaction(self, manager, monkeypatch):
        """login()'s row-is-None path used to raise AuthLoginFailed without
        ever closing the implicit transaction the lookup SELECT opened
        (Postgres, autocommit=False -- see _end_read_txn's docstring): the
        long-lived server connection was left idle-in-transaction after every
        rejected unknown-username attempt. Pinned at the manager level (not
        just Postgres) since _end_read_txn is a no-op-but-still-called
        contract on SQLite too."""
        calls = []
        monkeypatch.setattr(manager, "_end_read_txn", lambda: calls.append(1))
        with pytest.raises(AuthLoginFailed):
            manager.login("nosuchuser", "whatever password")
        assert calls == [1]

    def test_locked_account_closes_its_read_transaction(self, manager, monkeypatch):
        """Same gap as above, on the is_locked() exit path.

        Threshold-5 lockout is only _LOCKOUT_BASE_SEC (2.0s). login() snapshots
        manager._now() before scrypt; on Windows CI one hash can exceed that
        window, so the sixth call sees an expired lock. Freeze the clock so
        this asserts the is_locked() txn close, not hasher wall time.
        """
        frozen = manager._now()
        monkeypatch.setattr(manager, "_now", lambda: frozen)
        manager.create_user("alice", _GOOD_PASSWORD)
        for _ in range(5):
            with pytest.raises(AuthLoginFailed):
                manager.login("alice", "wrong")
        calls = []
        monkeypatch.setattr(manager, "_end_read_txn", lambda: calls.append(1))
        with pytest.raises(AuthAccountLocked):
            manager.login("alice", _GOOD_PASSWORD)
        assert calls == [1]

    def test_password_rotated_mid_verification_does_not_mint_a_session(self, tmp_path, monkeypatch):
        """TOCTOU: verify_password() costs ~0.1s of real scrypt work, and
        AuthManager._lock only serializes calls made through THIS instance.
        A SEPARATE process -- exactly what `cyclaw-user passwd` is -- opens
        its own AuthManager and its own DB connection, so this instance's
        lock does nothing to order against it. A login that already read the
        OLD row into memory before that rotation committed must not go on to
        mint a session for a password that, by the time the session is
        actually created, is no longer the account's real password.

        The race is simulated deterministically (no real threads/timing):
        authn.verify_password is patched to perform the concurrent rotation,
        via a second AuthManager sharing the same SQLite file, as a side
        effect of the FIRST real verification call inside login().
        """
        db_path = str(tmp_path / "race.db")
        manager_a = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
        manager_b = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
        try:
            manager_a.create_user("alice", _GOOD_PASSWORD)
            real_verify = importlib.import_module("utils.authn").verify_password
            rotated = {"done": False}

            def racing_verify(password, record):
                if not rotated["done"] and record != _dummy_record():
                    rotated["done"] = True
                    manager_b.set_password("alice", "a brand new password entirely")
                return real_verify(password, record)

            monkeypatch.setattr("utils.authn_manager.authn.verify_password", racing_verify)

            with pytest.raises(AuthLoginFailed):
                manager_a.login("alice", _GOOD_PASSWORD)

            # The account is not left unusable -- the NEW password, the one
            # that actually won the race, logs in normally afterward.
            assert manager_a.login("alice", "a brand new password entirely").username == "alice"
        finally:
            manager_a.close()
            manager_b.close()

    def test_account_disabled_mid_verification_does_not_mint_a_session(self, tmp_path, monkeypatch):
        """Same race, via disable_user() instead of a password rotation --
        the other half of the "revoke NOW, not eventually" promise
        _set_disabled()'s own comment makes for already-issued credentials."""
        db_path = str(tmp_path / "race2.db")
        manager_a = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
        manager_b = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
        try:
            manager_a.create_user("alice", _GOOD_PASSWORD)
            real_verify = importlib.import_module("utils.authn").verify_password
            disabled = {"done": False}

            def racing_verify(password, record):
                if not disabled["done"] and record != _dummy_record():
                    disabled["done"] = True
                    manager_b.disable_user("alice")
                return real_verify(password, record)

            monkeypatch.setattr("utils.authn_manager.authn.verify_password", racing_verify)

            with pytest.raises(AuthLoginFailed):
                manager_a.login("alice", _GOOD_PASSWORD)
        finally:
            manager_a.close()
            manager_b.close()

    def test_account_locked_by_a_concurrent_process_mid_verification_does_not_mint_a_session(
        self, tmp_path, monkeypatch
    ):
        """A THIRD variant of the same race: the account gets locked out by a
        concurrent process's failed attempts (not this login's own -- those
        never reach _record_failure_locked, since this one is using the
        correct password) while THIS login's verify_password() is still
        running. The claim's WHERE clause re-checks locked_until_ts, not
        just password_hash/disabled, so this must fail closed too."""
        db_path = str(tmp_path / "race4.db")
        manager_a = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
        manager_b = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
        try:
            manager_a.create_user("alice", _GOOD_PASSWORD)
            real_verify = importlib.import_module("utils.authn").verify_password
            locked = {"done": False}

            def racing_verify(password, record):
                if not locked["done"] and record != _dummy_record():
                    locked["done"] = True
                    # Five concurrent wrong-password attempts from a
                    # separate process/connection -- enough to trip the
                    # lockout ceiling on their own.
                    for _ in range(5):
                        with pytest.raises(AuthLoginFailed):
                            manager_b.login("alice", "wrong wrong wrong")
                return real_verify(password, record)

            monkeypatch.setattr("utils.authn_manager.authn.verify_password", racing_verify)

            with pytest.raises((AuthLoginFailed, AuthAccountLocked)):
                manager_a.login("alice", _GOOD_PASSWORD)
        finally:
            manager_a.close()
            manager_b.close()

    def test_claim_gates_on_the_exact_hash_that_was_verified_not_just_the_username(self, manager):
        """Direct unit test of the CAS claim's WHERE clause, no race
        simulation needed: mutate the stored hash out from under a manager
        that already has a stale `row` in hand, and confirm the claim -- not
        a subsequent write -- is what blocks the session."""
        manager.create_user("alice", _GOOD_PASSWORD)
        row = manager.conn.execute(manager._sql_get_user, ("alice",)).fetchone()
        stale_hash = row["password_hash"]

        # Simulate "the row changed after this hash was captured" directly,
        # without going through set_password (which would also revoke
        # sessions -- irrelevant here, this is testing the claim itself).
        manager.conn.execute(manager._sql_set_password, ("scrypt$1$1$1$AA==$AA==", "alice"))
        manager.conn.commit()

        claimed = manager.conn.execute(
            manager._sql_claim_login, (manager._now(), "alice", stale_hash, manager._now())
        )
        assert claimed.rowcount == 0
        manager.conn.commit()

    def test_stale_recheck_does_not_count_toward_lockout(self, tmp_path, monkeypatch):
        """The recheck failure is "the account changed out from under us",
        not "a wrong guess" -- it must not be routed through
        _record_failure_locked, or a legitimate password rotation could
        itself contribute to locking the very account it just secured."""
        db_path = str(tmp_path / "race3.db")
        manager_a = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
        manager_b = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
        try:
            manager_a.create_user("alice", _GOOD_PASSWORD)
            real_verify = importlib.import_module("utils.authn").verify_password
            rotated = {"done": False}

            def racing_verify(password, record):
                if not rotated["done"] and record != _dummy_record():
                    rotated["done"] = True
                    manager_b.set_password("alice", "a brand new password entirely")
                return real_verify(password, record)

            monkeypatch.setattr("utils.authn_manager.authn.verify_password", racing_verify)
            with pytest.raises(AuthLoginFailed):
                manager_a.login("alice", _GOOD_PASSWORD)

            # set_password() itself resets the counter to 0; assert it is
            # still 0 (not 1) after the raced attempt on top of it.
            row = manager_a.conn.execute(manager_a._sql_get_user, ("alice",)).fetchone()
            assert row["failed_count"] == 0
        finally:
            manager_a.close()
            manager_b.close()


class TestLockout:
    @pytest.fixture(autouse=True)
    def _freeze_lockout_clock(self, manager, monkeypatch):
        """Threshold-5 lockout is only _LOCKOUT_BASE_SEC (2.0s). login() snapshots
        manager._now() before scrypt; on Windows CI one hash can exceed that
        window, so the sixth call sees an expired lock. Freeze the clock so
        these tests assert lockout semantics, not hasher wall time.
        """
        self._now = manager._now()
        monkeypatch.setattr(manager, "_now", lambda: self._now)

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
        self._now += 3.0  # past the 2s delay at failure #5
        result = manager.login("alice", _GOOD_PASSWORD)
        assert result.username == "alice"

class TestSessions:
    def test_validate_session_returns_the_username(self, manager):
        manager.create_user("alice", _GOOD_PASSWORD)
        result = manager.login("alice", _GOOD_PASSWORD)
        info = manager.validate_session(result.session_id)
        assert info is not None
        assert info.username == "alice"
        # SessionInfo.csrf_token is the stored HASH, not the plaintext
        # LoginResult.csrf_token -- see SessionInfo's docstring.
        assert info.csrf_token == hash_token(result.csrf_token)

    def test_session_and_csrf_token_are_hashed_at_rest(self, manager):
        """Issue #998: a copied/backed-up DB file must not hand out directly
        usable session cookies or CSRF tokens -- both must be stored hashed,
        the same way device_tokens.token_hash already is."""
        manager.create_user("alice", _GOOD_PASSWORD)
        result = manager.login("alice", _GOOD_PASSWORD)
        # This fixture is always SQLite (see the `manager` fixture above), so
        # the placeholder is always "?" -- no need for the ph-interpolation
        # pattern utils/authn_manager.py's own SQL templates use for
        # SQLite/Postgres portability.
        row = manager.conn.execute(
            "SELECT session_id, csrf_token FROM sessions WHERE username = ?",
            ("alice",),
        ).fetchone()
        assert row["session_id"] != result.session_id
        assert row["session_id"] == hash_token(result.session_id)
        assert row["csrf_token"] != result.csrf_token
        assert row["csrf_token"] == hash_token(result.csrf_token)

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

    def test_token_for_unknown_user_closes_its_read_transaction(self, manager, monkeypatch):
        """Same class of gap as login()'s unknown-username path: the SELECT
        create_device_token() runs to check the user exists must not leave a
        Postgres connection idle-in-transaction when it raises."""
        calls = []
        monkeypatch.setattr(manager, "_end_read_txn", lambda: calls.append(1))
        with pytest.raises(AuthUserNotFound):
            manager.create_device_token("nobody", "laptop")
        assert calls == [1]

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

    def test_duplicate_label_closes_its_read_transaction(self, manager, monkeypatch):
        """Same class of gap on the duplicate-label rejection path."""
        manager.create_user("alice", _GOOD_PASSWORD)
        manager.create_device_token("alice", "laptop")
        calls = []
        monkeypatch.setattr(manager, "_end_read_txn", lambda: calls.append(1))
        with pytest.raises(AuthTokenLabelExists):
            manager.create_device_token("alice", "laptop")
        assert calls == [1]

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
