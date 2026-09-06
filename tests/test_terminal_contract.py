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

    Issue #1298 N1: a confirm click that hits this guard used to return before
    pendingConfirmById.delete, so the stored query stuck. The disabled path
    must still drop that map entry.
    """
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    body = js.split("async function submitQuery(", 1)
    assert len(body) == 2, "submitQuery is no longer declared as expected; update this test"
    after = body[1]
    guard = "if (sendBtn.disabled)"
    assert guard in after, f"submitQuery does not re-entry-guard on {guard!r}"
    # It has to run before the input is cleared, or a refused send still wipes
    # what the operator typed.
    assert after.index(guard) < after.index("input.value = ''"), (
        "the in-flight guard must precede the input reset inside submitQuery"
    )
    disabled_block = after.split(guard, 1)[1].split("const query =", 1)[0]
    assert "pendingConfirmById.delete(confirmEntryId)" in disabled_block, (
        "in-flight confirm must drop the stored query before returning"
    )
    assert "return;" in disabled_block


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


def test_health_polling_is_visibility_aware():
    """Background tabs should not keep polling /health; harness.html already
    does this for /api/status. The terminal must pause scheduling when hidden
    and refresh immediately when visible again."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "let healthVisible = !document.hidden" in js, (
        "a terminal loaded in an already-hidden tab must not start a polling loop"
    )
    assert "document.addEventListener('visibilitychange'" in js
    body = js.split("function scheduleHealthCheck(", 1)
    assert len(body) == 2, "scheduleHealthCheck moved; update this test"
    after = body[1].split("}", 1)[0]
    assert "if (healthVisible)" in after, "scheduleHealthCheck must guard on healthVisible"


def test_whoami_success_stores_rotated_csrf():
    """A reload keeps cyclaw_session but drops the JS csrfToken. refreshAuthUi
    must assign whoami's csrf_token or logout/Users writes 403 while logged in."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "csrfToken = data.csrf_token || null" in js
    assert "fetchWithTimeout(`${API}/auth/whoami`, { cache: 'no-store' }, 5000)" in js


def test_logout_honors_non_ok_http_status():
    """A failed logout must not null csrfToken then whoami, but 401/403 must refresh.

    Issue #1298 N4: the terminal always nulled csrfToken after the fetch,
    even on 401/403. whoami then painted logged-in with a dead CSRF.

    Codex P2 on PR #1313: a 403 is CSRF mismatch (token already rotated in
    another tab). Returning while keeping the rejected token skips the
    whoami rotate-and-return path, so retries and Users writes stay 403
    until a full reload. Refresh auth/CSRF on 401/403 without assigning
    null first; restore the rejected status line after whoami overwrites it.
    """
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    body = js.split("async function logout(", 1)
    assert len(body) == 2, "logout is no longer declared as expected; update this test"
    after = body[1].split("async function fetchWithTimeout(", 1)[0]
    assert "if (!response.ok)" in after, "logout must refuse to proceed on a non-2xx"
    assert after.index("if (!response.ok)") < after.index("csrfToken = null"), (
        "logout must keep csrfToken when the server rejected the request"
    )
    rejected_block = after.split("if (!response.ok)", 1)[1].split("} catch", 1)[0]
    assign_null = [ln for ln in rejected_block.splitlines() if ln.strip().startswith("csrfToken = null")]
    assert not assign_null, (
        "rejected logout must not null csrfToken before (or instead of) whoami refresh"
    )
    assert "response.status === 401" in rejected_block
    assert "response.status === 403" in rejected_block
    assert "refreshAuthUi()" in rejected_block, (
        "401/403 logout must refresh auth/CSRF from whoami rather than keep the rejected token"
    )
    assert rejected_block.index("refreshAuthUi()") < rejected_block.index("authStatus.textContent = rejected"), (
        "restore the rejected status line after whoami overwrites it with username · role"
    )
    assert "return;" in rejected_block


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
        "slash === '/users'",
        "/static/auth_admin.js",
    ):
        assert marker in html, f"missing {marker!r}"


def test_users_panel_fetchfn_is_bounded_by_a_timeout() -> None:
    """Users-panel fetchFn must not use a bare fetch (harness already wraps it)."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "fetchWithTimeout(API + path, init || {}, 15000)" in js
    assert "fetchFn: function (path, init) { return fetch(API + path, init); }" not in js


