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
# ``updlock``/``holdlock``/``xlock``/``tablock``/``tablockx``/``paglock``/
# ``serializable`` are MSSQL table hints that take write-grade or aggressively
# escalated locks inside an otherwise valid SELECT (``SELECT * FROM t WITH
# (UPDLOCK)``). On a connector whose contract is read-only that is an
# availability risk (blocking other writers/readers), so the hints are
# forbidden even though the statement itself only reads.
#
# The ``pg_``/``lo_``/``dblink`` group extends that same reasoning one step. Every
# name there is read-only *from the database's point of view*, so none of the
# gates above stop it: the statement is a single SELECT, starts with SELECT, has
# no stacked separator, and the read-only session happily executes it. What they
# reach is OUTSIDE the database:
#
#   * ``pg_read_*`` (``pg_read_file``, ``pg_read_binary_file``), ``pg_ls_*``
#     (``pg_ls_dir``, ``pg_ls_waldir``, ...), ``pg_stat_file``, and the
#     large-object ``lo_import`` / ``lo_export`` read (or write) the DB HOST's
#     filesystem -- a file-disclosure primitive dressed as a SELECT. The
#     prefixes are wildcarded because the family keeps growing; ``pg_stat_file``
#     is spelled out rather than ``pg_stat_\w+`` so the harmless catalog views
#     (``pg_stat_activity`` and friends) stay readable. ``lo_get``/``lo_put`` are
#     likewise left alone -- they move bytes within the database, not to disk.
#   * ``dblink*`` opens an outbound connection from the DB host: SSRF plus a
#     second session this connector's read-only enforcement never touches.
#   * ``pg_sleep`` (and the ``pg_terminate_backend``/``pg_cancel_backend`` pair)
#     is the availability argument the MSSQL lock hints above are already
#     blocked for, one line over.
#
# All of these need superuser or an installed extension, so on a correctly
# provisioned read-only role they fail anyway -- this is defense in depth for the
# case where the DSN points at an over-privileged account, which is exactly the
# case a read-only connector exists to contain. ``\w*`` on the prefixes catches
# the family members (``dblink_connect``, ``dblink_send_query``) that a bare
# ``\b`` would let through, since ``_`` is a word character.
_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|call|"
    r"exec|execute|into|copy|vacuum|attach|begin|commit|rollback|"
    r"updlock|holdlock|xlock|tablockx|tablock|paglock|serializable)\b",
    re.IGNORECASE,
)

# The side-effect FUNCTION names are scanned separately, against a copy that keeps
# quoted-identifier text (see _strip_quoted's keep_identifiers). They cannot share
# _FORBIDDEN_RE's scan: that one reads a copy with every quoted region blanked, so
# `SELECT "pg_sleep"(600)` and `SELECT pg_catalog."pg_read_file"('/etc/passwd')`
# slipped straight through -- Postgres folds unquoted identifiers to lower case, so
# a lower-case quoted identifier resolves to the very same built-in.
#
# The split is the point, not an implementation detail. _FORBIDDEN_RE's members are
# STATEMENT keywords, and a quoted identifier can never be one: `SELECT "delete"
# FROM t` is a column named delete, which is exactly why quoted regions are blanked
# for that scan. The names below are FUNCTIONS, and a function name written as a
# quoted identifier is still a call. Same text, opposite meaning, so they need
# opposite treatment.
_FORBIDDEN_FN_RE = re.compile(
    r"\b(pg_read_\w+|pg_ls_\w+|pg_stat_file|"
    r"lo_import|lo_export|dblink\w*|pg_sleep|"
    r"pg_terminate_backend|pg_cancel_backend)\b",
    re.IGNORECASE,
)

# Quoted regions can legitimately contain SQL keywords or comment/``;``
# punctuation as *data* or as a quoted column name -- e.g.
# ``SELECT 'please do not delete'`` or ``SELECT "delete" FROM t``. Those are never
# executable SQL, so the structural guards below scan a copy with quoted regions
# blanked out to avoid false-rejecting valid read queries. Real DML, comments and
# stacked statements always live OUTSIDE quotes, so the ``WITH (DELETE ... RETURNING)``
# CTE bypass and ``--``/``/* */`` comment hiding are still caught.
#
# This MUST be a single left-to-right scan rather than a regex alternation.
# The previous ``re.sub`` of ``'...'|"..."`` did not know about the other
# quoting forms, so a quote character sitting INSIDE one of them opened a
# phantom region that swallowed real SQL:
#
#     SELECT $$'$$ ; DROP TABLE t ; SELECT $$'$$
#          -> scanned as  SELECT $$ $$   (the ';' and 'DROP' are gone)
#
# and the guard accepted it as a single SELECT. Same shape with MSSQL bracket
# identifiers (``SELECT [a'b] ; DROP TABLE t ; SELECT [c'd]``). Scanning left
# to right gives each opener the precedence the database gives it, so a quote
# inside another quoted region is data, not an opener.
_SIMPLE_QUOTES = {"'": "'", '"': '"', "[": "]"}
# ``$$``/``$tag$`` -- Postgres dollar-quoting. A bare ``$1`` placeholder does not
# match (no closing ``$``), so it falls through and is emitted literally.
_DOLLAR_TAG_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
# Postgres escape-string literals (``E'a\'b'``) are the one form where a
# backslash escapes the closing quote. Rather than teach the scanner a second
# escape convention, they are refused outright -- the same call the guard
# already makes for SQL comments, and for the same reason: a read-only preview
# never needs one, so rejecting beats parsing. The check is made INSIDE the
# scan, against the unquoted text emitted so far, because a plain string may
# legitimately end in an E (``SELECT 'grade E' AS g``) and a raw pre-pass over
# the whole statement rejected exactly those.
_IDENT_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_$")


