"""Static guard: a SELECT-then-INSERT pair must handle the unique violation.

The bug class (PR #1216): a function pre-checks existence with a SELECT, then
INSERTs bare. The in-process lock around the pair orders only threads sharing
one manager -- the gateway, the harness and the cyclaw-user CLI each hold their
own AuthManager on the same DB file -- so a writer landing between the two
statements turns the constraint into a raw backend error
(sqlite3.IntegrityError, or psycopg's UniqueViolation; the two share no base
class). Raw, it escaped gate_auth's error ladder as a 500 and broke the CLI's
documented exit-code contract. The pre-check SELECT is the confession: its
author believed a uniqueness constraint exists, so the violation is reachable
and the INSERT must either sit in a ``try`` with a real handler or fold the
conflict into the SQL itself (``ON CONFLICT`` / ``OR IGNORE`` / ``OR REPLACE``).

Scope is ``utils/authn_manager.py`` ONLY, and that is a measured decision, not
an oversight. A full-repo inventory of every INSERT-executing function
(authn_manager, authn_store, personality, ratelimit, memory/store,
vector_store, sqlconnect) found every instance of the bug class in this one
module -- and found three functions elsewhere that are STRUCTURALLY identical
to the bug yet safe only because of schema facts a cheap rule cannot know:

  * ``utils/personality.py`` ``_load_soul`` -- same-table SELECT then bare
    INSERT, but ``soul_versions`` has an autoincrement PK and no UNIQUE column.
  * ``memory/store.py`` ``_insert_fact_conn`` -- capacity-check SELECT then
    bare INSERT, but ``facts.id`` is a rowid alias nobody supplies.
  * ``memory/store.py`` ``stage_episode`` -- ``idx_episodes_query_hash`` is a
    plain index, not UNIQUE.

Flagging those would be false positives; exonerating them mechanically needs a
DDL oracle (parse every backend's schema, map tables to violable constraints,
match INSERT targets and SELECT pre-check columns against them) plus resolution
of SQL hidden behind method calls (``ratelimit._upsert_sql()``) and helpers
(``memory.apply_proposal``). That is a static analyzer, not a repo guard; build
it only if the bug class ever appears in a second module. Here, every statement
flows through ``self._sql_*`` attributes whose shape is fixed (a string literal,
or an f-string whose leading fragment carries the verb), so a ~60-line detector
reaches zero false positives.

Lives in tests/ for the same reason tests/test_repo_hygiene.py gives: the three
test legs are release gates, while lint.yml is advisory end to end, so a guard
that must block a merge has to run here.
"""

from __future__ import annotations

import ast
from pathlib import Path

_TARGET = Path(__file__).resolve().parent.parent / "utils" / "authn_manager.py"

_CONFLICT_FOLDED = ("ON CONFLICT", "OR IGNORE", "OR REPLACE")


