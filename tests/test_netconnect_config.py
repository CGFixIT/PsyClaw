"""Configuration and exact-scope tests for agentic.netconnect."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentic.netconnect.config import load_netconnect_config
from utils.errors import NetConnectConfigError
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset():
    reset_config_cache()
    yield
    reset_config_cache()


def _cfg(tmp_path: Path, block: dict | None) -> str:
    doc: dict = {"logging": {"audit_file": str(tmp_path / "a.jsonl"), "audit_fields": {}}}
    if block is not None:
        doc["netconnect"] = block
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(path)


def test_disabled_defaults(tmp_path):
    cfg = load_netconnect_config(_cfg(tmp_path, {"enabled": False}))
    assert cfg.enabled is False
    assert cfg.allowed_cidrs == []
    assert cfg.allowed_net_ops == ["self", "arp"]
    assert cfg.command_timeout_sec == 5.0
    assert cfg.max_neighbors == 512


def test_enabled_requires_explicit_scope(tmp_path):
    with pytest.raises(NetConnectConfigError, match="at least one explicit CIDR"):
        load_netconnect_config(_cfg(tmp_path, {"enabled": True}))


def test_cidrs_are_canonicalized_and_deduplicated(tmp_path):
    cfg = load_netconnect_config(_cfg(tmp_path, {
        "enabled": True,
        "allowed_cidrs": ["192.168.1.7/24", "192.168.1.0/24"],
    }))
    assert cfg.allowed_cidrs == ["192.168.1.0/24"]


@pytest.mark.parametrize("cidr", [
    "0.0.0.0/0",
    "8.8.8.0/24",
    "100.64.0.0/10",
    "169.254.0.0/16",
    "192.0.0.0/24",
    "::1/128",
])
def test_non_rfc1918_or_ipv6_scope_is_rejected(tmp_path, cidr):
    with pytest.raises(NetConnectConfigError):
        load_netconnect_config(_cfg(tmp_path, {"enabled": True, "allowed_cidrs": [cidr]}))


def test_unknown_op_rejected(tmp_path):
    with pytest.raises(NetConnectConfigError, match="unknown ops"):
        load_netconnect_config(_cfg(tmp_path, {
            "enabled": True,
            "allowed_cidrs": ["10.0.0.0/8"],
            "allowed_net_ops": ["ping"],
        }))


def test_absent_block_raises(tmp_path):
    with pytest.raises(NetConnectConfigError):
        load_netconnect_config(_cfg(tmp_path, None))


@pytest.mark.parametrize("key,value", [
    ("enabled", "false"),
    ("command_timeout_sec", 0),
    ("command_timeout_sec", 31),
    ("max_neighbors", 0),
    ("max_neighbors", 4097),
])
def test_invalid_caps_and_boolean_rejected(tmp_path, key, value):
    block = {"enabled": False, key: value}
    with pytest.raises(NetConnectConfigError):
        load_netconnect_config(_cfg(tmp_path, block))