def _skip_simple_quote(sql: str, start: int, opener: str) -> int:
    """Index just past the region opened by ``opener`` at ``start``.

    Covers the three doubled-escape forms: ``'...''...'`` string literals,
    ``"..."" ..."`` quoted identifiers, and ``[...]]...]`` MSSQL bracket
    identifiers. Raises on an unterminated region.
    """
    closer = _SIMPLE_QUOTES[opener]
    cursor = start + 1
    while True:
        end = sql.find(closer, cursor)
        if end < 0:
            raise SqlConnectError(
                f"unterminated {opener!r} quoted region in SQL",
                code="SQLCONNECT_BAD_QUERY",
            )
        if sql[end + 1:end + 2] == closer:
            cursor = end + 2  # doubled closer escapes itself; keep going
            continue
        return end + 1


def _skip_dollar_quote(sql: str, start: int) -> int | None:
    """Index just past a ``$tag$...$tag$`` region, or None if this is not one."""
    opener = _DOLLAR_TAG_RE.match(sql, start)
    if opener is None:
        return None
    tag = opener.group(0)
    end = sql.find(tag, opener.end())
    if end < 0:
        raise SqlConnectError(
            f"unterminated {tag} dollar-quoted region in SQL",
            code="SQLCONNECT_BAD_QUERY",
        )
    return end + len(tag)


def _is_escape_string_prefix(emitted: list[str]) -> bool:
    """True if the unquoted text so far ends in a standalone ``E``/``e`` token.

    ``emitted`` holds only characters seen OUTSIDE a quoted region, so this
    distinguishes the ``E'...'`` escape-string prefix from an ``E`` that is
    merely the last character of an ordinary string literal.
    """
    if not emitted or emitted[-1] not in {"E", "e"}:
        return False
    return len(emitted) < 2 or emitted[-2] not in _IDENT_CHARS


def _strip_quoted(sql: str, *, keep_identifiers: bool = False) -> str:
    """Blank out every quoted region so the structural guards scan only real SQL.

    Each region collapses to a single space, preserving token boundaries.
    Handles single quotes, double-quoted identifiers, MSSQL bracket
    identifiers and Postgres dollar-quoting in one left-to-right pass, so no
    quoting form can hide a statement separator or a forbidden keyword inside
    another. Unterminated regions raise rather than swallow the remainder.

    ``keep_identifiers`` emits the *contents* of identifier quoting (``"..."``,
    ``[...]``) instead of blanking it, while STRING literals (``'...'``,
    ``$tag$...$tag$``) are still blanked. Only ``_FORBIDDEN_FN_RE`` reads that
    variant -- see its comment for why a quoted function name must stay visible
    while a quoted column name must not. The structural checks (``;``, comments)
    and ``_FORBIDDEN_RE`` keep reading the fully-blanked copy, so none of the
    stacked-statement / comment-hiding defenses are weakened by this.
    """
    out: list[str] = []
    cursor = 0
    while cursor < len(sql):
        char = sql[cursor]
        if char in _SIMPLE_QUOTES:
            if char == "'" and _is_escape_string_prefix(out):
                raise SqlConnectError(
                    "escape-string literals (E'...') are not allowed in read-only queries",
                    code="SQLCONNECT_BAD_QUERY",
                )
            end = _skip_simple_quote(sql, cursor, char)
            if keep_identifiers and char != "'":
                # Padded with spaces on BOTH sides so the emitted text can never
                # fuse with an adjacent token, and so _is_escape_string_prefix
                # never sees an identifier's last character as out[-1] (a column
                # named "gradeE" must not turn the next literal into an E'...').
                out.append(f" {sql[cursor + 1:end - 1]} ")
            else:
                out.append(" ")
            cursor = end
            continue
        dollar_end = _skip_dollar_quote(sql, cursor) if char == "$" else None
        if dollar_end is None:
            out.append(char)
            cursor += 1
        else:
            out.append(" ")
            cursor = dollar_end
    return "".join(out)


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
    # Second pass for the side-effect FUNCTIONS, against a copy that keeps
    # quoted-identifier text. Quoting a function name does not change which
    # function Postgres calls, so this scan must see through `"pg_sleep"` and
    # `pg_catalog."pg_read_file"` -- while string literals stay blanked, so a
    # query that merely mentions one of these names as data is still fine.
    fn_hit = _FORBIDDEN_FN_RE.search(_strip_quoted(cleaned, keep_identifiers=True))
    if fn_hit:
        raise SqlConnectError(
            f"forbidden keyword in read-only query: {fn_hit.group(0)!r}",
            code="SQLCONNECT_BAD_QUERY",
            details={"keyword": fn_hit.group(0)},
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
