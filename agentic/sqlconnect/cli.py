"""Command-line entry point: ``python -m agentic.sqlconnect.cli <subcommand>``.

Subcommands:

    status   Print sqlconnect config (driver, dsn env, ops, caps).
    schema   List table schemas (read-only).
    query    Run a read-only query (--sql SELECT...) or preview a table (--table).
             --sql --explain returns the query plan (Postgres); --table --count
             returns count(*) instead of a row preview.
    test     Run the pre-flight self-test.

Exit codes: 0 ok (and the clean no-op when disabled) / 2 op or query failed /
3 config-env problem (bad config, driver missing, DSN unset). Read-only by
construction -- there is no write path.

This module never imports gate.py, graph.py, or mcp_hybrid_server.py.
"""

from __future__ import annotations

import argparse
import json
import sys

from agentic.sqlconnect.config import SqlConnectConfig, load_sqlconnect_config
from utils.errors import (
    SqlConnectConfigError,
    SqlConnectError,
    SqlConnectRuntimeError,
    SqlDriverNotInstalledError,
)
from utils.logger import _get_config

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ENV = 3


def _heading(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def _kv(key: str, value: object) -> None:
    print(f"  {key:.<22} {value}")


def _err(text: str) -> None:
    print(f"  [ERR ] {text}", file=sys.stderr)


def _emit(obj: object) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _load(args: argparse.Namespace) -> SqlConnectConfig | None:
    try:
        return load_sqlconnect_config(args.config)
    except SqlConnectConfigError as exc:
        _err(f"Config error: {exc.message}")
        return None


def _disabled_noop() -> int:
    _heading("SQL connector disabled")
    print("  sqlconnect.enabled is false in config.yaml; nothing to do.")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    sc = _load(args)
    if sc is None:
        return EXIT_ENV
    _heading("CyClaw SQL Connector Status")
    _kv("enabled", getattr(sc, "enabled", False))
    _kv("driver", sc.driver)
    _kv("dsn_env", sc.dsn_env)
    _kv("allowed_sql_ops", ", ".join(sc.allowed_sql_ops))
    _kv("read_only", sc.read_only)
    _kv("statement_timeout_ms", sc.statement_timeout_ms)
    _kv("max_rows", sc.max_rows)
    return EXIT_OK


def _run(args: argparse.Namespace, op: str, **kw: object) -> int:
    sc = _load(args)
    if sc is None:
        return EXIT_ENV
    if not getattr(sc, "enabled", False):
        return _disabled_noop()
    from agentic.sqlconnect import context

    cfg = _get_config(args.config)
    try:
        res = context.run_op(cfg, sc, op, config_path=args.config, **kw)  # type: ignore[arg-type]
    except (SqlDriverNotInstalledError, SqlConnectRuntimeError) as exc:
        _err(exc.message)
        return EXIT_ENV
    except SqlConnectError as exc:
        _err(exc.message)
        return EXIT_FAIL
    if res.get("format") == "csv":
        print(res["csv"], end="")
    else:
        _emit(res)
    return EXIT_OK


def cmd_schema(args: argparse.Namespace) -> int:
    return _run(args, "schema_list")


def cmd_query(args: argparse.Namespace) -> int:
    if args.table:
        if args.count:
            return _run(args, "row_count", table=args.table)
        return _run(args, "table_preview", table=args.table)
    if args.sql:
        if args.explain:
            return _run(args, "explain", sql=args.sql)
        return _run(args, "run_select", sql=args.sql, fmt=args.format)
    _err("query requires --sql or --table")
    return EXIT_FAIL


def cmd_test(args: argparse.Namespace) -> int:
    from agentic.sqlconnect.selftest import run_self_test

    passed, total, lines = run_self_test(args.config)
    _heading(f"Self-test: {passed}/{total} passed")
    for line in lines:
        print(line)
    return EXIT_OK if passed == total else EXIT_FAIL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentic.sqlconnect.cli",
        description="CyClaw read-only SQL connector -- out-of-band, disabled by default.",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml (default: %(default)s)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Print sqlconnect config status.")
    p_status.set_defaults(func=cmd_status)

    p_schema = sub.add_parser("schema", help="List table schemas (read-only).")
    p_schema.set_defaults(func=cmd_schema)

    p_query = sub.add_parser("query", help="Run a read-only query or preview a table.")
    p_query.add_argument("--sql", help="A single SELECT/WITH query.")
    p_query.add_argument("--table", help="Preview a table (schema.table).")
    p_query.add_argument("--explain", action="store_true", help="With --sql: return the query plan (Postgres only).")
    p_query.add_argument("--count", action="store_true", help="With --table: return count(*) instead of a preview.")
    p_query.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format for --sql run_select: json (default) or csv.",
    )
    p_query.set_defaults(func=cmd_query)

    p_test = sub.add_parser("test", help="Run the pre-flight self-test.")
    p_test.set_defaults(func=cmd_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one subcommand, mapping typed failures onto the exit-code API.

    Exit codes are an API here: utils/ops_runner.py maps the documented set to
    ok/failed/env_config and everything else to "unknown".
    Most subcommands convert their own failures, but a handler at the dispatch
    point covers the whole class rather than the call sites known today --
    the same fix agentic/cli.py::main received in #824.

    Deliberately narrow: only the typed hierarchy is classified, so a genuine
    bug still surfaces as a traceback instead of being flattened into a tidy
    exit 2.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SqlConnectConfigError as exc:
        _err(f"Config error: {exc.message}")
        return EXIT_ENV
    except SqlDriverNotInstalledError as exc:
        _err(exc.message)
        return EXIT_ENV
    except SqlConnectError as exc:
        _err(exc.message)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
