"""Read-only SQL client scaffold (Postgres / MSSQL), disabled-by-default.

Read-only is enforced three ways: (1) a SELECT-only query guard rejects anything
that is not a single ``SELECT``/``WITH`` statement (no DDL/DML/multi-statement);
(2) the session is opened read-only at connect time; (3) ``allow_write`` is hard
False in v0.1. The DSN comes from an environment variable only -- never hardcoded.

Driver modules (``psycopg`` / ``pyodbc``) are imported lazily, so a disabled
connector never requires them. The actual connect/execute paths need a live DB and
are ``# pragma: no cover``; the query/identifier guards are pure and fully tested.

Never imported by gate.py / graph.py / mcp_hybrid_server.py.
"""

from __future__ import annotations

import csv
import io
import os
import re
from typing import Any, Literal

from agentic.sqlconnect.config import SqlConnectConfig
from utils.errors import (
    SqlConnectError,
    SqlConnectRuntimeError,
    SqlDriverNotInstalledError,
)
from utils.logger import audit_log

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
# NOTE: ``replace`` is deliberately NOT in this list. ``REPLACE`` as a write
# statement is MySQL-only DML, and this connector supports only Postgres/MSSQL,
# where ``replace(...)`` is a read-only scalar string function. Blocking it
# rejected legitimate read queries like ``SELECT replace(name,'a','b') FROM t``.
# Even if a REPLACE write statement existed, the leading-keyword gate (must start
# with SELECT/WITH) plus the single-statement check would already stop it.
# ``updlock``/``holdlock``/``xlock`` are MSSQL table hints that take
# write-grade locks inside an otherwise valid SELECT (``SELECT * FROM t WITH
# (UPDLOCK)``). On a connector whose contract is read-only that is an
# availability risk (blocking other writers/readers), so the hints are
# forbidden even though the statement itself only reads.
_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|call|"
    r"exec|execute|into|copy|vacuum|attach|begin|commit|rollback|"
    r"updlock|holdlock|xlock)\b",
    re.IGNORECASE,
)

# Single-quoted string literals (``''`` escapes a quote) and double-quoted
# identifiers (``""`` escapes a quote) can legitimately contain SQL keywords or
# comment/``;`` punctuation as *data* or as a quoted column name -- e.g.
# ``SELECT 'please do not delete'`` or ``SELECT "delete" FROM t``. Those are never
# executable SQL, so the structural guards below scan a copy with quoted regions
# blanked out to avoid false-rejecting valid read queries. Real DML, comments and
# stacked statements always live OUTSIDE quotes, so the ``WITH (DELETE ... RETURNING)``
# CTE bypass and ``--``/``/* */`` comment hiding are still caught.
_QUOTED_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")


def _strip_quoted(sql: str) -> str:
    """Blank out single-quoted literals and double-quoted identifiers for scanning.

    Replaces each quoted region with a single space (preserving token boundaries).
    Note: PostgreSQL dollar-quoting (``$$...$$``) is not stripped; a keyword inside
    a dollar-quoted literal would still be rejected -- fail-closed, never open.
    """
    return _QUOTED_RE.sub(" ", sql)


def assert_read_only_sql(sql: str) -> str:
    """Return a cleaned single SELECT/WITH statement, or raise ``SqlConnectError``."""
    if not isinstance(sql, str) or not sql.strip():
        raise SqlConnectError("empty SQL", code="SQLCONNECT_BAD_QUERY")
    cleaned = sql.strip().rstrip(";").strip()
    # Structural guards run on the quote-stripped copy (see _QUOTED_RE rationale).
    scan = _strip_quoted(cleaned)
    # Defense in depth: SQL comments (``--`` line, ``/* */`` block) are a known
    # vector for hiding forbidden keywords or a stacked statement from a keyword
    # scanner (the DB strips the comment, the guard does not). Read-only previews
    # never need comments, so reject them outright rather than try to parse them.
    if "--" in scan or "/*" in scan or "*/" in scan:
        raise SqlConnectError(
            "SQL comments are not allowed in read-only queries",
            code="SQLCONNECT_BAD_QUERY",
        )
    if ";" in scan:
        raise SqlConnectError("multiple statements are not allowed", code="SQLCONNECT_BAD_QUERY")
    lowered = cleaned.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise SqlConnectError("only SELECT/WITH queries are allowed", code="SQLCONNECT_BAD_QUERY")
    hit = _FORBIDDEN_RE.search(scan)
    if hit:
        raise SqlConnectError(
            f"forbidden keyword in read-only query: {hit.group(0)!r}",
            code="SQLCONNECT_BAD_QUERY",
            details={"keyword": hit.group(0)},
        )
    return cleaned


