"""Tests for windows/generate_service_task.py — #912 Windows twin.

Loaded as a standalone script via importlib (it lives under windows/, not a
package). No real Task Scheduler registration is ever attempted.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "windows" / "generate_service_task.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_service_task", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["generate_service_task"] = module
    spec.loader.exec_module(module)
    return module


gst = _load_module()


def test_source_never_imports_i6_core() -> None:
    tree = ast.parse(_SCRIPT_PATH.read_text(encoding="utf-8"))
    banned = {
        "gate",
        "graph",
        "mcp_hybrid_server",
        "gate_ops",
        "gate_auth",
        "gate_memory",
        "agentic",
        "sync",
        "telegram",
        "harness",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in banned


def test_refuses_without_confirm_and_reason(capsys) -> None:
    with patch("generate_service_task.platform.system", return_value="Windows"):
        assert gst.main(["--service", "gate"]) == 1
    err = capsys.readouterr().err
    assert "--confirm" in err
    assert "--reason" in err
    assert "ALWAYS-ON" in err


def test_refuses_empty_reason() -> None:
    with patch("generate_service_task.platform.system", return_value="Windows"):
        assert gst.main(["--service", "gate", "--confirm", "--reason", "   "]) == 1


def test_non_windows_refuses() -> None:
    with patch("generate_service_task.platform.system", return_value="Linux"):
        assert gst.main(["--service", "gate", "--confirm", "--reason", "x"]) == 3


def test_writes_xml_without_registering_or_embedding_key(tmp_path: Path, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"api": {"port": 8787}}), encoding="utf-8")
    with (
        patch("generate_service_task.platform.system", return_value="Windows"),
        patch("generate_service_task.Path.home", return_value=home),
        patch("utils.win_schtasks.Path.home", return_value=home),
    ):
        rc = gst.main([
            "--service",
            "gate",
            "--confirm",
            "--reason",
            "keep RAG up for lab use",
            "--config",
            str(cfg),
            "--api-key-target",
            "com.cgfixit.cyclaw.api-key",
        ])
    assert rc == 0
    xml_path = home / ".CyClaw" / "tasks" / "CyClaw-gate.xml"
    cmd_path = home / ".CyClaw" / "tasks" / "CyClaw-gate.cmd"
    assert xml_path.exists()
    text = xml_path.read_bytes().decode("utf-16")
    assert "schtasks /Create" not in text
    assert "LogonTrigger" in text
    assert "PT30S" in text
    cmd = cmd_path.read_text(encoding="utf-8")
    assert "gate.py" in cmd
    assert "CyClaw-CredMan-Env.ps1" in cmd
    assert "CYCLAW_API_KEY" in cmd
    assert "sk-ant-" not in cmd
    assert "xai-" not in cmd
    out = capsys.readouterr().out
    assert "schtasks /Create" in out
    assert "keep RAG up for lab use" in out


def test_harness_sets_nonsecret_home_env(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    with (
        patch("generate_service_task.platform.system", return_value="Windows"),
        patch("generate_service_task.Path.home", return_value=home),
        patch("utils.win_schtasks.Path.home", return_value=home),
    ):
        assert gst.main(["--service", "harness", "--confirm", "--reason", "console"]) == 0
    cmd = (home / ".CyClaw" / "tasks" / "CyClaw-harness.cmd").read_text(encoding="utf-8")
    assert "harness.server" in cmd
    assert "CYCLAW_HOME" in cmd
    assert "CYCLAW_REPO" in cmd
