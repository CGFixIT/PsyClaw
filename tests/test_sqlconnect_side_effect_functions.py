"""The read-only SQL guard must also refuse functions that reach outside the DB.

assert_read_only_sql's leading-keyword gate, single-statement check and
read-only session all judge a statement by what it does *to the database*. A
plain SELECT can still call server-side functions whose effect is on the DB
HOST -- reading its filesystem, opening an outbound connection, or parking a
backend -- and none of the existing gates see any of it.

These are read-only-from-the-database's-point-of-view, which is exactly why
they need to be named explicitly rather than inferred.
"""

from __future__ import annotations

import pytest

from agentic.sqlconnect.client import assert_read_only_sql
from utils.errors import SqlConnectError


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        ("SELECT pg_read_file('/etc/passwd')", "reads the DB host filesystem"),
        ("SELECT pg_read_binary_file('/etc/shadow')", "reads the DB host filesystem"),
        ("SELECT * FROM pg_ls_dir('/var/lib/postgresql')", "lists the DB host filesystem"),
        ("SELECT * FROM pg_ls_waldir()", "lists the DB host filesystem"),
        ("SELECT pg_stat_file('/etc/passwd')", "stats a DB host path"),
        ("SELECT lo_import('/etc/passwd')", "imports a DB host file"),
        ("SELECT lo_export(16384, '/tmp/out')", "writes to the DB host filesystem"),
        ("SELECT dblink('dbname=x', 'select 1')", "outbound connection / SSRF"),
        ("SELECT dblink_connect('host=169.254.169.254')", "outbound connection / SSRF"),
        ("SELECT dblink_send_query('c', 'select 1')", "outbound connection / SSRF"),
        ("SELECT pg_sleep(600)", "availability -- same argument as the MSSQL lock hints"),
        ("SELECT pg_terminate_backend(123)", "kills another session"),
        ("SELECT pg_cancel_backend(123)", "cancels another session"),
        ("WITH x AS (SELECT pg_read_file('/etc/passwd') AS c) SELECT * FROM x", "hidden in a CTE"),
        ("SELECT PG_READ_FILE('/etc/passwd')", "case-insensitive"),
    ],
)
def test_guard_rejects_side_effect_functions(bad, why):
    with pytest.raises(SqlConnectError) as excinfo:
        assert_read_only_sql(bad)
    assert excinfo.value.code == "SQLCONNECT_BAD_QUERY", why


@pytest.mark.parametrize(
    "good",
    [
        # The catalog views these names resemble are ordinary readable relations
        # and must not be caught by the new prefixes.
        "SELECT * FROM pg_stat_activity",
        "SELECT * FROM pg_stat_user_tables",
        "SELECT datname FROM pg_database",
        "SELECT * FROM pg_tables WHERE schemaname = 'public'",
        # Large-object accessors that move bytes inside the database only.
        "SELECT lo_get(16384)",
        # Ordinary reads that merely contain a substring of a blocked name.
        "SELECT sleeper_id FROM t",
        "SELECT * FROM readings",
        "SELECT name FROM t WHERE kind = 'dblink'",
    ],
)
def test_guard_still_allows_ordinary_reads(good):
    assert assert_read_only_sql(good).lower().startswith(("select", "with"))


@pytest.mark.parametrize(
    "bad",
    [
        # Postgres folds unquoted identifiers to lower case, so a lower-case
        # QUOTED identifier resolves to the very same built-in. _strip_quoted
        # blanks quoted regions before the keyword scan, so every one of these
        # walked straight past the guard until the scan was split.
        'SELECT "pg_sleep"(600)',
        'SELECT pg_catalog."pg_read_file"(\'/etc/passwd\')',
        'SELECT "pg_read_file"(\'/etc/passwd\')',
        'SELECT "dblink"(\'dbname=x\', \'select 1\')',
        'SELECT "lo_export"(16384, \'/tmp/out\')',
        'SELECT * FROM "pg_ls_dir"(\'/var/lib/postgresql\')',
        # MSSQL bracket identifiers are the same trick in the other dialect.
        "SELECT [pg_sleep](600)",
        # And hidden one level down inside a CTE.
        'WITH x AS (SELECT "pg_read_file"(\'/etc/passwd\') AS c) SELECT * FROM x',
    ],
)
def test_quoted_identifier_cannot_smuggle_a_side_effect_function(bad):
    with pytest.raises(SqlConnectError) as excinfo:
        assert_read_only_sql(bad)
    assert excinfo.value.code == "SQLCONNECT_BAD_QUERY"


@pytest.mark.parametrize(
    "good",
    [
        # A quoted identifier that merely collides with a STATEMENT keyword is a
        # column name, not a statement -- the original reason quoted regions are
        # blanked for _FORBIDDEN_RE. Splitting the scan must not regress this.
        'SELECT "delete" FROM t',
        'SELECT "select", "update" FROM t',
        'SELECT t."insert" AS i FROM t',
        "SELECT [delete] FROM t",
        # A quoted identifier ending in E must not turn the next literal into an
        # E'...' escape string once identifier text is emitted into the scan.
        "SELECT \"gradeE\", 'plain' FROM t",
    ],
)
def test_quoted_statement_keywords_are_still_ordinary_column_names(good):
    assert assert_read_only_sql(good).lower().startswith("select")


def test_blocked_name_inside_a_string_literal_is_data_not_sql():
    """Quoted regions are blanked before the keyword scan -- a literal that
    merely mentions a blocked function is not an attempt to call it."""
    sql = "SELECT 'ask an admin to run pg_read_file for you' AS hint"
    assert assert_read_only_sql(sql).startswith("SELECT")


def test_rejection_names_the_offending_keyword():
    """The error carries the matched keyword so an operator can see WHY."""
    with pytest.raises(SqlConnectError) as excinfo:
        assert_read_only_sql("SELECT pg_sleep(10)")
    assert excinfo.value.details["keyword"].lower() == "pg_sleep"