def _leading_text(node: ast.expr) -> str | None:
    """The statically-known leading text of a SQL expression, or None.

    Handles the two shapes utils/authn_manager.py actually uses: a plain string
    literal, and an f-string (implicit literal+f-string concatenation collapses
    to one JoinedStr whose first element is a Constant carrying the verb; the
    interpolations are only the paramstyle placeholder).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _sql_attribute_map(module: ast.Module) -> dict[str, str]:
    """Map every `self._sql_* = <literal|f-string>` assignment to its text."""
    out: dict[str, str] = {}
    for node in ast.walk(module):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Attribute)
            and target.attr.startswith("_sql_")
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            text = _leading_text(node.value)
            if text is not None:
                out[target.attr] = text
    return out


def _resolve_sql(call: ast.Call, sql_map: dict[str, str]) -> str | None:
    if not call.args:
        return None
    arg = call.args[0]
    direct = _leading_text(arg)
    if direct is not None:
        return direct
    if (
        isinstance(arg, ast.Attribute)
        and isinstance(arg.value, ast.Name)
        and arg.value.id == "self"
    ):
        return sql_map.get(arg.attr)
    return None


def find_unguarded_pairs(source: str) -> list[str]:
    """Names of functions with a SELECT-then-bare-INSERT pair in `source`.

    Bare means: the INSERT's execute call is not lexically inside a ``try``
    block that has at least one ``except`` handler (a ``try/finally`` with no
    handler does NOT count -- the repo has two of that exact shape), and the
    INSERT SQL does not fold the conflict via ON CONFLICT / OR IGNORE /
    OR REPLACE.
    """
    module = ast.parse(source)
    sql_map = _sql_attribute_map(module)
    offenders: list[str] = []

    for func in ast.walk(module):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        # Spans of try-bodies that actually have a handler. finalbody-only
        # trys are deliberately excluded: they re-raise, so the raw backend
        # error still escapes.
        handled_spans: list[tuple[int, int]] = []
        for node in ast.walk(func):
            if isinstance(node, ast.Try) and node.handlers and node.body:
                start = node.body[0].lineno
                end = max(stmt.end_lineno or stmt.lineno for stmt in node.body)
                handled_spans.append((start, end))

        executes: list[tuple[int, str]] = []
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("execute", "executemany")
            ):
                sql = _resolve_sql(node, sql_map)
                if sql is not None:
                    executes.append((node.lineno, sql.lstrip().upper()))
        executes.sort()

        first_select = next((ln for ln, sql in executes if sql.startswith("SELECT")), None)
        if first_select is None:
            continue
        for lineno, sql in executes:
            if not sql.startswith("INSERT") or lineno <= first_select:
                continue
            if any(marker in sql for marker in _CONFLICT_FOLDED):
                continue
            if any(start <= lineno <= end for start, end in handled_spans):
                continue
            offenders.append(func.name)
            break

    return offenders


# ---------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------


def test_authn_manager_has_no_unguarded_select_then_insert():
    """Every SELECT-then-INSERT pair in authn_manager handles the violation.

    PR #1216 wrapped create_user and create_device_token; the merge-order
    allowlist is gone. Zero unguarded pairs is the only accepted state.
    """
    offenders = set(find_unguarded_pairs(_TARGET.read_text(encoding="utf-8")))
    assert offenders == set(), (
        "unguarded SELECT-then-INSERT pair(s) in utils/authn_manager.py: "
        f"{sorted(offenders)} -- the pre-check SELECT means a uniqueness constraint "
        "exists, so wrap the INSERT in try/except using _is_unique_violation "
        "(see bootstrap_if_empty for the reference shape), or fold the "
        "conflict into the SQL"
    )


# ---------------------------------------------------------------------------
# Detector behavior, pinned on synthetic sources so the guard cannot rot
# ---------------------------------------------------------------------------

_BUG = '''
class M:
    def create_thing(self):
        row = self.conn.execute("SELECT * FROM t WHERE k = ?", (k,)).fetchone()
        if row is not None:
            raise Exists()
        self.conn.execute("INSERT INTO t (k) VALUES (?)", (k,))
        self.conn.commit()
'''

_GUARDED = '''
class M:
    def create_thing(self):
        row = self.conn.execute("SELECT * FROM t WHERE k = ?", (k,)).fetchone()
        if row is not None:
            raise Exists()
        try:
            self.conn.execute("INSERT INTO t (k) VALUES (?)", (k,))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
'''

_TRY_FINALLY_ONLY = '''
class M:
    def create_thing(self):
        self.conn.execute("SELECT 1 FROM t").fetchone()
        try:
            self.conn.execute("INSERT INTO t (k) VALUES (?)", (k,))
        finally:
            self.conn.close()
'''

_FOLDED = '''
class M:
    def bump(self):
        self.conn.execute("SELECT 1 FROM t").fetchone()
        self.conn.execute("INSERT INTO t (k) VALUES (?) ON CONFLICT (k) DO NOTHING", (k,))
'''

_NO_PRECHECK = '''
class M:
    def log_thing(self):
        self.conn.execute("INSERT INTO audit (line) VALUES (?)", (line,))
'''

_ATTRIBUTE_INDIRECTION = '''
class M:
    def _prepare(self):
        ph = self._ph
        self._sql_get = f"SELECT * FROM t WHERE k = {ph}"
        self._sql_put = (
            "INSERT INTO t "
            f"(k) VALUES ({ph})"
        )

    def create_thing(self):
        if self.conn.execute(self._sql_get, (k,)).fetchone():
            raise Exists()
        self.conn.execute(self._sql_put, (k,))
'''


def test_detector_flags_the_bug_shape():
    assert find_unguarded_pairs(_BUG) == ["create_thing"]


def test_detector_accepts_a_real_handler():
    assert find_unguarded_pairs(_GUARDED) == []


def test_detector_rejects_try_finally_without_except():
    """A handler-less try re-raises, so the raw backend error still escapes."""
    assert find_unguarded_pairs(_TRY_FINALLY_ONLY) == ["create_thing"]


def test_detector_accepts_conflict_folded_sql():
    assert find_unguarded_pairs(_FOLDED) == []


def test_detector_ignores_inserts_with_no_precheck():
    """No pre-check SELECT, no claim of a violable constraint -- out of scope."""
    assert find_unguarded_pairs(_NO_PRECHECK) == []


def test_detector_resolves_self_sql_attributes():
    """The pattern authn_manager actually uses: SQL held in self._sql_* names,
    the INSERT text split across an implicitly-concatenated f-string, and the
    pre-check SELECT inline in the `if` test rather than bound to a name."""
    assert find_unguarded_pairs(_ATTRIBUTE_INDIRECTION) == ["create_thing"]