def _columns_and_types(description: Any) -> tuple[list[str], list[str]]:
    """Split a DB-API cursor ``description`` into column names and type names.

    DB-API ``description`` rows are 7-tuples
    ``(name, type_code, display_size, internal_size, precision, scale, null_ok)``.
    ``type_code`` is driver-specific (pyodbc gives a Python type like ``str``;
    psycopg gives a type OID), so the type is rendered as a portable best-effort
    string: a type's ``__name__`` when present (``str``/``int``/...), else
    ``str(type_code)`` (e.g. the OID). Returns ``([], [])`` for a non-row
    statement (``description`` is ``None``). Pure -- unit-tested without a DB.
    """
    if not description:
        return [], []
    cols: list[str] = []
    types: list[str] = []
    for d in description:
        cols.append(d[0])
        type_code = d[1] if len(d) > 1 else None
        types.append(getattr(type_code, "__name__", None) or str(type_code))
    return cols, types


def validate_identifier(name: str) -> str:
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise SqlConnectError(f"invalid SQL identifier: {name!r}", code="SQLCONNECT_BAD_IDENT")
    return name


def quote_identifier(name: str, driver: str) -> str:
    parts = validate_identifier(name).split(".")
    if driver == "mssql":
        return ".".join(f"[{p}]" for p in parts)
    return ".".join(f'"{p}"' for p in parts)


# Lead characters that trigger formula evaluation when a CSV cell is opened in
# Excel / LibreOffice / Google Sheets. A row exported from an untrusted DB that
# starts with one of these is a CSV-injection vector: open in a spreadsheet and
# the formula executes (e.g. `=HYPERLINK("...")`, `=cmd|'...'!A0`, `=2+2`). The
# tab and carriage-return characters are also recognised by some spreadsheet
# parsers as formula leads.
_CSV_FORMULA_LEADS = frozenset(("=", "+", "-", "@", "\t", "\r"))


def _neutralize_csv_cell(value: Any) -> Any:
    """Prefix a single quote to any string cell that starts with a CSV formula
    lead character. Other types pass through unchanged.

    Defense for the SqlClient ``fmt="csv"`` export path. Raw DB rows are
    untrusted from the spreadsheet's perspective even when CyClaw's SQL guard
    has accepted the SELECT — the row contents come from whatever the
    database holds. The leading apostrophe is the OWASP-recommended fix and
    is silently dropped by spreadsheet apps on display, so the cell renders
    as the original text but never as a formula.
    """
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_LEADS:
        return "'" + value
    return value


