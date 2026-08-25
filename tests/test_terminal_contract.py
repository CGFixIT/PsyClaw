"""Contract tests between the web console (static/terminal.html) and gate.py.

The console is a static file served by the gateway itself, so nothing ties its
fetch() targets to gate.py's registered routes: a renamed or removed endpoint
only fails at runtime in a browser, invisible to pytest and CI. These tests
extract every gateway path the console calls — direct `${API}/...` fetch
targets plus callOps('/ops/...') invocations — and assert each one exists on
gate.app with the HTTP method the console uses.

Runs entirely offline against the imported FastAPI app (no server, no LLM).
"""

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

import gate

_STATIC = Path(gate.__file__).resolve().parent / "static"
_TERMINAL_HTML = _STATIC / "terminal.html"
# The console's behaviour lives in terminal.js since the CSP fix: script-src 'self'
# blocks an inline <script>, so the code had to move out of the markup. These are
# contract tests about what the CONSOLE does, not about which file holds it, so
# read both and assert against the pair. Splitting the console further only means
# adding the new file here.
_TERMINAL_JS = _STATIC / "terminal.js"


def _console_source() -> str:
    return _TERMINAL_HTML.read_text(encoding="utf-8") + "\n" + _TERMINAL_JS.read_text(encoding="utf-8")

# Paths the console calls with POST (everything else it calls with GET).
_POST_PATHS = {
    "/query", "/soul/reload", "/soul/propose", "/soul/apply", "/soul/restore",
    "/ops/sync", "/ops/agentic", "/ops/fsconnect", "/ops/sqlconnect",
    "/auth/login", "/auth/logout", "/auth/bootstrap-password",
    # First-run: the console POSTs this when the operator clicks Build, and
    # GETs /index/status to follow it. Gated on the loopback socket peer plus
    # same-origin rather than the API key -- an unset CYCLAW_API_KEY fails
    # CLOSED, which would brick the very flow the route exists to unblock.
    "/index/build",
}


def _console_paths() -> set[str]:
    html = _console_source()
    # Direct fetch targets: `${API}/health`, `${API}/soul/reload`, ...
    paths = set(re.findall(r"\$\{API\}(/[A-Za-z0-9_/-]+)", html))
    # Indirect ops targets: callOps('/ops/sync', {...}) -> fetch(`${API}${path}`)
    paths |= set(re.findall(r"callOps\('(/[A-Za-z0-9_/-]+)'", html))
    return paths


def _gate_routes() -> dict[str, set[str]]:
    return {r.path: set(r.methods or ()) for r in gate.app.routes if isinstance(r, APIRoute)}


def test_console_path_extraction_is_not_empty():
    """Regex-rot guard: if terminal.html's fetch idiom changes and extraction
    breaks, this fails loudly instead of the per-path tests passing vacuously."""
    paths = _console_paths()
    assert len(paths) >= 10, f"extracted only {sorted(paths)} from terminal.html + terminal.js"
    assert "/health" in paths
    assert "/query" in paths
    assert any(p.startswith("/ops/") for p in paths)


def test_online_confirm_buttons_send_explicit_provider():
    # The two provider buttons used to be hardcoded, so this asserted the
    # literal handleConfirm(true, id, 'grok') / (…, 'claude') call sites. They
    # are now generated from the server's available_providers list, so assert
    # the contract instead of the old shape: both provider names are still
    # wired, and the click still passes an explicit provider rather than a bare
    # `true` (which the graph would default to grok).
    html = _console_source()
    assert "grok:" in html and "claude:" in html
    assert "handleConfirm(true, id, provider)" in html
    assert "body.online_provider = onlineProvider" in html
    assert "Escalating to ${providerLabel}" in html


