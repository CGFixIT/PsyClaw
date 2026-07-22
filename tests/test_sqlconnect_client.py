"""Tests for agentic.sqlconnect.client guards (pure) + driver/DSN handling."""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from agentic.sqlconnect.client import (
    SqlClient,
    _columns_and_types,
    _rows_to_csv,
    assert_read_only_sql,
    quote_identifier,
    validate_identifier,
)
from agentic.sqlconnect.config import SqlConnectConfig
from utils.errors import (
    SqlConnectError,
    SqlConnectRuntimeError,
    SqlDriverNotInstalledError,
)


def test_assert_accepts_select_and_with():
    assert assert_read_only_sql("SELECT 1") == "SELECT 1"
    assert assert_read_only_sql("  with t as (select 1) select * from t ;").startswith("with")


@pytest.mark.parametrize(
    "bad",
    [
        "DELETE FROM users",
        "DROP TABLE t",
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET x=1",
        "SELECT 1; DROP TABLE t",
        "SELECT * INTO newt FROM t",
        "EXEC sp_who",
        "",
        # SQL comments are a stacked-statement / keyword-hiding vector and are refused.
        "SELECT 1 -- harmless",
        "SELECT 1 /* DROP TABLE t */",
        "SELECT 1 /* ; */ FROM t",
    ],
)
def test_assert_rejects_non_readonly(bad):
    with pytest.raises(SqlConnectError):
        assert_read_only_sql(bad)


@pytest.mark.parametrize(
    "good",
    [
        "SELECT replace(name, 'a', 'b') FROM t",
        "select id, REPLACE(path, '\\\\', '/') as p from files",
        "WITH x AS (SELECT replace(c, ' ', '_') AS c FROM t) SELECT * FROM x",
    ],
)
def test_assert_allows_replace_read_function(good):
    """``replace()`` is a read-only scalar in Postgres/MSSQL and must not be blocked."""
    assert assert_read_only_sql(good).lower().startswith(("select", "with"))


@pytest.mark.parametrize(
    "good",
    [
        # String literals / quoted identifiers may legitimately contain SQL keywords
        # or punctuation -- they are data / column names, not executable statements.
        "SELECT 'please do not delete' AS note",
        "SELECT * FROM t WHERE name = 'create account'",
        'SELECT "delete" FROM t',  # forbidden word as a quoted identifier
        "SELECT 'a;b' AS x",  # semicolon inside a literal is not a 2nd statement
        "SELECT 'rate is 5/*2' AS note",  # comment marker inside a literal is just data
        "SELECT 'it''s fine' AS note",  # doubled-quote escape inside a literal
    ],
)
def test_assert_allows_keywords_inside_quoted_literals(good):
    """A keyword/punctuation inside a quoted literal or identifier must not be blocked."""
    assert assert_read_only_sql(good).lower().startswith(("select", "with"))


@pytest.mark.parametrize(
    "bad",
    [
        # The classic CTE-DML bypass: starts with WITH but performs a write. The DML
        # keyword is OUTSIDE quotes, so quote-stripping must not let it through.
        "WITH t AS (DELETE FROM users RETURNING *) SELECT * FROM t",
        "WITH t AS (UPDATE users SET x=1 RETURNING *) SELECT * FROM t",
        "WITH t AS (INSERT INTO users VALUES (1) RETURNING *) SELECT * FROM t",
        # Stacked statement / real comment outside quotes still caught after stripping.
        "SELECT * FROM t; DROP TABLE x",
        "SELECT * FROM t -- DROP TABLE x",
    ],
)
def test_assert_still_rejects_dml_outside_quotes(bad):
    """Quote-stripping must not reopen the CTE-DML / stacked-statement vectors."""
    with pytest.raises(SqlConnectError):
        assert_read_only_sql(bad)


def test_assert_rejects_comments_with_specific_code():
    """Comment rejection fires before the keyword/multi-statement guards."""
    with pytest.raises(SqlConnectError) as exc:
        assert_read_only_sql("SELECT * FROM t -- /* sneaky */ DROP TABLE x")
    assert exc.value.code == "SQLCONNECT_BAD_QUERY"
    assert "comment" in str(exc.value).lower()


