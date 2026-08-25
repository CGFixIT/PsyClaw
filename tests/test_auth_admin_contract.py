"""Contract tests for static/auth_admin.js — the shared Users admin panel.

Deliberately its own file rather than an append to test_terminal_contract.py:
auth_admin.js is a SHARED component (terminal.html and harness.html both load
it), not part of the terminal console's route contract, and keeping it separate
means a PR touching this panel does not collide at EOF with a PR touching the
terminal contract tests.

Source-reading, like its sibling: these assert the shape of the client code,
because the failure they guard against is invisible to any server-side test.
"""

from pathlib import Path

import gate

_AUTH_ADMIN_JS = Path(gate.__file__).resolve().parent / "static" / "auth_admin.js"


def _source() -> str:
    return _AUTH_ADMIN_JS.read_text(encoding="utf-8")


def _code_only() -> str:
    # Strip comment lines: the guarded helper's own comment quotes the old
    # `.then(reload)` pattern it replaced, which would otherwise trip the
    # bypass assertion below.
    return "\n".join(
        line for line in _source().splitlines() if not line.lstrip().startswith("//")
    )


def test_privileged_user_mutations_surface_failures():
    """A refused privileged mutation must never look like it succeeded.

    Role change / delete / password reset can all be refused -- 401/403 on an
    expired session, 403 on a CSRF mismatch, 429 under the rate limit, 503 with
    auth off. They were bare ``.then(reload)`` with no status check and no
    rejection handler, so a refusal repainted the row from the server's
    UNCHANGED state and the <select> silently snapped back to the old role.
    That is indistinguishable from "applied, then re-rendered", so an admin
    could believe they had demoted or deleted an account the server rejected.
    """
    js = _source()
    assert "function mutate(" in js, "the guarded mutation helper is gone"
    for label in ('mutate("role change"', 'mutate("delete"', 'mutate("password reset"'):
        assert label in js, f"missing guarded call: {label}"


def test_no_mutation_bypasses_the_guarded_helper():
    """Pattern-level guard, not per-call-site.

    A future mutation added with the old bare ``.then(reload)`` shape would
    reintroduce exactly the silent failure this replaced, so assert the shape
    is absent from the whole file rather than listing today's three callers.
    """
    assert ".then(reload)" not in _code_only(), "a mutation bypassed mutate()"


def test_mutate_checks_status_and_handles_an_unreachable_gateway():
    js = _source()
    body = js.split("function mutate(", 1)[1].split("\n    }", 1)[0]
    assert "resp.ok" in body, "mutate() must check the response status"
    assert ".catch(" in body, "mutate() must handle an unreachable gateway"


def test_the_initial_paint_reports_its_own_failure():
    """render() ends by calling reload(), which is fetch-backed. Called bare it
    left an empty panel plus an unhandled rejection when the gateway was down,
    with nothing on screen explaining why."""
    code = _code_only()
    assert "reload().catch(" in code, "the bootstrap reload() lost its rejection handler"
