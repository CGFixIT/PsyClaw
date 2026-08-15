"""Tests for utils/authn_cli.py -- the `cyclaw-user` console script.

Calls main(argv) directly (no subprocess) against a real tmp_path config and
SQLite DB, mirroring tests/test_sync_cli.py's exit-code-contract style.
getpass is only exercised via explicit monkeypatching -- every other test
passes --password so it never blocks on stdin.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import yaml

from utils.authn_cli import EXIT_ENV, EXIT_FAIL, EXIT_OK, load_config, main

_GOOD_PASSWORD = "correct horse battery staple"


@pytest.fixture
def config_path(tmp_path):
    cfg = {"auth": {"enabled": True, "db_path": str(tmp_path / "auth.db")}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(cfg), encoding="utf-8")
    return str(path)


class TestLoadConfig:
    def test_missing_file_raises_auth_config_error(self, tmp_path):
        from utils.errors import AuthConfigError

        with pytest.raises(AuthConfigError):
            load_config(str(tmp_path / "does-not-exist.yaml"))

    def test_invalid_yaml_raises_auth_config_error(self, tmp_path):
        from utils.errors import AuthConfigError

        bad = tmp_path / "config.yaml"
        bad.write_text("auth: [unterminated", encoding="utf-8")
        with pytest.raises(AuthConfigError):
            load_config(str(bad))

    def test_non_mapping_root_raises_auth_config_error(self, tmp_path):
        from utils.errors import AuthConfigError

        bad = tmp_path / "config.yaml"
        bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(AuthConfigError):
            load_config(str(bad))

    def test_relative_path_anchors_to_repo_root_not_cwd(self):
        # config.yaml at the repo root always exists in this checkout.
        cfg = load_config("config.yaml")
        assert isinstance(cfg, dict)
        assert "app" in cfg


class TestAddListDisableEnable:
    def test_add_then_list(self, config_path, capsys):
        assert main(["--config", config_path, "add", "alice", "--password", _GOOD_PASSWORD]) == EXIT_OK
        capsys.readouterr()
        assert main(["--config", config_path, "list"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "alice" in out
        assert "enabled" in out

    def test_add_duplicate_is_exit_fail(self, config_path, capsys):
        main(["--config", config_path, "add", "alice", "--password", _GOOD_PASSWORD])
        capsys.readouterr()
        code = main(["--config", config_path, "add", "alice", "--password", "another good password"])
        assert code == EXIT_FAIL
        assert "already exists" in capsys.readouterr().err

    def test_add_short_password_is_exit_fail(self, config_path, capsys):
        code = main(["--config", config_path, "add", "alice", "--password", "short"])
        assert code == EXIT_FAIL

    def test_list_with_no_users_says_so(self, config_path, capsys):
        assert main(["--config", config_path, "list"]) == EXIT_OK
        assert "no users" in capsys.readouterr().out

    def test_disable_then_enable(self, config_path, capsys):
        main(["--config", config_path, "add", "alice", "--password", _GOOD_PASSWORD])
        capsys.readouterr()
        assert main(["--config", config_path, "disable", "alice"]) == EXIT_OK
        assert "disabled" in capsys.readouterr().out.lower()
        assert main(["--config", config_path, "list"]) == EXIT_OK
        assert "disabled" in capsys.readouterr().out
        assert main(["--config", config_path, "enable", "alice"]) == EXIT_OK

    def test_disable_unknown_user_is_exit_fail(self, config_path, capsys):
        code = main(["--config", config_path, "disable", "nobody"])
        assert code == EXIT_FAIL
        assert "unknown user" in capsys.readouterr().err

    def test_add_role_and_list_shows_it(self, config_path, capsys):
        assert main([
            "--config", config_path, "add", "eve", "--password", _GOOD_PASSWORD, "--role", "audit",
        ]) == EXIT_OK
        capsys.readouterr()
        assert main(["--config", config_path, "list"]) == EXIT_OK
        assert "eve" in capsys.readouterr().out
        assert main(["--config", config_path, "list"]) == EXIT_OK
        assert "audit" in capsys.readouterr().out

    def test_add_invalid_role_is_exit_fail(self, config_path, capsys):
        code = main([
            "--config", config_path, "add", "eve", "--password", _GOOD_PASSWORD, "--role", "root",
        ])
        assert code == EXIT_FAIL


class TestPasswd:
    def test_passwd_changes_the_password(self, config_path):
        main(["--config", config_path, "add", "alice", "--password", _GOOD_PASSWORD])
        new_password = "a completely different password"
        assert main(["--config", config_path, "passwd", "alice", "--password", new_password]) == EXIT_OK

        from utils.authn_manager import AuthManager

        manager = AuthManager(load_config(config_path))
        try:
            result = manager.login("alice", new_password)
            assert result.username == "alice"
        finally:
            manager.close()

    def test_passwd_unknown_user_is_exit_fail(self, config_path):
        code = main(["--config", config_path, "passwd", "nobody", "--password", _GOOD_PASSWORD])
        assert code == EXIT_FAIL

    def test_omitted_password_prompts_via_getpass(self, config_path, monkeypatch, capsys):
        main(["--config", config_path, "add", "alice", "--password", _GOOD_PASSWORD])
        capsys.readouterr()
        prompts = iter(["a brand new prompted password", "a brand new prompted password"])
        monkeypatch.setattr("getpass.getpass", lambda *_a, **_kw: next(prompts))
        assert main(["--config", config_path, "passwd", "alice"]) == EXIT_OK

    def test_mismatched_confirmation_is_exit_fail(self, config_path, monkeypatch, capsys):
        main(["--config", config_path, "add", "alice", "--password", _GOOD_PASSWORD])
        capsys.readouterr()
        prompts = iter(["first attempt password", "a different second attempt"])
        monkeypatch.setattr("getpass.getpass", lambda *_a, **_kw: next(prompts))
        code = main(["--config", config_path, "passwd", "alice"])
        assert code == EXIT_FAIL
        assert "did not match" in capsys.readouterr().err


class TestDeviceTokens:
    def test_create_list_revoke(self, config_path, capsys):
        main(["--config", config_path, "add", "alice", "--password", _GOOD_PASSWORD])
        capsys.readouterr()

        assert main(["--config", config_path, "token", "create", "alice", "laptop"]) == EXIT_OK
        create_out = capsys.readouterr().out
        assert "will not be shown again" in create_out
        token = create_out.strip().splitlines()[-1]
        assert len(token) > 20

        assert main(["--config", config_path, "token", "list", "alice"]) == EXIT_OK
        assert "laptop" in capsys.readouterr().out

        assert main(["--config", config_path, "token", "revoke", "alice", "laptop"]) == EXIT_OK
        capsys.readouterr()

        assert main(["--config", config_path, "token", "revoke", "alice", "laptop"]) == EXIT_FAIL
        assert "no active token" in capsys.readouterr().err

    def test_token_for_unknown_user_is_exit_fail(self, config_path):
        code = main(["--config", config_path, "token", "create", "nobody", "laptop"])
        assert code == EXIT_FAIL

    def test_token_list_with_none_says_so(self, config_path, capsys):
        main(["--config", config_path, "add", "alice", "--password", _GOOD_PASSWORD])
        capsys.readouterr()
        assert main(["--config", config_path, "token", "list", "alice"]) == EXIT_OK
        assert "no tokens" in capsys.readouterr().out


class TestBadConfig:
    def test_missing_config_is_exit_env(self, tmp_path, capsys):
        code = main(["--config", str(tmp_path / "nope.yaml"), "list"])
        assert code == EXIT_ENV
        assert "Error" in capsys.readouterr().err

    def test_auth_manager_construction_failure_is_exit_env(self, config_path, monkeypatch, capsys):
        """A bad DB (unopenable path, corrupt file, misconfigured Postgres
        DSN) must report a clean EXIT_ENV, not an unhandled traceback."""
        from utils import authn_cli

        def boom(_cfg):
            raise RuntimeError("simulated store failure")

        monkeypatch.setattr(authn_cli, "AuthManager", boom)
        code = main(["--config", config_path, "list"])
        assert code == EXIT_ENV
        assert "could not open the auth store" in capsys.readouterr().err


class TestManagerAlwaysClosed:
    def test_manager_close_runs_even_on_a_failed_command(self, config_path, monkeypatch):
        from utils import authn_cli

        real_manager_cls = authn_cli.AuthManager
        instances = []

        def spy_manager(cfg):
            m = real_manager_cls(cfg)
            m.close = MagicMock(wraps=m.close)
            instances.append(m)
            return m

        monkeypatch.setattr(authn_cli, "AuthManager", spy_manager)
        main(["--config", config_path, "disable", "nobody"])  # a command that raises AuthUserNotFound
        assert instances[0].close.called