def test_validate_identifier():
    assert validate_identifier("schema.table") == "schema.table"
    for bad in ("1bad", "a;b", "a-b", "drop table"):
        with pytest.raises(SqlConnectError):
            validate_identifier(bad)


def test_quote_identifier_per_driver():
    assert quote_identifier("s.t", "postgres") == '"s"."t"'
    assert quote_identifier("s.t", "mssql") == "[s].[t]"


def test_op_guard():
    sc = SqlConnectConfig(allowed_sql_ops=["schema_list"])
    client = SqlClient({}, sc)
    with pytest.raises(SqlConnectError):
        client.run_select("SELECT 1")


def test_run_select_bad_sql_before_connect():
    sc = SqlConnectConfig()
    client = SqlClient({}, sc)
    with pytest.raises(SqlConnectError):
        client.run_select("DELETE FROM t")  # guard fires before any DB work


def test_driver_absent():
    sc = SqlConnectConfig(driver="postgres")
    client = SqlClient({}, sc)
    with pytest.raises(SqlDriverNotInstalledError):
        client._import_driver()  # psycopg not installed in this env


def test_dsn_missing(monkeypatch):
    sc = SqlConnectConfig()
    monkeypatch.delenv(sc.dsn_env, raising=False)
    client = SqlClient({}, sc)
    with pytest.raises(SqlConnectRuntimeError):
        client._dsn()


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple] = []
        self.description = [("col",)]

    def execute(self, sql, params=()) -> None:
        self.executed.append((sql, params))

    def fetchmany(self, n):
        return [("v",)]


class _FakeConn:
    def __init__(self) -> None:
        self.read_only = False
        self.timeout = None
        self.cur = _FakeCursor()

    def cursor(self):
        return self.cur

    def close(self) -> None:
        pass


class _FakeDriver:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def connect(self, dsn):
        return self.conn


def test_execute_applies_statement_timeout_postgres(monkeypatch):
    sc = SqlConnectConfig(driver="postgres", statement_timeout_ms=7000)
    monkeypatch.setenv(sc.dsn_env, "postgresql://x")
    client = SqlClient({}, sc)
    fake = _FakeDriver()
    monkeypatch.setattr(client, "_import_driver", lambda: fake)
    client._execute("SELECT 1")
    # The timeout is applied via set_config BEFORE the user query runs.
    first_sql, first_params = fake.conn.cur.executed[0]
    assert "set_config" in first_sql.lower()
    assert "statement_timeout" in first_sql.lower()
    assert first_params == ("7000",)
    assert fake.conn.cur.executed[1][0] == "SELECT 1"


def test_execute_applies_query_timeout_mssql(monkeypatch):
    sc = SqlConnectConfig(driver="mssql", statement_timeout_ms=8000)
    monkeypatch.setenv(sc.dsn_env, "Driver=ODBC;")
    client = SqlClient({}, sc)
    fake = _FakeDriver()
    monkeypatch.setattr(client, "_import_driver", lambda: fake)
    client._execute("SELECT 1")
    # MSSQL uses the connection query timeout (seconds), not a SET statement.
    assert fake.conn.timeout == 8
    assert not any("statement_timeout" in s.lower() for s, _ in fake.conn.cur.executed)


def test_execute_timeout_disabled_when_non_positive(monkeypatch):
    sc = SqlConnectConfig(driver="postgres")
    sc.statement_timeout_ms = 0  # bypass post-init validation to exercise the disabled branch
    monkeypatch.setenv(sc.dsn_env, "postgresql://x")
    client = SqlClient({}, sc)
    fake = _FakeDriver()
    monkeypatch.setattr(client, "_import_driver", lambda: fake)
    client._execute("SELECT 1")
    # No timeout SET issued; only the user query runs.
    assert fake.conn.cur.executed == [("SELECT 1", ())]