def _rows_to_csv(columns: list[str], rows: list[list[Any]]) -> str:
    """Render *columns* + *rows* as an RFC 4180 CSV string (header + data rows).

    Uses :mod:`csv` with default dialect (comma delimiter, CRLF line terminator,
    quoting on demand). ``None`` values are rendered as the empty string, matching
    standard SQL NULL export behaviour. Pure — unit-tested without a live DB.

    String cells whose first character is a spreadsheet formula lead (``=``,
    ``+``, ``-``, ``@``, ``\\t``, ``\\r``) are prefixed with a single quote to
    neutralise CSV-injection if the export is opened in Excel / LibreOffice /
    Google Sheets. Headers are also passed through this filter — an attacker
    who can name a SQL column can otherwise smuggle a formula in via the
    header row.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([_neutralize_csv_cell(c) for c in columns])
    for row in rows:
        writer.writerow(["" if v is None else _neutralize_csv_cell(v) for v in row])
    return buf.getvalue()


class SqlClient:
    """Read-only SQL client bound to a config's ``sqlconnect`` block."""

    def __init__(self, cfg: dict, sql_cfg: SqlConnectConfig, config_path: str = "config.yaml") -> None:
        self.cfg = cfg
        self.sql_cfg = sql_cfg
        self.config_path = config_path

    def _guard_op(self, op: str) -> None:
        if op not in self.sql_cfg.allowed_sql_ops:
            raise SqlConnectError(
                f"sql op {op!r} is not in allowed_sql_ops",
                code="SQLCONNECT_OP_NOT_ALLOWED",
                details={"op": op, "allowed": list(self.sql_cfg.allowed_sql_ops)},
            )

    def _dsn(self) -> str:
        dsn = os.environ.get(self.sql_cfg.dsn_env)
        if not dsn:
            raise SqlConnectRuntimeError(
                f"DSN env var {self.sql_cfg.dsn_env!r} is not set",
                details={"dsn_env": self.sql_cfg.dsn_env},
            )
        return dsn

    def _import_driver(self) -> Any:
        if self.sql_cfg.driver == "postgres":
            try:
                import psycopg  # noqa: PLC0415 -- lazy: disabled connector needs no driver
            except ImportError as exc:
                raise SqlDriverNotInstalledError(
                    "psycopg is not installed (pip install 'psycopg[binary]')",
                    details={"driver": "postgres"},
                ) from exc
            return psycopg
        try:
            import pyodbc  # noqa: PLC0415 -- lazy
        except ImportError as exc:
            raise SqlDriverNotInstalledError(
                "pyodbc is not installed (pip install pyodbc)",
                details={"driver": "mssql"},
            ) from exc
        return pyodbc

    def _apply_statement_timeout(self, conn: Any, cur: Any) -> None:
        """Enforce ``statement_timeout_ms`` on the session before the user query.

        Without this, a slow/pathological read-only SELECT (e.g. a cartesian join)
        runs unbounded -- the configured timeout would be a no-op. Driver-specific:
        Postgres applies a session GUC via ``set_config`` (parameterized -- ``SET``
        itself rejects bind params); MSSQL/pyodbc sets the per-query timeout on the
        connection (seconds). ``<= 0`` disables the cap explicitly.
        """
        timeout_ms = int(self.sql_cfg.statement_timeout_ms)
        if timeout_ms <= 0:
            return
        if self.sql_cfg.driver == "postgres":
            # SET statement_timeout cannot take a bind parameter; set_config can.
            cur.execute("SELECT set_config('statement_timeout', %s, false)", (str(timeout_ms),))
        else:  # mssql / pyodbc: query timeout is in seconds on the connection
            with suppress_attr_error():
                conn.timeout = max(1, timeout_ms // 1000)

    def _enforce_read_only(self, driver: Any, conn: Any) -> None:
        """Fail closed: the read-only session must actually take effect.

        psycopg exposes a settable ``read_only`` property; pyodbc does not --
        it enforces read-only via ``SQL_ATTR_ACCESS_MODE = SQL_MODE_READ_ONLY``
        on the connection. Silently ignoring ``AttributeError`` here would mean
        a driver without the property runs on a *read-write* session while the
        connector claims to be read-only (fail-open), so a driver that
        supports neither mechanism raises instead of executing the query.
        """
        try:
            conn.read_only = True
            return  # psycopg
        except AttributeError:
            pass
        # pyodbc (MSSQL): set the ODBC access mode on the connection.
        set_attr = getattr(conn, "set_attr", None)
        access_mode = getattr(driver, "SQL_ATTR_ACCESS_MODE", None)
        mode_read_only = getattr(driver, "SQL_MODE_READ_ONLY", None)
        if callable(set_attr) and access_mode is not None and mode_read_only is not None:
            try:
                set_attr(access_mode, mode_read_only)
            except Exception as exc:
                raise SqlConnectRuntimeError(
                    "failed to set the connection read-only (SQL_ATTR_ACCESS_MODE=SQL_MODE_READ_ONLY)",
                    details={"driver": self.sql_cfg.driver},
                ) from exc
            return
        raise SqlConnectRuntimeError(
            "SQL driver does not support read-only sessions; refusing to run on a read-write connection",
            details={"driver": self.sql_cfg.driver},
        )

    def _execute(self, sql: str, params: tuple = ()) -> dict:  # pragma: no cover - needs live DB
        driver = self._import_driver()
        dsn = self._dsn()
        conn = driver.connect(dsn)
        try:
            self._enforce_read_only(driver, conn)
            cur = conn.cursor()
            self._apply_statement_timeout(conn, cur)
            cur.execute(sql, params)
            cols, col_types = _columns_and_types(cur.description)
            rows = cur.fetchmany(self.sql_cfg.max_rows + 1)
            truncated = len(rows) > self.sql_cfg.max_rows
            rows = rows[: self.sql_cfg.max_rows]
            return {
                "columns": cols,
                "column_types": col_types,
                "rows": [list(r) for r in rows],
                "row_count": len(rows),
                "truncated": truncated,
            }
        finally:
            conn.close()

    def schema_list(self) -> dict:
        self._guard_op("schema_list")
        audit_log({"event": "sqlconnect_read", "op": "schema_list"}, self.config_path)
        sql = "SELECT table_schema, table_name FROM information_schema.tables ORDER BY table_schema, table_name"
        return {"op": "schema_list", **self._execute(sql)}

    def table_preview(self, table: str) -> dict:
        self._guard_op("table_preview")
        ident = quote_identifier(table, self.sql_cfg.driver)
        audit_log({"event": "sqlconnect_read", "op": "table_preview", "table": table}, self.config_path)
        # ident is allow-list-validated + quoted (validate_identifier/quote_identifier)
        # and max_rows is coerced to int; no untrusted text reaches the SQL string.
        if self.sql_cfg.driver == "mssql":
            sql = f"SELECT TOP {int(self.sql_cfg.max_rows)} * FROM {ident}"  # noqa: S608
        else:
            sql = f"SELECT * FROM {ident} LIMIT {int(self.sql_cfg.max_rows)}"  # noqa: S608
        return {"op": "table_preview", "table": table, **self._execute(sql)}

    def run_select(self, sql: str, fmt: Literal["json", "csv"] = "json") -> dict:
        self._guard_op("run_select")
        cleaned = assert_read_only_sql(sql)  # pure guard, runs before any connection
        audit_log({"event": "sqlconnect_read", "op": "run_select", "fmt": fmt}, self.config_path)
        result = self._execute(cleaned)
        if fmt == "csv":
            return {"op": "run_select", "format": "csv", "csv": _rows_to_csv(result["columns"], result["rows"])}
        return {"op": "run_select", **result}

    def explain(self, sql: str) -> dict:
        """Return the query plan for a read-only SELECT/WITH (Postgres only).

        The inner statement passes the same SELECT-only guard as ``run_select``,
        and plain ``EXPLAIN`` (no ``ANALYZE``) only *plans* it -- it never executes
        the query, so no DML can hide inside. MSSQL has no single-statement
        ``EXPLAIN`` equivalent (it uses a session ``SET SHOWPLAN`` toggle), so this
        op is refused for the mssql driver rather than emitting invalid SQL.
        """
        self._guard_op("explain")
        if self.sql_cfg.driver == "mssql":
            raise SqlConnectError(
                "explain is not supported for the mssql driver",
                code="SQLCONNECT_OP_NOT_ALLOWED",
                details={"driver": "mssql"},
            )
        cleaned = assert_read_only_sql(sql)  # pure guard, runs before any connection
        audit_log({"event": "sqlconnect_read", "op": "explain"}, self.config_path)
        return {"op": "explain", **self._execute(f"EXPLAIN {cleaned}")}

    def row_count(self, table: str) -> dict:
        """Return ``count(*)`` for a table without materialising its rows."""
        self._guard_op("row_count")
        ident = quote_identifier(table, self.sql_cfg.driver)
        audit_log({"event": "sqlconnect_read", "op": "row_count", "table": table}, self.config_path)
        # ident is allow-list-validated + driver-quoted; no untrusted text reaches SQL.
        sql = f"SELECT count(*) AS row_count FROM {ident}"  # noqa: S608
        return {"op": "row_count", "table": table, **self._execute(sql)}


class suppress_attr_error:  # pragma: no cover - trivial helper used only in live path
    """Context manager: ignore AttributeError when a driver lacks a conn attr.

    Only for best-effort knobs (e.g. ``conn.timeout``); the read-only session
    itself is enforced fail-closed by :meth:`SqlClient._enforce_read_only`.
    """

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, *_: object) -> bool:
        return exc_type is AttributeError


__all__ = ["SqlClient", "_rows_to_csv", "assert_read_only_sql", "validate_identifier", "quote_identifier"]