def test_confirm_buttons_are_gated_on_server_declared_availability():
    """A 'Send to <provider>' button must never be rendered for a provider the
    user gate would decline to route to — pressing it silently produced an
    offline answer labelled as a cloud answer. The console reads the server's
    available_providers list, and defaults to the empty (offline-only) list so
    a response without the field fails closed."""
    html = _console_source()
    assert "data.available_providers" in html
    assert "availableProviders = []" in html, "the default must be offline-only, not both providers"
    assert "for (const provider of availableProviders)" in html


@pytest.mark.parametrize("path", sorted(_console_paths()))
def test_console_endpoint_exists_on_gateway(path):
    routes = _gate_routes()
    assert path in routes, (
        f"static/terminal.html calls {path!r} but gate.py registers no such "
        f"route — the console would get a 404 at runtime"
    )


@pytest.mark.parametrize("path", sorted(_console_paths()))
def test_console_endpoint_accepts_console_method(path):
    routes = _gate_routes()
    if path not in routes:
        pytest.skip("missing route reported by test_console_endpoint_exists_on_gateway")
    method = "POST" if path in _POST_PATHS else "GET"
    assert method in routes[path], (
        f"console calls {method} {path} but the route only allows "
        f"{sorted(routes[path])} — the console would get a 405 at runtime"
    )


def test_submit_query_refuses_to_start_while_one_is_in_flight():
    """submitQuery must bail on re-entry, not just decline a second button click.

    sendBtn.disabled is the in-flight signal, and a disabled button emits no
    click — but the Enter-key handler and the confirm-gate buttons call
    submitQuery() directly and never consult it. A second entry overwrites the
    single global activeQueryController, so the first query's abort handle is
    lost, Esc can no longer cancel the second one (its handler sees the nulled
    global), and the second one's deadline timer dereferences that null.

    The guard belongs at the top of submitQuery — before the input is cleared,
    so a refused send does not eat the operator's text — because the confirm
    buttons stay clickable while a later query is running. static/harness.html
    carries the same guard in onSend() for the same reason.
    """
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    body = js.split("async function submitQuery(", 1)
    assert len(body) == 2, "submitQuery is no longer declared as expected; update this test"
    after = body[1]
    guard = "if (sendBtn.disabled) return;"
    assert guard in after, f"submitQuery does not re-entry-guard on {guard!r}"
    # It has to run before the input is cleared, or a refused send still wipes
    # what the operator typed.
    assert after.index(guard) < after.index("input.value = ''"), (
        "the in-flight guard must precede the input reset inside submitQuery"
    )


def test_check_health_treats_a_non_ok_response_as_unreachable():
    """checkHealth must guard on resp.ok like every other fetch in the file.

    It alone parsed the body unconditionally. A gateway answering 4xx/5xx with a
    JSON error body (FastAPI's {"detail": ...}) then flowed into the SUCCESS
    branch: data.status was undefined so the footer rendered "gateway
    undefined", the dot left its offline state, a bogus graph_timeout_sec could
    be adopted as the query deadline, and healthBackoffMs was reset to the base
    interval -- so the console kept polling a failing gateway every 15s while
    telling the operator it was fine. The backoff reset is the load-bearing part:
    it must stay unreachable for a non-2xx response.
    """
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    body = js.split("async function checkHealth(", 1)
    assert len(body) == 2, "checkHealth is no longer declared as expected; update this test"
    after = body[1].split("\n}", 1)[0]
    assert "if (!resp.ok)" in after, "checkHealth does not guard on resp.ok"
    # A non-JSON error body (e.g. the TrustedHost 400's plain text) must not
    # throw before the status is inspected.
    assert ".catch(() => ({}))" in after, "checkHealth does not tolerate a non-JSON body"
    # The ok-guard has to precede every field read and, above all, the backoff reset.
    assert after.index("if (!resp.ok)") < after.index("healthBackoffMs = HEALTH_BASE_INTERVAL"), (
        "the resp.ok guard must precede the backoff reset, or a failing gateway "
        "keeps being polled at the base interval"
    )
    # The status render moved into paintHealthStatus() so build-state changes
    # can repaint the chip immediately instead of waiting up to 15s for the
    # next poll. Same guarantee as before, tracked to the new symbol: the
    # ok-guard must still run before anything is rendered from the response.
    assert after.index("if (!resp.ok)") < after.index("paintHealthStatus()"), (
        "the resp.ok guard must precede the status render"
    )