# ---------------------------------------------------------------------------
# read-only enforcement: fail-closed, never a silently read-write session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        # MSSQL table hints that take write-grade locks inside a valid SELECT.
        "SELECT * FROM t WITH (UPDLOCK)",
        "SELECT * FROM t WITH (HOLDLOCK)",
        "SELECT * FROM t WITH (XLOCK)",
        "select * from t with (updlock, holdlock)",
        "SELECT * FROM t WITH (NOLOCK, XLOCK)",
        "SELECT * FROM t WITH (TABLOCKX)",
        "SELECT * FROM t WITH (TABLOCK)",
        "SELECT * FROM t WITH (PAGLOCK)",
        "SELECT * FROM t WITH (SERIALIZABLE)",
        "select * from t with (serializable)",
    ],
)
def test_assert_rejects_lock_taking_hints(bad):
    """Lock hints are forbidden even though the statement only reads."""
    with pytest.raises(SqlConnectError):
        assert_read_only_sql(bad)


def test_execute_sets_read_only_for_psycopg_style_driver(monkeypatch):
    sc = SqlConnectConfig(driver="postgres")
    monkeypatch.setenv(sc.dsn_env, "postgresql://x")
    client = SqlClient({}, sc)
    fake = _FakeDriver()
    monkeypatch.setattr(client, "_import_driver", lambda: fake)
    client._execute("SELECT 1")
    assert fake.conn.read_only is True


class _NoReadOnlyConn:
    """pyodbc-like connection: no settable ``read_only`` property."""

    def __init__(self) -> None:
        self.cur = _FakeCursor()
        self.attrs: dict = {}

    @property
    def read_only(self):  # read-only property -> assignment raises AttributeError
        return False

    def cursor(self):
        return self.cur

    def close(self) -> None:
        pass


class _PyodbcConn(_NoReadOnlyConn):
    """pyodbc-like connection that supports ``set_attr`` (SQLSetConnectAttr)."""

    def set_attr(self, attr, value) -> None:
        self.attrs[attr] = value


def test_execute_readonly_pyodbc_uses_access_mode(monkeypatch):
    """A driver without ``read_only`` gets SQL_ATTR_ACCESS_MODE=READ_ONLY instead."""
    sc = SqlConnectConfig(driver="mssql")
    monkeypatch.setenv(sc.dsn_env, "Driver=ODBC;")
    client = SqlClient({}, sc)
    conn = _PyodbcConn()
    fake = SimpleNamespace(
        connect=lambda dsn: conn,
        SQL_ATTR_ACCESS_MODE=101,
        SQL_MODE_READ_ONLY=1,
    )
    monkeypatch.setattr(client, "_import_driver", lambda: fake)
    client._execute("SELECT 1")
    assert conn.attrs == {101: 1}


def test_execute_readonly_fails_closed_when_unsupported(monkeypatch):
    """A driver with neither read_only nor set_attr must refuse to run."""
    sc = SqlConnectConfig(driver="mssql")
    monkeypatch.setenv(sc.dsn_env, "Driver=ODBC;")
    client = SqlClient({}, sc)
    conn = _NoReadOnlyConn()
    fake = SimpleNamespace(connect=lambda dsn: conn)  # no SQL_ATTR_ACCESS_MODE consts
    monkeypatch.setattr(client, "_import_driver", lambda: fake)
    with pytest.raises(SqlConnectRuntimeError, match="read-only"):
        client._execute("SELECT 1")
    # Fail-closed means the user query never reached the connection.
    assert conn.cur.executed == []


def test_execute_readonly_fails_closed_when_set_attr_rejected(monkeypatch):
    """If the driver rejects the read-only attribute, refuse to run."""

    class _RejectingConn(_PyodbcConn):
        def set_attr(self, attr, value) -> None:
            raise RuntimeError("driver rejected the attribute")

    sc = SqlConnectConfig(driver="mssql")
    monkeypatch.setenv(sc.dsn_env, "Driver=ODBC;")
    client = SqlClient({}, sc)
    conn = _RejectingConn()
    fake = SimpleNamespace(
        connect=lambda dsn: conn,
        SQL_ATTR_ACCESS_MODE=101,
        SQL_MODE_READ_ONLY=1,
    )
    monkeypatch.setattr(client, "_import_driver", lambda: fake)
    with pytest.raises(SqlConnectRuntimeError, match="read-only"):
        client._execute("SELECT 1")
    assert conn.cur.executed == []


