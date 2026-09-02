"""Tests for utils.gen_cert (cyclaw-gen-cert)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from utils.gen_cert import EXIT_ENV, EXIT_FAIL, EXIT_OK, main, subject_alt_names


def test_san_includes_hostname_and_loopback():
    san = subject_alt_names("cyclaw-box")
    assert "DNS:cyclaw-box" in san
    assert "DNS:localhost" in san
    assert "IP:127.0.0.1" in san


def test_san_appends_extra_entries():
    san = subject_alt_names("cyclaw-box", extra=["IP:10.0.0.5", "DNS:box.local"])
    assert san.endswith("IP:10.0.0.5,DNS:box.local") or (
        "IP:10.0.0.5" in san and "DNS:box.local" in san
    )


def test_missing_openssl_is_env_error(monkeypatch):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: None)
    assert main(["--certfile", "x.pem", "--keyfile", "y.pem"]) == EXIT_ENV


def test_nonpositive_days_is_env_error(tmp_path):
    assert main([
        "--certfile", str(tmp_path / "c.pem"),
        "--keyfile", str(tmp_path / "k.pem"),
        "--days", "0",
    ]) == EXIT_ENV


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


def test_existing_pair_is_refused_without_force(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))
    cert = tmp_path / "c.pem"
    key = tmp_path / "k.pem"
    cert.write_text("old-cert", encoding="utf-8")
    key.write_text("old-key", encoding="utf-8")
    called = {"n": 0}

    def _should_not_run(*_a, **_k):
        called["n"] += 1
        raise AssertionError("openssl must not run when the pair already exists")

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _should_not_run)
    rc = main(["--certfile", str(cert), "--keyfile", str(key)])
    assert rc == EXIT_ENV
    assert called["n"] == 0
    assert "refusing to overwrite" in capsys.readouterr().err
    assert cert.read_text(encoding="utf-8") == "old-cert"


def test_comma_in_san_is_env_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))
    rc = main([
        "--certfile", str(tmp_path / "c.pem"),
        "--keyfile", str(tmp_path / "k.pem"),
        "--san", "IP:10.0.0.5,DNS:evil",
    ])
    assert rc == EXIT_ENV
    assert "invalid --san" in capsys.readouterr().err


def test_force_overwrites_and_forwards_extra_san(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))
    cert = tmp_path / "c.pem"
    key = tmp_path / "k.pem"
    cert.write_text("old-cert", encoding="utf-8")
    key.write_text("old-key", encoding="utf-8")
    seen: dict[str, list[str]] = {}

    def _fake_run(cmd, **_kwargs):
        seen["cmd"] = list(cmd)
        Path(cmd[cmd.index("-out") + 1]).write_text("new-cert", encoding="utf-8")
        Path(cmd[cmd.index("-keyout") + 1]).write_text("new-key", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _fake_run)
    rc = main([
        "--certfile", str(cert),
        "--keyfile", str(key),
        "--force",
        "--san", "IP:10.0.0.5",
    ])
    assert rc == EXIT_OK
    assert "subjectAltName=" in " ".join(seen["cmd"])
    assert "IP:10.0.0.5" in " ".join(seen["cmd"])
    assert cert.read_text(encoding="utf-8") == "new-cert"
