"""Tests for macos/generate_service_plist.py -- the supervised launchd
LaunchAgent generator for gate.py / the harness (highest-risk of CyClaw's
launchd generators; see docs/work/MACOS_LAUNCHD_INTEGRATION_PLAN.md).

Loaded as a standalone script via importlib (it lives under macos/, not a
package) so its main() can be called in-process with mocked
platform.system()/Path.home(), matching this repo's other launchd-generator
test files. No real ~/Library/LaunchAgents is ever touched.
"""

from __future__ import annotations

import importlib.util
import os
import plistlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX fixtures")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "macos" / "generate_service_plist.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_service_plist", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    # Registered in sys.modules under its own name so unittest.mock.patch can
    # resolve "generate_service_plist.<attr>" dotted paths (mock imports the
    # target module by name; a spec-loaded-but-unregistered module isn't
    # importable by that name otherwise).
    sys.modules["generate_service_plist"] = module
    spec.loader.exec_module(module)
    return module


gsp = _load_module()


def _write_config(tmp_path: Path, port: int = 8787) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"api": {"host": "127.0.0.1", "port": port}}), encoding="utf-8")
    return path


def _run(tmp_home: Path, *args: str) -> int:
    with (
        patch("generate_service_plist.platform.system", return_value="Darwin"),
        patch("utils.launchd_plist.Path.home", return_value=tmp_home),
    ):
        return gsp.main(list(args))


# ---------------------------------------------------------------------------
# Platform + confirmation gates
# ---------------------------------------------------------------------------


def test_non_darwin_refuses() -> None:
    with patch("generate_service_plist.platform.system", return_value="Linux"):
        assert gsp.main(["--service", "gate", "--confirm", "--reason", "x"]) == 3


def test_missing_confirm_refuses(tmp_path: Path, capsys) -> None:
    assert _run(tmp_path / "home", "--service", "gate", "--reason", "x") == 1
    err = capsys.readouterr().err
    assert "--confirm" in err
    assert not (tmp_path / "home" / "Library" / "LaunchAgents").exists()


def test_missing_reason_refuses(tmp_path: Path, capsys) -> None:
    assert _run(tmp_path / "home", "--service", "gate", "--confirm") == 1
    err = capsys.readouterr().err
    assert "--reason" in err


def test_blank_reason_refuses(tmp_path: Path) -> None:
    assert _run(tmp_path / "home", "--service", "gate", "--confirm", "--reason", "   ") == 1


def test_nonpositive_throttle_refuses(tmp_path: Path) -> None:
    assert (
        _run(
            tmp_path / "home", "--service", "gate", "--confirm", "--reason", "x",
            "--throttle-sec", "0",
        )
        == 2
    )


# ---------------------------------------------------------------------------
# gate.py plist
# ---------------------------------------------------------------------------


def test_gate_plist_structure(tmp_path: Path, capsys) -> None:
    config = _write_config(tmp_path, port=9999)
    home = tmp_path / "home"

    code = _run(
        home, "--service", "gate", "--config", str(config), "--confirm", "--reason", "test run",
    )
    assert code == 0

    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.gate.plist"
    document = plistlib.loads(plist_path.read_bytes())

    assert document["Label"] == "com.cgfixit.cyclaw.gate"
    assert document["RunAtLoad"] is True
    assert document["KeepAlive"] == {"SuccessfulExit": False}
    assert document["ThrottleInterval"] == 30
    assert "EnvironmentVariables" not in document  # gate.py needs none by default
    args = document["ProgramArguments"]
    assert args[-1].endswith("gate.py")
    assert document["WorkingDirectory"] == str(_REPO_ROOT)

    out = capsys.readouterr().out
    assert "port 9999" in out
    assert "test run" in out
    assert "launchctl bootstrap gui/" in out
    assert "launchctl bootout" in out


def test_gate_plist_default_config_port(tmp_path: Path) -> None:
    # No --config passed: falls back to the real repo config.yaml's api.port.
    home = tmp_path / "home"
    assert _run(home, "--service", "gate", "--confirm", "--reason", "x") == 0
    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.gate.plist"
    assert plistlib.loads(plist_path.read_bytes())["Label"] == "com.cgfixit.cyclaw.gate"


def test_gate_plist_missing_config_file_errors(tmp_path: Path) -> None:
    home = tmp_path / "home"
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(SystemExit) as exc_info:
        _run(home, "--service", "gate", "--config", str(missing), "--confirm", "--reason", "x")
    assert exc_info.value.code == 3


def test_gate_plist_api_key_service_wraps_keychain(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    home = tmp_path / "home"

    assert (
        _run(
            home, "--service", "gate", "--config", str(config),
            "--api-key-service", "com.cgfixit.cyclaw.api-key",
            "--confirm", "--reason", "x",
        )
        == 0
    )

    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.gate.plist"
    document = plistlib.loads(plist_path.read_bytes())
    args = document["ProgramArguments"]
    assert args[0].endswith("cyclaw-keychain-env.sh")
    assert args[1] == "com.cgfixit.cyclaw.api-key"
    assert args[2] == "CYCLAW_API_KEY"
    assert args[3] == "--"
    assert args[-1].endswith("gate.py")
    assert "EnvironmentVariables" not in document  # secret never lands here


# ---------------------------------------------------------------------------
# harness plist
# ---------------------------------------------------------------------------


def test_harness_plist_structure(tmp_path: Path, capsys) -> None:
    home = tmp_path / "home"

    code = _run(home, "--service", "harness", "--confirm", "--reason", "keep console up")
    assert code == 0

    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.harness.plist"
    document = plistlib.loads(plist_path.read_bytes())

    assert document["Label"] == "com.cgfixit.cyclaw.harness"
    assert document["RunAtLoad"] is True
    assert document["KeepAlive"] == {"SuccessfulExit": False}
    args = document["ProgramArguments"]
    assert args[1:3] == ["-m", "harness.server"]
    assert document["EnvironmentVariables"]["CYCLAW_HOME"] == str(home / ".CyClaw")
    assert document["EnvironmentVariables"]["CYCLAW_REPO"] == str(_REPO_ROOT)

    out = capsys.readouterr().out
    assert "port 8790" in out
    assert "keep console up" in out


def test_harness_plist_custom_throttle(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert (
        _run(
            home, "--service", "harness", "--confirm", "--reason", "x", "--throttle-sec", "60",
        )
        == 0
    )
    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.harness.plist"
    assert plistlib.loads(plist_path.read_bytes())["ThrottleInterval"] == 60


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------


def test_neither_plist_ever_contains_a_secret_or_replace_marker(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _run(home, "--service", "gate", "--confirm", "--reason", "x")
    _run(home, "--service", "harness", "--confirm", "--reason", "x")

    agents_dir = home / "Library" / "LaunchAgents"
    for plist_path in agents_dir.glob("com.cgfixit.cyclaw.*.plist"):
        raw = plist_path.read_bytes()
        assert b"REPLACE_" not in raw


def test_idempotent_overwrite(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _run(home, "--service", "gate", "--confirm", "--reason", "first", "--throttle-sec", "10")
    _run(home, "--service", "gate", "--confirm", "--reason", "second", "--throttle-sec", "45")

    agents_dir = home / "Library" / "LaunchAgents"
    matches = list(agents_dir.glob("com.cgfixit.cyclaw.gate*"))
    assert len(matches) == 1
    document = plistlib.loads(matches[0].read_bytes())
    assert document["ThrottleInterval"] == 45