def test_audit_slash_command_is_intercepted_client_side():
    """/audit is documented in the help text and has a dedicated panel. Without
    interception it is POSTed to /query and returns a raw JSON error."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    body = js.split("async function submitQuery(", 1)
    assert len(body) == 2, "submitQuery moved; update this test"
    after = body[1].split("const loadingId = addLoadingEntry()", 1)[0]
    assert "toLocaleLowerCase('en-US')" in after, "slash intercept must use en-US, not locale toLowerCase"
    assert "slash === '/audit'" in after, "/audit is not intercepted in submitQuery"
    assert "openAuditPanel()" in after, "/audit interception does not open the audit panel"


def test_help_slash_command_is_intercepted_client_side():
    """/help must stay in the console, not POST to /query as a retrieval query."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    body = js.split("async function submitQuery(", 1)
    assert len(body) == 2, "submitQuery moved; update this test"
    after = body[1].split("const loadingId = addLoadingEntry()", 1)[0]
    assert "slash === '/help'" in after, "/help is not intercepted in submitQuery"


def test_open_audit_panel_uses_api_key_route_when_typed():
    """Typed CYCLAW_API_KEY must hit GET /audit/summary, not the session route."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    body = js.split("async function openAuditPanel(", 1)
    assert len(body) == 2, "openAuditPanel moved; update this test"
    after = body[1].split("async function submitQuery(", 1)[0]
    assert "`${API}/audit/summary`" in after, "openAuditPanel does not fetch /audit/summary"
    assert "authHeaders()" in after, "openAuditPanel does not send authHeaders() with the typed key"


def test_describe_api_key_error_maps_unset_and_mismatch():
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "function describeApiKeyError(" in js
    body = js.split("function describeApiKeyError(", 1)[1].split("\nfunction ", 1)[0]
    assert "CYCLAW_API_KEY not set" in body
    assert "Invalid or missing API key" in body


def test_api_key_placeholder_names_soul_and_ops():
    html = _TERMINAL_HTML.read_text(encoding="utf-8")
    assert 'placeholder="CYCLAW_API_KEY (Soul / ops)"' in html


def test_confirm_prompt_query_is_stored_per_entry():
    """A single global pendingConfirmQuery string was overwritten by each new
    low-confidence query, so approving a stale prompt submitted the wrong text.
    The fix stores query text keyed by confirm-entry id."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "const pendingConfirmById = new Map()" in js
    assert "pendingConfirmById.set(entryId, query)" in js
    assert "pendingConfirmById.get(confirmEntryId)" in js
    assert "pendingConfirmById.delete(confirmEntryId)" in js
    assert "handleConfirm(true, id, provider)" in js
    # handleConfirm must validate the stored query before escalating.
    assert "pendingConfirmById.get(entryId)" in js
    assert "Confirmation expired" in js


def test_propose_soul_refuses_empty_reason():
    """Empty soul reason must not be replaced with a canned I5 string."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    fn = js.split("async function proposeSoulEvolution()", 1)
    assert len(fn) == 2, "proposeSoulEvolution missing"
    body = fn[1].split("async function applySoulEvolution()", 1)[0]
    assert "|| 'user-requested'" not in body
    assert "A reason is required." in body
    assert "/soul/propose" in body
    before_fetch, _after = body.split("`${API}/soul/propose`", 1)
    assert "if (!reason)" in before_fetch
    reason_guard = before_fetch.split("if (!reason)", 1)[1].split("return;", 1)[0]
    assert "pendingSoulProposal = null" in reason_guard
    assert "proposalBox.style.display = 'none'" in reason_guard
    assert "return;" in before_fetch


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


def test_index_build_post_is_bounded_like_every_other_fetch():
    """A bare fetch here strands the first-run panel with no way out.

    startIndexBuild sets state='running' BEFORE the request, and
    renderFirstRun's running branch draws no button -- so a request that never
    settles leaves "Building your library" on screen permanently, with no Try
    again affordance and no error. Every other call in this file is bounded;
    this one was the exception.
    """
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "fetchWithTimeout(`${API}/index/build`" in js
    assert "await fetch(`${API}/index/build`" not in js, "the build POST lost its timeout"


def test_index_status_poll_has_a_failure_ceiling():
    """The retry-on-drop is correct; retrying forever is not.

    Without a ceiling a gateway that dies mid-build leaves the tab polling
    /index/status every 1.5s indefinitely while the panel still reads
    "Building your library" -- the operator is never told contact was lost.
    """
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "INDEX_POLL_MAX_MISSES" in js, "the poll retry lost its ceiling"
    # The streak must reset on a reachable server and on a fresh build, or a
    # long-lived tab would eventually trip the ceiling on transient blips.
    assert js.count("indexBuild.misses = 0") >= 2
    assert "indexBuild.misses += 1" in js


def test_index_status_poll_treats_a_non_ok_response_as_a_dropped_poll():
    """pollIndexStatus must guard on resp.ok before it reads the build state.

    It parsed the body unconditionally. A gateway answering a JSON-bodied
    non-2xx -- a 429 from the front-running rate limiter (the console spends
    40 of the 60 req/min budget polling this route every INDEX_POLL_MS), a 503,
    FastAPI's {"detail": ...} -- parsed fine, so s.state came back undefined and
    `s.state === 'done' ? 'idle' : 'error'` read that as a FAILED build. The
    panel announced the build had stopped while it kept running server-side.

    checkHealth (same file) already documents and fixes this exact class, and
    its comment claims "every other fetch in this file guards on resp.ok" --
    this poller was the one that did not. Throwing routes a JSON-bodied failure
    down the same miss-counter path a network error already took: retried, and
    bounded by INDEX_POLL_MAX_MISSES.
    """
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    body = js.split("function pollIndexStatus(", 1)
    assert len(body) == 2, "pollIndexStatus is no longer declared as expected; update this test"
    after = body[1].split("\n}", 1)[0]
    assert "if (!resp.ok)" in after, "pollIndexStatus does not guard on resp.ok"
    # Ordering is the load-bearing half. A guard placed after either of these
    # does nothing: the body would already be parsed, and the miss streak the
    # catch block depends on would already have been cleared by a failed poll.
    assert after.index("if (!resp.ok)") < after.index("await resp.json()"), (
        "the resp.ok guard must precede the body parse"
    )
    assert after.index("if (!resp.ok)") < after.index("indexBuild.misses = 0"), (
        "the resp.ok guard must precede the miss-streak reset, or a failing "
        "gateway keeps clearing the streak that surfaces lost contact"
    )


def test_panel_loaders_return_success_and_retry_on_failure():
    """Subsystem panels must latch 'loaded' only on success and retry later.

    Setting the flag before the await makes a 401/network failure latch the
    panel as loaded forever; reopening never retries. Each runX helper must
    return true/false so the toggle can set *Loaded accordingly.
    """
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    for runner in ("runSync", "runAgentic", "runFs", "runSql"):
        body = js.split(f"async function {runner}(", 1)
        assert len(body) == 2, f"{runner} moved; update this test"
        block = body[1].split("\n}", 1)[0]
        assert "return true;" in block, f"{runner} does not return true on success"
        assert "return false;" in block, f"{runner} does not return false on error"

    for toggle, runner in (
        ("toggleSyncPanel", "runSync"),
        ("toggleAgenticPanel", "runAgentic"),
        ("toggleFsPanel", "runFs"),
        ("toggleSqlPanel", "runSql"),
    ):
        body = js.split(f"async function {toggle}(", 1)
        assert len(body) == 2, f"{toggle} moved; update this test"
        block = body[1].split("\n}", 1)[0]
        assert f"await {runner}('status')" in block, f"{toggle} does not call {runner}('status')"
        assert "apiKeyInput.value.trim()" in block, f"{toggle} does not check for an API key"


def test_audit_panel_fetch_is_wrapped():
    """openAuditPanel is an async click handler; an unwrapped reject is an
    unhandled rejection AND leaves the placeholder text sitting there looking
    like a panel that loaded. Every sibling panel wraps its fetch."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    body = js.split("async function openAuditPanel()", 1)[1].split("\n}", 1)[0]
    assert "try {" in body and "catch" in body, "openAuditPanel's fetch is unguarded"


