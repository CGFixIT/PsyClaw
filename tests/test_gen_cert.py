"""Tests for utils.gen_cert (cyclaw-gen-cert)."""

from __future__ import annotations

from utils.gen_cert import EXIT_ENV, main, subject_alt_names


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
