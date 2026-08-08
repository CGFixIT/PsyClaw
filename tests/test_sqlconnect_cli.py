"""Tests for agentic.sqlconnect.cli + selftest."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentic.sqlconnect import cli
from agentic.sqlconnect.selftest import run_self_test
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset():
    reset_config_cache()
    yield
    reset_config_cache()


def _cfg(tmp_path: Path, block: dict) -> str:
    doc = {"logging": {"audit_file": str(tmp_path / "a.jsonl"), "audit_fields": {}}, "sqlconnect": block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(path)


def test_status(tmp_path, capsys):
    cp = _cfg(tmp_path, {"enabled": False})
    assert cli.main(["--config", cp, "status"]) == 0
    assert "SQL Connector Status" in capsys.readouterr().out


def test_bad_config_exit_env(tmp_path):
    cp = _cfg(tmp_path, {"enabled": True, "driver": "oracle"})
    assert cli.main(["--config", cp, "status"]) == 3


def test_disabled_query_noop(tmp_path):
    cp = _cfg(tmp_path, {"enabled": False})
    assert cli.main(["--config", cp, "schema"]) == 0


def test_query_bad_sql_exit_fail(tmp_path):
    cp = _cfg(tmp_path, {"enabled": True})
    assert cli.main(["--config", cp, "query", "--sql", "DELETE FROM t"]) == 2


def test_query_good_sql_driver_absent_exit_env(tmp_path):
    cp = _cfg(tmp_path, {"enabled": True})
    # valid SELECT passes the guard, then the (absent) driver import -> EXIT_ENV
    assert cli.main(["--config", cp, "query", "--sql", "SELECT 1"]) == 3


def test_query_requires_arg(tmp_path):
    cp = _cfg(tmp_path, {"enabled": True})
    assert cli.main(["--config", cp, "query"]) == 2


def test_query_csv_format_reaches_guard_and_exits_env(tmp_path):
    # --format csv passes the guard (SQL is valid) then hits the absent driver -> EXIT_ENV.
    cp = _cfg(tmp_path, {"enabled": True})
    assert cli.main(["--config", cp, "query", "--sql", "SELECT 1", "--format", "csv"]) == 3


def test_query_csv_format_prints_csv_string(tmp_path, capsys, monkeypatch):
    cp = _cfg(tmp_path, {"enabled": True})
    # context is imported lazily inside _run; patch the module-level run_op directly.
    import agentic.sqlconnect.context as ctx_mod

    monkeypatch.setattr(
        ctx_mod,
        "run_op",
        lambda *a, **kw: {"format": "csv", "csv": "id,name\r\n1,alice\r\n"},
    )
    code = cli.main(["--config", cp, "query", "--sql", "SELECT 1", "--format", "csv"])
    out = capsys.readouterr().out
    assert code == 0
    assert "id,name" in out
    assert "alice" in out


def test_query_explain_selected_for_valid_sql(tmp_path):
    # --sql --explain on Postgres passes the guard, then hits the absent driver
    # import -> EXIT_ENV, proving the explain op was selected (not rejected).
    cp = _cfg(tmp_path, {"enabled": True})
    assert cli.main(["--config", cp, "query", "--sql", "SELECT 1", "--explain"]) == 3


def test_query_explain_refused_for_mssql(tmp_path):
    # explain is unsupported on mssql -> SqlConnectError -> EXIT_FAIL, before any
    # driver import is attempted.
    cp = _cfg(tmp_path, {"enabled": True, "driver": "mssql"})
    assert cli.main(["--config", cp, "query", "--sql", "SELECT 1", "--explain"]) == 2


def test_query_count_selected_for_table(tmp_path):
    # --table --count selects row_count; the identifier is valid so it reaches the
    # absent driver import -> EXIT_ENV (proving row_count was selected).
    cp = _cfg(tmp_path, {"enabled": True})
    assert cli.main(["--config", cp, "query", "--table", "public.t", "--count"]) == 3


def test_schema_driver_absent_exit_env(tmp_path):
    cp = _cfg(tmp_path, {"enabled": True})
    assert cli.main(["--config", cp, "schema"]) == 3


def test_test_command(tmp_path):
    cp = _cfg(tmp_path, {"enabled": True})
    assert cli.main(["--config", cp, "test"]) == 0


def test_selftest_all_pass(tmp_path):
    cp = _cfg(tmp_path, {"enabled": True})
    passed, total, lines = run_self_test(cp)
    assert total == 5 and passed == total
    assert "read-only guard rejects DML" in "\n".join(lines)


def test_selftest_bad_config(tmp_path):
    doc = {"logging": {"audit_file": str(tmp_path / "a.jsonl")}}  # no sqlconnect block
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    passed, total, lines = run_self_test(str(path))
    assert total == 5 and passed == total - 1


# ── exit codes are an API ────────────────────────────────────────────────────

def test_main_maps_typed_errors_and_does_not_mask_untyped_bugs(monkeypatch):
    """Dispatch-point handler, matching agentic/cli.py::main (#824).

    utils/ops_runner.py's _SQLCONNECT_LABELS maps 0/2/3 and reports everything
    else as "unknown". Narrow on purpose: a genuine bug still raises rather than
    being flattened into a tidy exit 2.
    """
    from utils.errors import (
        SqlConnectConfigError,
        SqlConnectRuntimeError,
        SqlDriverNotInstalledError,
    )

    for exc, want in [
        (SqlConnectConfigError("bad cfg"), cli.EXIT_ENV),
        (SqlDriverNotInstalledError("no driver"), cli.EXIT_ENV),
        (SqlConnectRuntimeError("failed"), cli.EXIT_FAIL),
    ]:
        monkeypatch.setattr(cli, "cmd_status", lambda _a, _e=exc: (_ for _ in ()).throw(_e))
        assert cli.main(["status"]) == want

    monkeypatch.setattr(cli, "cmd_status", lambda _a: (_ for _ in ()).throw(RuntimeError("a real bug")))
    with pytest.raises(RuntimeError, match="a real bug"):
        cli.main(["status"])