# ---------------------------------------------------------------------------
# column type metadata
# ---------------------------------------------------------------------------


def test_columns_and_types_renders_portable_type_names():
    # pyodbc-style: type_code is a Python type -> its __name__.
    assert _columns_and_types([("id", int), ("name", str)]) == (["id", "name"], ["int", "str"])
    # psycopg-style: type_code is a numeric OID -> stringified.
    assert _columns_and_types([("c", 23)]) == (["c"], ["23"])
    # non-row statement (description is None) and a description row without a
    # type code both degrade gracefully rather than raising.
    assert _columns_and_types(None) == ([], [])
    assert _columns_and_types([("c",)]) == (["c"], ["None"])


def test_execute_result_includes_column_types(monkeypatch):
    sc = SqlConnectConfig(driver="postgres")
    monkeypatch.setenv(sc.dsn_env, "postgresql://x")
    client = SqlClient({}, sc)
    fake = _FakeDriver()
    monkeypatch.setattr(client, "_import_driver", lambda: fake)
    res = client._execute("SELECT 1")
    assert res["columns"] == ["col"]  # from _FakeCursor.description
    assert "column_types" in res and isinstance(res["column_types"], list)


# ---------------------------------------------------------------------------
# explain + row_count read-only ops
# ---------------------------------------------------------------------------


def test_explain_wraps_a_guarded_select_postgres(monkeypatch):
    sc = SqlConnectConfig(driver="postgres")
    monkeypatch.setenv(sc.dsn_env, "postgresql://x")
    client = SqlClient({}, sc)
    fake = _FakeDriver()
    monkeypatch.setattr(client, "_import_driver", lambda: fake)
    res = client.explain("SELECT 1")
    assert res["op"] == "explain"
    # EXPLAIN-wraps the guard-cleaned statement; runs after the timeout SET.
    assert fake.conn.cur.executed[-1][0] == "EXPLAIN SELECT 1"


def test_explain_rejects_dml_before_connect():
    client = SqlClient({}, SqlConnectConfig(driver="postgres"))
    with pytest.raises(SqlConnectError):
        client.explain("DELETE FROM t")  # SELECT-only guard fires before any DB work


def test_explain_refused_for_mssql():
    # MSSQL has no single-statement EXPLAIN; the op is refused, never emitted.
    client = SqlClient({}, SqlConnectConfig(driver="mssql"))
    with pytest.raises(SqlConnectError):
        client.explain("SELECT 1")


def test_explain_op_guard_when_not_allowlisted():
    client = SqlClient({}, SqlConnectConfig(allowed_sql_ops=["schema_list"]))
    with pytest.raises(SqlConnectError):
        client.explain("SELECT 1")


def test_row_count_builds_count_star(monkeypatch):
    sc = SqlConnectConfig(driver="postgres")
    monkeypatch.setenv(sc.dsn_env, "postgresql://x")
    client = SqlClient({}, sc)
    fake = _FakeDriver()
    monkeypatch.setattr(client, "_import_driver", lambda: fake)
    res = client.row_count("public.users")
    assert res["op"] == "row_count" and res["table"] == "public.users"
    assert fake.conn.cur.executed[-1][0] == 'SELECT count(*) AS row_count FROM "public"."users"'


def test_row_count_rejects_bad_identifier_before_connect():
    client = SqlClient({}, SqlConnectConfig(driver="postgres"))
    with pytest.raises(SqlConnectError):
        client.row_count("a; DROP")  # identifier guard fires before any DB work


# ---------------------------------------------------------------------------
# CSV rendering
# ---------------------------------------------------------------------------


def test_rows_to_csv_produces_header_and_data_rows():
    out = _rows_to_csv(["id", "name"], [[1, "alice"], [2, "bob"]])
    lines = out.splitlines()
    assert lines[0] == "id,name"
    assert lines[1] == "1,alice"
    assert lines[2] == "2,bob"


def test_rows_to_csv_quotes_values_with_commas():
    out = _rows_to_csv(["v"], [["hello, world"]])
    assert '"hello, world"' in out


def test_rows_to_csv_renders_none_as_empty_string():
    out = _rows_to_csv(["a", "b"], [[None, "x"]])
    assert out.splitlines()[1] == ",x"