def test_ops_calls_cover_server_side_budgets():
    """callOps deadlines must sit above the server-side subprocess budgets
    (utils/ops_runner.py: 120s _TIMEOUT_SEC; /ops/sync action=sync up to
    _sync_timeout_sec()*2+60 = 7260s with post_sync_check). The old flat
    60000 aborted the tab while the CLI kept running and threw away the
    exit-code envelope."""
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "const OPS_CLI_TIMEOUT_MS = 130000;" in js
    assert "callOps(path, body, timeoutMs = OPS_CLI_TIMEOUT_MS)" in js
    assert "action === 'sync' ? opsSyncDeadlineMs : OPS_CLI_TIMEOUT_MS" in js
    # sync.sync_timeout_sec has no upper bound, so the sync deadline must come
    # from the server (/health) rather than a constant that cannot cover every
    # valid configuration; the literal below is only the pre-/health fallback.
    assert "let opsSyncDeadlineMs = 7320000;" in js
    assert "opsSyncDeadlineMs = (data.ops_sync_timeout_sec + 60) * 1000;" in js
    assert "}, 60000);" not in js, "flat 60s ops ceiling regressed"


def test_health_details_are_keyboard_reachable_and_json_uses_textcontent():
    """Health guidance used to live only on statusText.title (hover). The
    disclosure must be a native <details> next to the chip so keyboard and
    screen-reader users can open the latest /health payload without hover,
    and that payload must be assigned with textContent -- never innerHTML.
    """
    html = _TERMINAL_HTML.read_text(encoding="utf-8")
    js = _TERMINAL_JS.read_text(encoding="utf-8")

    assert 'id="healthDetails"' in html
    details = html.split('id="healthDetails"', 1)[1]
    assert "<summary>" in html.split('class="health-details"', 1)[1]
    assert 'id="healthJson"' in details
    assert "<pre" in html.split('id="healthJson"', 1)[0][-80:]

    assert "function paintHealthDisclosure(" in js
    body = js.split("function paintHealthDisclosure(", 1)[1].split("\n}", 1)[0]
    assert "jsonEl.textContent = jsonText" in body
    assert "jsonEl.innerHTML" not in body
    assert ".innerHTML =" not in body
    assert "JSON.stringify(lastHealth, null, 2)" in js
    # Unreachable /health must not leave stale JSON labelled as current.
    assert "Last successful health response" in js
    assert "paintHealthStatus({ unreachable: true })" in js


