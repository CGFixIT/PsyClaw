"""Tests for utils.gen_cert (cyclaw-gen-cert)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import utils.gen_cert as gen_cert


def test_san_includes_hostname_and_loopback():
    san = gen_cert.subject_alt_names("cyclaw-box")
    assert "DNS:cyclaw-box" in san
    assert "DNS:localhost" in san
    assert "IP:127.0.0.1" in san


def test_san_appends_extra_entries():
    san = gen_cert.subject_alt_names("cyclaw-box", extra=["IP:10.0.0.5", "DNS:box.local"])
    assert san.endswith("IP:10.0.0.5,DNS:box.local") or (
        "IP:10.0.0.5" in san and "DNS:box.local" in san
    )


def test_find_openssl_falls_back_to_windows_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.gen_cert.shutil.which", lambda _name: None)
    candidate = tmp_path / "openssl.exe"
    candidate.write_text("", encoding="utf-8")
    monkeypatch.setattr("utils.gen_cert._WINDOWS_OPENSSL", (candidate,))
    assert gen_cert.find_openssl() == candidate


def test_find_openssl_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr("utils.gen_cert.shutil.which", lambda _name: None)
    monkeypatch.setattr("utils.gen_cert._WINDOWS_OPENSSL", ())
    assert gen_cert.find_openssl() is None


def test_lan_ipv4_oserror_and_loopback_are_ignored(monkeypatch):
    class _BoomSock:
        def connect(self, *_a):
            raise OSError("no route")

        def close(self):
            pass

    monkeypatch.setattr("utils.gen_cert.socket.socket", lambda *a, **k: _BoomSock())
    assert gen_cert._lan_ipv4() is None

    class _LoopSock:
        def connect(self, *_a):
            return None

        def getsockname(self):
            return ("127.0.0.1", 0)

        def close(self):
            pass

    monkeypatch.setattr("utils.gen_cert.socket.socket", lambda *a, **k: _LoopSock())
    assert gen_cert._lan_ipv4() is None


def test_remove_temporary_output_warns_on_oserror(monkeypatch, tmp_path, capsys):
    target = tmp_path / "x.tmp"
    target.write_text("x", encoding="utf-8")
    original = Path.unlink

    def _unlink(self, *args, **kwargs):
        if self == target:
            raise OSError("busy")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _unlink)
    gen_cert._remove_temporary_output(target)
    assert "could not remove temporary" in capsys.readouterr().err


def test_backup_existing_output_cleans_failed_copy(monkeypatch, tmp_path):
    target = tmp_path / "cert.pem"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr("utils.gen_cert._temporary_output_path", lambda _t: tmp_path / "bak.tmp")

    def _boom(*_a, **_k):
        raise OSError("copy failed")

    monkeypatch.setattr("utils.gen_cert.shutil.copy2", _boom)
    removed: list[Path] = []
    monkeypatch.setattr(
        "utils.gen_cert._remove_temporary_output",
        lambda p: removed.append(p),
    )
    try:
        gen_cert._backup_existing_output(target)
        raise AssertionError("expected OSError")
    except OSError:
        pass
    assert removed and removed[0].name == "bak.tmp"


def test_missing_openssl_is_env_error(monkeypatch):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: None)
    assert gen_cert.main(["--certfile", "x.pem", "--keyfile", "y.pem"]) == gen_cert.EXIT_ENV


def test_nonpositive_days_is_env_error(tmp_path):
    assert gen_cert.main([
        "--certfile", str(tmp_path / "c.pem"),
        "--keyfile", str(tmp_path / "k.pem"),
        "--days", "0",
    ]) == gen_cert.EXIT_ENV


def test_repo_local_custom_keyfile_is_refused(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("utils.gen_cert._REPO_ROOT", tmp_path)
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))

    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("unsafe repository-local keyfile must be refused before OpenSSL runs")

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _should_not_run)
    rc = gen_cert.main(["--certfile", "data/tls/cert.pem", "--keyfile", "certs/key.pem"])
    assert rc == gen_cert.EXIT_ENV
    assert "outside data/tls" in capsys.readouterr().err


def test_force_does_not_bypass_repo_local_keyfile_guard(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("utils.gen_cert._REPO_ROOT", tmp_path)
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))

    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("--force must not write a repository-local key outside data/tls")

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _should_not_run)
    rc = gen_cert.main([
        "--certfile", "data/tls/cert.pem",
        "--keyfile", "certs/key.pem",
        "--force",
    ])
    assert rc == gen_cert.EXIT_ENV
    assert "outside data/tls" in capsys.readouterr().err


def test_openssl_timeout_is_reported(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))

    def _raise_timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _raise_timeout)

    rc = gen_cert.main([
        "--certfile", str(tmp_path / "c.pem"),
        "--keyfile", str(tmp_path / "k.pem"),
    ])
    assert rc == gen_cert.EXIT_FAIL
    assert "timed out" in capsys.readouterr().err


def test_failed_openssl_cleans_partial_outputs_and_allows_a_retry(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))
    cert = tmp_path / "c.pem"
    key = tmp_path / "k.pem"

    def _partial_failure(cmd, **_kwargs):
        Path(cmd[cmd.index("-out") + 1]).write_text("partial-cert", encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="", stderr="openssl failed")

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _partial_failure)
    assert gen_cert.main(["--certfile", str(cert), "--keyfile", str(key)]) == gen_cert.EXIT_FAIL
    assert not cert.exists()
    assert not key.exists()
    assert not list(tmp_path.glob("*.tmp"))

    def _successful_retry(cmd, **_kwargs):
        Path(cmd[cmd.index("-out") + 1]).write_text("new-cert", encoding="utf-8")
        Path(cmd[cmd.index("-keyout") + 1]).write_text("new-key", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _successful_retry)
    assert gen_cert.main(["--certfile", str(cert), "--keyfile", str(key)]) == gen_cert.EXIT_OK
    assert cert.read_text(encoding="utf-8") == "new-cert"
    assert key.read_text(encoding="utf-8") == "new-key"


def test_second_temporary_output_failure_cleans_first_output(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))
    original = gen_cert._temporary_output_path
    calls = {"count": 0}

    def _fail_second_allocation(target):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("no writable temporary path")
        return original(target)

    monkeypatch.setattr("utils.gen_cert._temporary_output_path", _fail_second_allocation)
    rc = gen_cert.main([
        "--certfile", str(tmp_path / "c.pem"),
        "--keyfile", str(tmp_path / "k.pem"),
    ])
    assert rc == gen_cert.EXIT_FAIL
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_key_install_restores_the_previous_certificate_pair(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))
    cert = tmp_path / "c.pem"
    key = tmp_path / "k.pem"
    cert.write_text("old-cert", encoding="utf-8")
    key.write_text("old-key", encoding="utf-8")
    staged: dict[str, Path] = {}

    def _successful_run(cmd, **_kwargs):
        staged["cert"] = Path(cmd[cmd.index("-out") + 1])
        staged["key"] = Path(cmd[cmd.index("-keyout") + 1])
        staged["cert"].write_text("new-cert", encoding="utf-8")
        staged["key"].write_text("new-key", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _successful_run)
    real_replace = gen_cert.os.replace

    def _fail_key_install(source, destination):
        if Path(source) == staged.get("key") and Path(destination) == key:
            raise OSError("key file is locked")
        return real_replace(source, destination)

    monkeypatch.setattr("utils.gen_cert.os.replace", _fail_key_install)
    rc = gen_cert.main([
        "--certfile", str(cert),
        "--keyfile", str(key),
        "--force",
    ])
    assert rc == gen_cert.EXIT_FAIL
    assert cert.read_text(encoding="utf-8") == "old-cert"
    assert key.read_text(encoding="utf-8") == "old-key"
    assert not list(tmp_path.glob("*.tmp"))


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
    rc = gen_cert.main(["--certfile", str(cert), "--keyfile", str(key)])
    assert rc == gen_cert.EXIT_ENV
    assert called["n"] == 0
    assert "refusing to overwrite" in capsys.readouterr().err
    assert cert.read_text(encoding="utf-8") == "old-cert"


def test_comma_in_san_is_env_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))
    rc = gen_cert.main([
        "--certfile", str(tmp_path / "c.pem"),
        "--keyfile", str(tmp_path / "k.pem"),
        "--san", "IP:10.0.0.5,DNS:evil",
    ])
    assert rc == gen_cert.EXIT_ENV
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
    rc = gen_cert.main([
        "--certfile", str(cert),
        "--keyfile", str(key),
        "--force",
        "--san", "IP:10.0.0.5",
    ])
    assert rc == gen_cert.EXIT_OK
    assert "subjectAltName=" in " ".join(seen["cmd"])
    assert "IP:10.0.0.5" in " ".join(seen["cmd"])
    assert cert.read_text(encoding="utf-8") == "new-cert"


def test_find_openssl_uses_which_when_present(monkeypatch):
    monkeypatch.setattr("utils.gen_cert.shutil.which", lambda _name: r"C:\Tools\openssl.exe")
    assert gen_cert.find_openssl() == Path(r"C:\Tools\openssl.exe")


def test_umask_unavailable_still_runs_openssl(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))
    monkeypatch.setattr(
        "utils.gen_cert.os.umask",
        lambda *_a, **_k: (_ for _ in ()).throw(AttributeError("no umask")),
    )

    def _fake_run(cmd, **_kwargs):
        Path(cmd[cmd.index("-out") + 1]).write_text("cert", encoding="utf-8")
        Path(cmd[cmd.index("-keyout") + 1]).write_text("key", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _fake_run)
    rc = gen_cert.main([
        "--certfile", str(tmp_path / "c.pem"),
        "--keyfile", str(tmp_path / "k.pem"),
    ])
    assert rc == gen_cert.EXIT_OK


def test_openssl_oserror_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))

    def _boom(*_a, **_k):
        raise OSError("cannot exec")

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _boom)
    rc = gen_cert.main([
        "--certfile", str(tmp_path / "c.pem"),
        "--keyfile", str(tmp_path / "k.pem"),
    ])
    assert rc == gen_cert.EXIT_FAIL
    assert "failed to run openssl" in capsys.readouterr().err


def test_failed_key_install_without_prior_cert_unlinks_and_reports_rollback_error(
    tmp_path, monkeypatch, capsys
):
    """Cert freshly written (no previous backup); key install fails; unlink rollback fails."""
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))
    cert = tmp_path / "c.pem"
    key = tmp_path / "k.pem"
    staged: dict[str, Path] = {}

    def _successful_run(cmd, **_kwargs):
        out = Path(cmd[cmd.index("-out") + 1])
        keyout = Path(cmd[cmd.index("-keyout") + 1])
        out.write_text("new-cert", encoding="utf-8")
        keyout.write_text("new-key", encoding="utf-8")
        staged["cert"] = out
        staged["key"] = keyout
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _successful_run)
    real_replace = gen_cert.os.replace

    def _fail_key_install(source, destination):
        if Path(source) == staged.get("key") and Path(destination) == key:
            raise OSError("key file is locked")
        return real_replace(source, destination)

    monkeypatch.setattr("utils.gen_cert.os.replace", _fail_key_install)
    original_unlink = Path.unlink

    def _unlink_boom(self, *args, **kwargs):
        if self == cert:
            raise OSError("unlink blocked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _unlink_boom)
    rc = gen_cert.main(["--certfile", str(cert), "--keyfile", str(key)])
    assert rc == gen_cert.EXIT_FAIL
    err = capsys.readouterr().err
    assert "failed to restore previous certificate" in err
    assert "failed to install certificate pair" in err


def test_keyfile_chmod_failure_is_best_effort(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.gen_cert.find_openssl", lambda: Path("/usr/bin/openssl"))

    def _fake_run(cmd, **_kwargs):
        Path(cmd[cmd.index("-out") + 1]).write_text("cert", encoding="utf-8")
        Path(cmd[cmd.index("-keyout") + 1]).write_text("key", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("utils.gen_cert.subprocess.run", _fake_run)
    monkeypatch.setattr(
        Path,
        "chmod",
        lambda self, *a, **k: (_ for _ in ()).throw(OSError("acl deny")),
    )
    rc = gen_cert.main([
        "--certfile", str(tmp_path / "c.pem"),
        "--keyfile", str(tmp_path / "k.pem"),
    ])
    assert rc == gen_cert.EXIT_OK
    assert (tmp_path / "c.pem").read_text(encoding="utf-8") == "cert"
