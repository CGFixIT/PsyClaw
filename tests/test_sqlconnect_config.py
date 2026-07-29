"""Tests for agentic.sqlconnect.config -- loader + validators."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentic.sqlconnect.config import load_sqlconnect_config
from utils.errors import SqlConnectConfigError
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset():
    reset_config_cache()
    yield
    reset_config_cache()


def _cfg(tmp_path: Path, block: dict | None) -> str:
    doc: dict = {"logging": {"audit_file": str(tmp_path / "a.jsonl"), "audit_fields": {}}}
    if block is not None:
        doc["sqlconnect"] = block
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(path)


def test_defaults(tmp_path):
    sc = load_sqlconnect_config(_cfg(tmp_path, {"enabled": True}))
    assert sc.driver == "postgres"
    assert sc.read_only is True and sc.allow_write is False
    assert sc.enabled is True


def test_absent_block_raises(tmp_path):
    with pytest.raises(SqlConnectConfigError):
        load_sqlconnect_config(_cfg(tmp_path, None))


def test_bad_driver(tmp_path):
    with pytest.raises(SqlConnectConfigError):
        load_sqlconnect_config(_cfg(tmp_path, {"enabled": True, "driver": "oracle"}))


def test_read_only_false_rejected(tmp_path):
    with pytest.raises(SqlConnectConfigError):
        load_sqlconnect_config(_cfg(tmp_path, {"enabled": True, "read_only": False}))


def test_allow_write_rejected(tmp_path):
    with pytest.raises(SqlConnectConfigError):
        load_sqlconnect_config(_cfg(tmp_path, {"enabled": True, "allow_write": True}))


def test_unknown_op_rejected(tmp_path):
    with pytest.raises(SqlConnectConfigError):
        load_sqlconnect_config(_cfg(tmp_path, {"enabled": True, "allowed_sql_ops": ["drop_table"]}))


def test_enabled_default_false(tmp_path):
    sc = load_sqlconnect_config(_cfg(tmp_path, {"driver": "mssql"}))
    assert getattr(sc, "enabled", None) is False


@pytest.mark.parametrize("key", ["enabled", "read_only", "allow_write"])
@pytest.mark.parametrize("value", ["false", 0, 1])
def test_boolean_fields_reject_non_bools(tmp_path: Path, key: str, value: object) -> None:
    block: dict[str, object] = {"enabled": True}
    block[key] = value
    with pytest.raises(SqlConnectConfigError, match=rf"sqlconnect\.{key} must be a boolean"):
        load_sqlconnect_config(_cfg(tmp_path, block))