def test_ollama_down_exposes_inline_how_to_start():
    """When Ollama is down the chip stays terse/amber; startup help is a
    one-click control with local `ollama serve` copy, not an external docs
    link and not hover-only title text.
    """
    html = _TERMINAL_HTML.read_text(encoding="utf-8")
    js = _TERMINAL_JS.read_text(encoding="utf-8")

    assert 'id="ollamaHelp"' in html
    help_block = html.split('id="ollamaHelp"', 1)[1].split("</details>", 1)[0]
    assert "How to start" in help_block
    assert "ollama serve" in help_block
    assert "http" not in help_block.lower()

    describe = js.split("function describeHealth(", 1)
    assert len(describe) == 2, "describeHealth moved; update this test"
    desc_body = describe[1].split("function paintHealthDisclosure(", 1)[0]
    assert "down.includes('ollama')" in desc_body
    assert "Local AI engine isn't running" in desc_body
    assert "tone: 'warn'" in desc_body
    assert "tone: 'ok'" in desc_body
    assert "ollamaHelp.hidden = !ollamaDown" in js
    # The chip ranks "No library yet" above the Ollama-down sentence, but the
    # How to start control must still appear whenever the service is down.
    ranked = desc_body.split("if (d.index_ready === false)", 1)
    assert len(ranked) == 2, "index_ready ranking moved; update this test"
    no_lib = ranked[1].split("if (ollamaDown)", 1)[0]
    assert "ollamaDown," in no_lib or "ollamaDown:" in no_lib
    assert "ollamaDown: false" not in no_lib


