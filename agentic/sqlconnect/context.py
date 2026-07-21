"""Thin read bundlers over :class:`agentic.sqlconnect.client.SqlClient`."""

from __future__ import annotations

from typing import Literal

from agentic.sqlconnect.client import SqlClient
from agentic.sqlconnect.config import SqlConnectConfig
from utils.errors import SqlConnectError


def run_op(
    cfg: dict,
    sql_cfg: SqlConnectConfig,
    op: str,
    *,
    config_path: str = "config.yaml",
    table: str | None = None,
    sql: str | None = None,
    fmt: Literal["json", "csv"] = "json",
) -> dict:
    client = SqlClient(cfg, sql_cfg, config_path=config_path)
    if op == "schema_list":
        return client.schema_list()
    if op == "table_preview":
        return client.table_preview(table or "")
    if op == "run_select":
        return client.run_select(sql or "", fmt=fmt)
    if op == "explain":
        return client.explain(sql or "")
    if op == "row_count":
        return client.row_count(table or "")
    # Typed, not ValueError: the CLI maps SqlConnectError -> EXIT_FAIL (2); a bare
    # builtin would escape _run's except clauses as an uncaught traceback (exit 1),
    # breaking the documented 0/2/3 exit-code API.
    raise SqlConnectError(
        f"unknown sql op: {op!r}",
        code="SQLCONNECT_OP_NOT_ALLOWED",
        details={"op": op},
    )


__all__ = ["run_op"]