def test_enter_handler_ignores_ime_composition():
    """Enter also commits an IME candidate. Sending on that keystroke submits a
    half-composed query and swallows the key the operator meant for the IME."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    handler = js.split("input.addEventListener('keydown'", 1)
    assert len(handler) == 2, "the keydown handler moved; update this test"
    after = handler[1]
    assert "if (e.isComposing) return;" in after
    assert after.index("if (e.isComposing) return;") < after.index("e.key === 'Enter'"), (
        "the composition check must precede the Enter branch"
    )


def test_login_form_controls_exist():
    html = _console_source()
    for marker in (
        'id="authUser"',
        'id="authPass"',
        'id="authLoginBtn"',
        'id="authLogoutBtn"',
        'id="authStatus"',
        "${API}/auth/login",
        "${API}/auth/logout",
        "${API}/auth/whoami",
    ):
        assert marker in html, f"missing login-form marker {marker!r}"


def test_users_and_audit_markup_exist():
    html = _console_source()
    for marker in (
        'id="usersToggleBtn"',
        'id="usersPanel"',
        'id="usersPanelBody"',
        'id="auditToggleBtn"',
        'id="auditPanel"',
        'id="auditSummary"',
        "query === '/users'",
        "/static/auth_admin.js",
    ):
        assert marker in html, f"missing {marker!r}"


def test_query_does_not_send_api_key_as_bearer():
    """Once auth.enabled is on, Authorization on /query is a device token.
    The typed CYCLAW_API_KEY must stay on /soul/* and /ops/* only."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "function queryHeaders()" in js
    fetch_query = js.split("const resp = await fetch(`${API}/query`", 1)
    assert len(fetch_query) == 2, "/query fetch site moved; update this test"
    header_block = fetch_query[1].split("});", 1)[0]
    assert "queryHeaders()" in header_block
    assert "authHeaders()" not in header_block


def test_hidden_attribute_is_not_overridden_by_display_rules():
    """`hidden` must actually hide, even on elements with an explicit display.

    The attribute is only a UA-stylesheet `display: none`, so ANY explicit
    display rule silently outranks it. `.toolbar-btn` sets
    `display: inline-flex`, which meant #usersToggleBtn and #auditToggleBtn --
    both shipped carrying `hidden`, both driven by applyRoleChrome()'s role
    gate -- rendered normally regardless of role, and clicking one produced an
    error entry instead of the button simply not being there. Server-side auth
    was never affected; this is the visual gate only.

    Found by rendering the console in a real browser and reading computed
    style, which no test here does -- these read source. This pins the fix so
    it cannot be dropped by a future CSS tidy-up.
    """
    html = _TERMINAL_HTML.read_text(encoding="utf-8")
    assert re.search(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", html), (
        "the global [hidden] display:none !important rule is gone -- role-gated "
        "toolbar buttons will render for every visitor again"
    )


def test_advanced_mode_hides_every_operator_console():
    """The five subsystem consoles must sit behind the advanced switch.

    They shim out-of-band subsystems behind an API key: operator surfaces, not
    user surfaces. Before this wrapper existed they were unconditionally
    visible, so the first thing a non-engineer saw was five tools to ignore.

    Deliberately NOT folded into applyRoleChrome(): that gate keys off the auth
    role from /auth/whoami, and auth.enabled ships false, so in the shipped
    posture the route 503s, authRole stays null, and anything gated on it would
    be permanently invisible rather than optional.
    """
    html = _TERMINAL_HTML.read_text(encoding="utf-8")
    js = _TERMINAL_JS.read_text(encoding="utf-8")

    wrapper = html.split('<span class="advanced-tools" id="advancedTools" hidden>', 1)
    assert len(wrapper) == 2, "the #advancedTools wrapper is gone"
    body = wrapper[1].split("</span>", 1)[0]
    for btn in (
        "soulToggleBtn", "syncToggleBtn", "agenticToggleBtn",
        "fsToggleBtn", "sqlToggleBtn", "usersToggleBtn", "auditToggleBtn",
    ):
        assert f'id="{btn}"' in body, f"{btn} escaped the advanced wrapper"

    # Ships closed: the wrapper carries `hidden` and the button says collapsed.
    assert 'id="advancedToggleBtn"' in html
    assert 'aria-expanded="false"' in html, "advanced mode must default to off"
    # display:contents keeps the buttons in .soul-toolbar's flex layout; any
    # other value re-flows the whole toolbar.
    assert re.search(r"\.advanced-tools\s*\{[^}]*display:\s*contents", html)
    # localStorage access is wrapped: private windows throw on READ, not just write.
    assert "function readAdvancedPref()" in js
    pref = js.split("function readAdvancedPref()", 1)[1].split("\n}", 1)[0]
    assert "try {" in pref and "catch" in pref, "localStorage read must be guarded"


def test_no_best_effort_phrasing_survives_client_side():
    """'Offline Best Effort' told a user nothing about which model would
    answer. The three literal user-facing strings that said so must be gone --
    the server-authored confirm_message (gate.py's _confirm_choices) is what
    now names the real model, and it's what the console renders above these
    buttons. NOT a blanket substring ban: "offline-best-effort" is the real
    answer_model enum value from graph.py and legitimately appears in JS
    comments/logic discussing it -- only the removed user-facing copy is
    asserted gone here."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "Offline Best Effort" not in js
    assert "stay offline with best-effort local" not in js
    assert "Staying offline (best-effort local)" not in js


def test_stay_offline_button_reads_correctly_in_both_layouts():
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "'No — Stay Offline' : 'Stay Offline'" in js


def test_error_copy_table_covers_the_query_reachable_codes():
    """Every code /query can actually emit -- the ~10 HTTPException codes plus
    the 4 that arrive as a 200 with an `error` field carrying graph.py's
    "{code}: {message}" stamp -- must have a plain-language entry, so an error
    never regresses to a bare code with no sentence."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "const ERROR_COPY = {" in js
    table = js.split("const ERROR_COPY = {", 1)[1].split("\n};", 1)[0]
    for code in (
        "INDEX_NOT_FOUND", "PROMPT_INJECTION_BLOCKED", "GRAPH_TIMEOUT",
        "GRAPH_ERROR", "RATE_LIMIT", "VALIDATION_ERROR", "PAYLOAD_TOO_LARGE",
        "AUTH_ROLE_DENIED", "CROSS_SITE_BLOCKED", "AUTH_REQUIRED",
        "EMBEDDING_ERROR", "LLM_SERVICE_ERROR", "GROK_SERVICE_ERROR",
        "CLAUDE_SERVICE_ERROR",
    ):
        assert f"{code}:" in table, f"{code} has no ERROR_COPY entry"


def test_query_error_rendering_keeps_the_code_visible():
    """The friendly sentence must not silently swallow the code -- an operator
    debugging over someone's shoulder still needs it, just in small print
    beside the plain-language text rather than as the whole message."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "function describeQueryError(" in js
    assert "function extractErrorCode(" in js
    # Both /query render sites must surface the code via the meta row, not
    # just interpolate the raw server string into the entry text.
    submit_fn = js.split("async function submitQuery(", 1)[1]
    assert "describeQueryError(err)" in submit_fn
    assert "describeQueryError(data.error)" in submit_fn
    assert "{ k: 'code', v: code }" in submit_fn
