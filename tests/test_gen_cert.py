"""Tests for utils.gen_cert (cyclaw-gen-cert)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from utils.gen_cert import EXIT_ENV, EXIT_FAIL, main, subject_alt_names


def test_san_includes_hostname_and_loopback():
    san = subject_alt_names("cyclaw-box")
    assert "DNS:cyclaw-box" in san
    assert "DNS:localhost" in san
    assert "IP:127.0.0.1" in san


def test_missing_openssl_is_env_error(monkeypatch):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: None)
    assert main(["--certfile", "x.pem", "--keyfile", "y.pem"]) == EXIT_ENV


def test_nonpositive_days_is_env_error(tmp_path):
    assert main([
        "--certfile", str(tmp_path / "c.pem"),
        "--keyfile", str(tmp_path / "k.pem"),
        "--days", "0",
    ]) == EXIT_ENV


def test_repo_local_custom_keyfile_is_refused(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("utils.gen_cert._REPO_ROOT", tmp_path)
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))

    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("unsafe repository-local keyfile must be refused before OpenSSL runs")

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _should_not_run)
    rc = main(["--certfile", "data/tls/cert.pem", "--keyfile", "certs/key.pem"])
    assert rc == EXIT_ENV
    assert "outside data/tls" in capsys.readouterr().err


def test_openssl_timeout_is_reported(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))

    def _raise_timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _raise_timeout)

    rc = main([
        "--certfile", str(tmp_path / "c.pem"),
        "--keyfile", str(tmp_path / "k.pem"),
    ])
    assert rc == EXIT_FAIL
    assert "timed out" in capsys.readouterr().err
