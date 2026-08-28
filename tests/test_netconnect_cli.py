"""CLI behavior for passive netconnect operations."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from agentic.netconnect import cli
from agentic.netconnect.selftest import run_self_test
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset():
    reset_config_cache()
    yield
    reset_config_cache()


def _cfg(tmp_path: Path, block: dict) -> str:
    doc = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
        "netconnect": block,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(path)


def test_status_and_disabled_noop(tmp_path, capsys):
    path = _cfg(tmp_path, {"enabled": False})
    assert cli.main(["--config", path, "status"]) == 0
    assert "active_scanning" in capsys.readouterr().out
    assert cli.main(["--config", path, "arp"]) == 0
    assert "disabled" in capsys.readouterr().out.lower()


def test_self_inventory_never_resolves_or_probes(tmp_path, capsys, monkeypatch):
    path = _cfg(tmp_path, {
        "enabled": True,
        "allowed_cidrs": ["192.168.1.0/24", "127.0.0.0/8"],
    })
    import agentic.netconnect.client as client

    monkeypatch.setattr(client.socket, "gethostname", lambda: "cyclaw-host")
    monkeypatch.setattr(client.socket, "if_nameindex", lambda: [(1, "lo"), (2, "Ethernet")])
    monkeypatch.setattr(
        client.socket,
        "getaddrinfo",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("self must not resolve")),
    )

    assert cli.main(["--config", path, "self"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["addresses"] == []
    assert [item["name"] for item in payload["interfaces"]] == ["lo", "Ethernet"]
    assert payload["active_probe"] is False
    assert payload["complete"] is False


def test_arp_uses_fixed_argv_filters_scope_and_redacts_audit(tmp_path, capsys, monkeypatch):
    path = _cfg(tmp_path, {
        "enabled": True,
        "allowed_cidrs": ["192.168.1.0/24"],
    })
    import agentic.netconnect.client as client

    argv = [r"C:\Windows\System32\arp.exe", "-a"]
    seen: dict = {}
    monkeypatch.setattr(client.platform, "system", lambda: "Windows")
    monkeypatch.setattr(client, "_neighbor_command", lambda _system: argv)

    def fake_run(actual, **kwargs):
        seen["argv"] = actual
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            actual,
            0,
            stdout=(
                "Interface: 192.168.1.50 --- 0x6\n"
                "  192.168.1.1 aa-bb-cc-dd-ee-ff dynamic\n"
                "  8.8.8.8 11-22-33-44-55-66 dynamic\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(client.subprocess, "run", fake_run)
    assert cli.main(["--config", path, "arp"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["ip"] for item in payload["neighbors"]] == ["192.168.1.1"]
    assert payload["active_probe"] is False
    assert seen["argv"] == argv
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["timeout"] == 5.0

    audit_text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "192.168.1.1" not in audit_text
    assert "aa:bb:cc:dd:ee:ff" not in audit_text
    event = json.loads(audit_text.splitlines()[-1])
    assert event["event"] == "netconnect_scan"
    assert len(event["scope_hashes"]) == 1


def test_bad_config_exit_env(tmp_path):
    path = _cfg(tmp_path, {"enabled": True, "allowed_cidrs": ["0.0.0.0/0"]})
    assert cli.main(["--config", path, "status"]) == cli.EXIT_ENV


def test_selftest_never_runs_platform_command(tmp_path, monkeypatch):
    path = _cfg(tmp_path, {"enabled": True, "allowed_cidrs": ["10.0.0.0/8"]})
    import agentic.netconnect.client as client

    monkeypatch.setattr(
        client.subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("selftest must stay offline")),
    )
    passed, total, _lines = run_self_test(path)
    assert total == 4 and passed == total
