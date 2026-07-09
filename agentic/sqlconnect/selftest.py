"""Pre-flight self-test for ``python -m agentic.sqlconnect.cli test`` (no live DB)."""

from __future__ import annotations

import os

from agentic.sqlconnect.client import assert_read_only_sql, validate_identifier
from agentic.sqlconnect.config import load_sqlconnect_config
from utils.errors import SqlConnectConfigError, SqlConnectError
from utils.selftest import fail, finalize, ok, skip


def run_self_test(config_path: str = "config.yaml") -> tuple[int, int, list[str]]:
    results: list[tuple[bool, str]] = []

    try:
        sql_cfg = load_sqlconnect_config(config_path)
        results.append(ok("01. sqlconnect config loads and validates"))
    except SqlConnectConfigError as exc:
        results.append(fail("01. sqlconnect config loads and validates", exc.message))
        for n in range(2, 6):
            results.append(skip(f"{n:02d}. (skipped -- no config)", "config invalid"))
        return finalize(results)

    # 02. read-only guard rejects DML and accepts SELECT.
    rejected = 0
    for bad in ("DELETE FROM users", "DROP TABLE t", "SELECT 1; DROP TABLE t"):
        try:
            assert_read_only_sql(bad)
        except SqlConnectError:
            rejected += 1
    try:
        assert_read_only_sql("SELECT 1")
        accepts = True
    except SqlConnectError:
        accepts = False
    if rejected == 3 and accepts:
        results.append(ok("02. read-only guard rejects DML, accepts SELECT"))
    else:
        results.append(fail("02. read-only guard", f"rejected {rejected}/3, accepts={accepts}"))

    # 03. identifier validation.
    try:
        validate_identifier("schema.table")
        bad_rejected = False
        try:
            validate_identifier("1; drop")
        except SqlConnectError:
            bad_rejected = True
        results.append(ok("03. identifier validation") if bad_rejected
                       else fail("03. identifier validation", "did not reject bad ident"))
    except SqlConnectError as exc:
        results.append(fail("03. identifier validation", exc.message))

    # 04. driver import (tolerate absence).
    from agentic.sqlconnect.client import SqlClient
    client = SqlClient({}, sql_cfg, config_path)
    try:
        client._import_driver()
        results.append(ok(f"04. {sql_cfg.driver} driver importable"))
    except Exception:  # noqa: BLE001 -- driver is optional/opt-in
        results.append(skip("04. driver import", f"{sql_cfg.driver} driver not installed (opt-in)"))

    # 05. DSN env presence (tolerate absence).
    if os.environ.get(sql_cfg.dsn_env):
        results.append(ok(f"05. DSN env {sql_cfg.dsn_env} set"))
    else:
        results.append(skip("05. DSN env", f"{sql_cfg.dsn_env} not set (set it to connect)"))

    return finalize(results)


if __name__ == "__main__":
    p, t, out = run_self_test()
    for ln in out:
        print(ln)
    print(f"\n{p}/{t} passed")
    raise SystemExit(0 if p == t else 1)