def test_index_build_renders_elapsed_when_present():
    """GET /index/status already returns elapsed_sec; the panel used to ignore
    it. Store only finite non-negative values, reset on a new build, format
    compactly, and show elapsed even when chunk totals are missing. Do not
    coerce missing values with `elapsed_sec || 0` (that prints a fake 0).
    """
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "function formatElapsed(" in js
    fmt = js.split("function formatElapsed(", 1)[1].split("\n}", 1)[0]
    assert "if (!Number.isFinite(seconds) || seconds < 0) return ''" in fmt
    assert "${whole}s" in fmt
    assert "padStart(2, '0')}s" in fmt

    assert "indexBuild.elapsed = null" in js
    assert "Number.isFinite(s.elapsed_sec) && s.elapsed_sec >= 0" in js
    assert "s.elapsed_sec || 0" not in js
    assert "Elapsed ${elapsed}" in js

    start = js.split("async function startIndexBuild(", 1)
    assert len(start) == 2, "startIndexBuild moved; update this test"
    start_body = start[1].split("function pollIndexStatus(", 1)[0]
    assert "indexBuild.elapsed = null" in start_body

    poll = js.split("function pollIndexStatus(", 1)
    assert len(poll) == 2, "pollIndexStatus moved; update this test"
    poll_body = poll[1].split("\n  }, INDEX_POLL_MS);", 1)[0]
    assert poll_body.index("if (!resp.ok)") < poll_body.index("indexBuild.elapsed"), (
        "elapsed must be stored only after the resp.ok guard"
    )


def test_answer_route_copy_uses_stable_model_used_roles():
    """Default answer meta translates the stable model_used role plus the
    additive llm_model tag. Blocked/unavailable/unknown roles must not claim
    a model answered. Raw fields stay available in advanced mode.
    """
    js = _TERMINAL_JS.read_text(encoding="utf-8")
    assert "function describeAnswerRoute(" in js
    body = js.split("function describeAnswerRoute(", 1)[1].split("\n}", 1)[0]
    assert "case 'local':" in body
    assert "answered from your documents · ${llmModel || 'the local model'}" in body
    assert (
        "not in your documents · answered locally by ${llmModel || 'the local model'} "
        "from its own knowledge"
    ) in body
    assert "case 'offline-best-effort':" in body
    assert "case 'grok':" in body
    assert "case 'claude':" in body
    assert "not in your documents · sent to ${llmModel || 'Grok'}" in body
    assert "not in your documents · sent to ${llmModel || 'Claude'}" in body
    assert "default:" in body
    assert "return null;" in body
    for blocked in (
        "guardrail-blocked", "hook-denied", "external-unavailable",
    ):
        assert f"case '{blocked}'" not in body

    submit = js.split("async function submitQuery(", 1)[1]
    assert "describeAnswerRoute(data.model_used, data.llm_model)" in submit
    assert "if (advancedMode)" in submit
    # The default row uses the friendly sentence; it must not still be the
    # raw role/mode/hits list for every visitor.
    default_meta = submit.split("const route = describeAnswerRoute", 1)[1]
    assert "{ k: 'route', v: route }" in default_meta
    assert "if (route)" in default_meta
