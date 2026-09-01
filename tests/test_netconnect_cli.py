"""CLI behavior for passive netconnect operations."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import pytest
import yaml

import utils.logger as logger_mod
from agentic.netconnect import cli
from agentic.netconnect.selftest import run_self_test


@pytest.fixture(autouse=True)
def _reset():
    logger_mod.reset_config_cache()
    yield
    logger_mod.reset_config_cache()


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


def test_status_surfaces_unknown_config_keys(tmp_path, capsys):
    """A typo'd netconnect key must show up in status output, not vanish.

    load_netconnect_config() drops unrecognized keys onto _unknown_keys; before
    this display line nothing on the CLI path read it, so e.g. `max_neighbor`
    silently ran on the default.
    """
    path = _cfg(tmp_path, {"enabled": False, "max_neighbor": 5})
    assert cli.main(["--config", path, "status"]) == 0
    captured = capsys.readouterr()
    assert "max_neighbor" in captured.err
    assert "unknown netconnect keys" in captured.err

    # And a clean config stays silent on stderr. _cfg reuses the same path and
    # _get_config caches by path, so drop the cache before the reload.
    logger_mod.reset_config_cache()
    clean = _cfg(tmp_path, {"enabled": False})
    assert cli.main(["--config", clean, "status"]) == 0
    assert "unknown netconnect keys" not in capsys.readouterr().err


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

    argv = [r"C:\\Windows\\System32\\arp.exe", "-a"]
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
    assert payload["neighbors"][0]["interface"] == "192.168.1.50"
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


def test_windows_interface_ip_is_scope_filtered(tmp_path, capsys, monkeypatch):
    path = _cfg(tmp_path, {
        "enabled": True,
        "allowed_cidrs": ["192.168.1.1/32"],
    })
    import agentic.netconnect.client as client

    argv = [r"C:\\Windows\\System32\\arp.exe", "-a"]
    monkeypatch.setattr(client.platform, "system", lambda: "Windows")
    monkeypatch.setattr(client, "_neighbor_command", lambda _system: argv)

    def fake_run(actual, **kwargs):
        return subprocess.CompletedProcess(
            actual,
            0,
            stdout=(
                "Interface: 192.168.1.50 --- 0x6\n"
                "  192.168.1.1 aa-bb-cc-dd-ee-ff dynamic\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(client.subprocess, "run", fake_run)
    assert cli.main(["--config", path, "arp"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["ip"] for item in payload["neighbors"]] == ["192.168.1.1"]
    assert payload["neighbors"][0]["interface"] == ""
    assert payload["active_probe"] is False
    audit_text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "192.168.1.50" not in audit_text
    assert "192.168.1.1" not in audit_text


def test_main_wires_logging_before_dispatch(tmp_path, monkeypatch):
    """main() must call setup_logging before dispatch, not leave it uncalled.

    Before this fix, this entrypoint never called setup_logging: its own
    loggers reached only Python's stderr last-resort handler regardless of
    config.yaml's logging.log_file. Does not re-test setup_logging's own
    mechanics (covered by test_logger.py) -- only that THIS entrypoint calls
    it, with the loaded config, before the subcommand runs.
    """
    log_path = tmp_path / "cyclaw.log"
    block = {"enabled": False}
    doc = {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {},
                        "log_file": str(log_path), "capture_third_party": True, "third_party_level": "INFO"},
            "netconnect": block}
    cfg_p = tmp_path / "config.yaml"
    cfg_p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg_path = str(cfg_p)

    monkeypatch.setattr(logger_mod, "_logging_initialized", False)
    real_root = logging.getLogger()
    before = list(real_root.handlers)
    try:
        assert cli.main(["--config", cfg_path, "status"]) == 0

        logging.getLogger("agentic.netconnect.wiring_regression_test").warning("netconnect-cli-wiring-marker")
        for handler in real_root.handlers:
            handler.flush()
        assert log_path.exists(), "main() did not call setup_logging with the loaded config"
        assert "netconnect-cli-wiring-marker" in log_path.read_text(encoding="utf-8")
    finally:
        for handler in list(real_root.handlers):
            if handler not in before:
                real_root.removeHandler(handler)
                handler.close()
        logger_mod._logging_initialized = False


def test_main_maps_a_broken_log_file_to_env_exit_not_a_crash(tmp_path, monkeypatch):
    """setup_logging's own OSError must map onto the exit-code API.

    Before this fix, main() called setup_logging(_get_config(args.config))
    OUTSIDE the dispatch try -- a misconfigured logging.log_file (here: it
    names a directory, so opening it as a file raises IsADirectoryError)
    escaped straight out of main() as an uncaught traceback with exit code
    1, which ops_runner's exit-code mapping has no entry for, so it reported
    "unknown" instead of the environment/config problem this actually is
    (codex review on #1239).
    """
    doc = {
        "logging": {
            "audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {},
            "log_file": str(tmp_path),  # a directory, not a file
        },
        "netconnect": {"enabled": False},
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    monkeypatch.setattr(logger_mod, "_logging_initialized", False)
    cyclaw_logger = logging.getLogger("cyclaw")
    agentic_logger = logging.getLogger("agentic")
    before_cyclaw = list(cyclaw_logger.handlers)
    before_agentic = list(agentic_logger.handlers)
    try:
        assert cli.main(["--config", str(cfg_path), "status"]) == cli.EXIT_ENV
    finally:
        for logger_obj, before in ((cyclaw_logger, before_cyclaw), (agentic_logger, before_agentic)):
            for handler in list(logger_obj.handlers):
                if handler not in before:
                    logger_obj.removeHandler(handler)
                    handler.close()
        logger_mod._logging_initialized = False


def test_main_maps_a_malformed_logging_block_to_env_exit_not_a_crash(tmp_path, monkeypatch):
    """setup_logging's own AttributeError/TypeError must map onto the exit-code API.

    A malformed logging: block (here: a bool instead of a mapping) makes
    setup_logging's log_cfg.get(...) raise AttributeError before any handler
    is attached -- this must not escape main() as an uncaught traceback with
    exit code 1, which ops_runner's exit-code mapping has no entry for
    (codex review on #1239, fourth round).
    """
    doc = {
        "logging": True,  # malformed: not a mapping
        "netconnect": {"enabled": False},
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    monkeypatch.setattr(logger_mod, "_logging_initialized", False)
    cyclaw_logger = logging.getLogger("cyclaw")
    agentic_logger = logging.getLogger("agentic")
    before_cyclaw = list(cyclaw_logger.handlers)
    before_agentic = list(agentic_logger.handlers)
    try:
        assert cli.main(["--config", str(cfg_path), "status"]) == cli.EXIT_ENV
    finally:
        for logger_obj, before in ((cyclaw_logger, before_cyclaw), (agentic_logger, before_agentic)):
            for handler in list(logger_obj.handlers):
                if handler not in before:
                    logger_obj.removeHandler(handler)
                    handler.close()
        logger_mod._logging_initialized = False


def test_main_maps_an_invalid_log_path_to_env_exit_not_a_crash(tmp_path, monkeypatch):
    """setup_logging's own ValueError must map onto the exit-code API.

    logging.log_file containing an embedded NUL byte makes
    logging.FileHandler raise ValueError -- a third exception type this
    narrow config-load-and-logging-init call site can raise, beyond the
    OSError and AttributeError/TypeError already handled. This must not
    escape main() as an uncaught traceback with exit code 1, which
    ops_runner's exit-code mapping has no entry for (codex review on
    #1239, fifth round).
    """
    doc = {
        "logging": {
            "audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {},
            "log_file": "bad\x00path",  # embedded NUL -> ValueError
        },
        "netconnect": {"enabled": False},
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    monkeypatch.setattr(logger_mod, "_logging_initialized", False)
    cyclaw_logger = logging.getLogger("cyclaw")
    agentic_logger = logging.getLogger("agentic")
    before_cyclaw = list(cyclaw_logger.handlers)
    before_agentic = list(agentic_logger.handlers)
    try:
        assert cli.main(["--config", str(cfg_path), "status"]) == cli.EXIT_ENV
    finally:
        for logger_obj, before in ((cyclaw_logger, before_cyclaw), (agentic_logger, before_agentic)):
            for handler in list(logger_obj.handlers):
                if handler not in before:
                    logger_obj.removeHandler(handler)
                    handler.close()
        logger_mod._logging_initialized = False
