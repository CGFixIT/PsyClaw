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


def test_a_refused_mutation_survives_the_follow_up_reload():
    """A CSRF-rejected role change / delete / password reset (or a
    validation-rejected create) must keep its error on screen.

    mutate() and the create handler both call reload() right after a refused
    mutation, to repaint the (unchanged) list from the server. reload()'s own
    success path used to call onStatus() unconditionally, clearing the error
    the very same handler had just shown one line above -- so the failure
    flashed for a moment and then the panel went quiet, indistinguishable
    from the mutation having gone through. reload() must accept a
    preserveStatus flag, and every caller that just recorded a failure must
    pass it through instead of reloading bare.
    """
    js = _source()
    assert "async function reload(preserveStatus)" in js, "reload() lost its preserveStatus parameter"
    assert "if (!preserveStatus) onStatus();" in js, (
        "reload()'s successful path must skip clearing status when preserveStatus is set"
    )

    mutate_body = js.split("function mutate(", 1)[1].split("\n    }", 1)[0]
    assert "return reload(failed);" in mutate_body, "mutate() must forward its own failure into reload()"

    create_body = js.split('createBtn.addEventListener("click"', 1)[1].split("\n    });", 1)[0]
    assert "reload(failed);" in create_body, "the create-user handler must forward its own failure into reload()"


def test_mutate_clears_status_before_new_attempt():
    """A stale error must not persist across a fresh attempt, or the operator
    cannot tell whether the new click failed or the old one did."""
    js = _source()
    body = js.split("function mutate(", 1)[1].split("\n    }", 1)[0]
    assert "onStatus()" in body, "mutate() must clear status at the start of a new attempt"


def test_reload_surfaces_list_failures_and_clears_on_success():
    """reload() must use onStatus for non-ok responses (not just listBox text)
    and clear the status after a successful render."""
    js = _source()
    body = js.split("async function reload(", 1)[1].split("\n    }", 1)[0]
    assert 'onStatus("cannot list users' in body, "reload() must surface list failures via onStatus"
    assert body.count("onStatus()") >= 1, "reload() must clear status on success"


def test_embedders_pass_an_onStatus_callback():
    """Both terminal.html and harness.html instantiate the shared panel with a
    real status callback; without one the default no-op swallows errors."""
    static = Path(gate.__file__).resolve().parent / "static"
    for filename in ("terminal.js", "harness.html"):
        text = (static / filename).read_text(encoding="utf-8")
        assert "onStatus:" in text, f"{filename} does not pass onStatus to CyClawAuthAdmin.render"
        assert "usersPanelStatus" in text, f"{filename} is missing the usersPanelStatus node"