def test_rows_to_csv_empty_rows_gives_header_only():
    out = _rows_to_csv(["col"], [])
    assert out.strip() == "col"


# ---------------------------------------------------------------------------
# CSV formula-injection neutralisation: a cell starting with =/+/-/@/tab/CR
# would execute as a formula in Excel / LibreOffice / Google Sheets if the
# export were opened in a spreadsheet. The cell is prefixed with a single
# quote (OWASP-recommended fix) which spreadsheet apps silently drop on
# display, so the cell renders as the original text but never as a formula.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "=SUM(A1:A9)",
        "+cmd|'/c calc'!A0",
        "-2+5",
        "@HYPERLINK",
        "\tprefix",
        "\rcarriage",
    ],
)
def test_rows_to_csv_neutralises_formula_leads(payload):
    """Each formula-lead payload must have a single quote prepended in the
    rendered CSV (regardless of what splitlines / csv.reader do with the
    embedded control character afterwards). Inspect the raw bytes."""
    out = _rows_to_csv(["v"], [[payload]])
    # The first byte of the data row (after the "v\r\n" header) is the cell
    # contents. Skip the header line and any quoting csv adds for control
    # chars by simply asserting the apostrophe-prefixed payload appears
    # somewhere in the rendered output and the BARE payload does not.
    assert "'" + payload in out
    # The unprefixed payload must not appear at the very START of a cell —
    # i.e. immediately after the row-separator '\n'. This is the position
    # spreadsheet parsers read first.
    assert ("\n" + payload) not in out
    # Also confirm the header is intact (the neutraliser only fires on cell
    # leads, not column names that contain no formula chars).
    assert out.startswith("v\r\n")


def test_rows_to_csv_does_not_alter_safe_strings():
    """Cells that do NOT begin with a formula lead character pass through."""
    safe_payloads = ["alice", "0", "hello world", "  =leading-space", "x=2", ""]
    for s in safe_payloads:
        out = _rows_to_csv(["v"], [[s]])
        import csv as _csv
        parsed = list(_csv.reader(out.splitlines()))
        assert parsed[1][0] == s, s


def test_rows_to_csv_neutralises_formula_in_header():
    """A column name starting with a formula lead is also an injection vector
    (an attacker who can name a SQL column can smuggle the formula through
    the header row), so headers go through the same filter."""
    out = _rows_to_csv(["=cmd|x"], [["v"]])
    import csv as _csv
    parsed = list(_csv.reader(out.splitlines()))
    assert parsed[0][0] == "'=cmd|x"
    assert parsed[1][0] == "v"  # data row unchanged


def test_rows_to_csv_non_string_cells_pass_through():
    """Numbers and other scalars must not get an apostrophe prefix."""
    out = _rows_to_csv(["n"], [[42], [3.14], [True]])
    import csv as _csv
    parsed = list(_csv.reader(out.splitlines()))
    assert parsed[1][0] == "42"
    assert parsed[2][0] == "3.14"
    assert parsed[3][0] == "True"


def test_run_select_returns_csv_when_fmt_csv(monkeypatch):
    sc = SqlConnectConfig(driver="postgres")
    monkeypatch.setenv(sc.dsn_env, "postgresql://x")
    client = SqlClient({}, sc)
    fake = _FakeDriver()
    # Override description so _execute returns column 'col' with value 'v'
    monkeypatch.setattr(client, "_import_driver", lambda: fake)
    res = client.run_select("SELECT 1", fmt="csv")
    assert res["format"] == "csv"
    assert "csv" in res
    assert "col" in res["csv"]  # header row present
    assert "v" in res["csv"]  # data row present


def test_run_select_returns_json_by_default(monkeypatch):
    sc = SqlConnectConfig(driver="postgres")
    monkeypatch.setenv(sc.dsn_env, "postgresql://x")
    client = SqlClient({}, sc)
    fake = _FakeDriver()
    monkeypatch.setattr(client, "_import_driver", lambda: fake)
    res = client.run_select("SELECT 1")
    assert "rows" in res
    assert res.get("format") != "csv"
