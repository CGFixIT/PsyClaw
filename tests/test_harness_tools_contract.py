"""Two-way contract between harness/tools_view.py's catalog and the live app.

tests/test_harness.py:223 (test_tools_endpoint_lists_only_live_routes) already pins
one direction: every _HARNESS_SURFACES path is a registered path (data["wired"] ==
data["total"]). What nothing pins is the reverse -- every registered /api/* route is
either cataloged or a named, commented exemption -- nor that the catalog's declared
METHOD matches the route (list_wired_tools's "wired" check is path-only; a row
claiming GET on a POST-only route renders wired regardless), nor that a route
guarded by the harness's API-key dependency chain is actually covered by
tests/test_harness_auth.py's GUARDED list. All three drift classes were real when
this file was added: POST /api/web/search and GET/POST /api/keys existed on the live
app with no catalog row, and /api/keys additionally had no GUARDED entry at all.

Runs entirely offline against the app factory (no server, no LLM, no Ollama), same
technique as tests/test_harness_console_contract.py.
"""

from __future__ import annotations

import re

from fastapi.routing import APIRoute

from harness.config import HarnessConfig
from harness.server import create_app
from harness.tools_view import _HARNESS_SURFACES

_PARAM = "{}"

# Sub-actions of an already-cataloged slash family, or a separate session+CSRF
# auth domain the /users command drives -- exempt rather than cataloged, per the
# WAKU_HARNESS_TOOLS_PLAN.md S1 policy: one row per user-facing slash family,
# exemptions for sub-actions and for /api/auth/*. Each entry names the family it
# belongs to so a reviewer can see why it isn't a phantom instead of a decision.
_CATALOG_EXEMPT: frozenset[tuple[str, str]] = frozenset({
    # /web sub-actions -- "web" (GET /api/web) and "web-fetch" are cataloged.
    ("POST", "/api/web"),
    ("POST", "/api/web/allow"),
    ("POST", "/api/web/deny"),
    ("POST", "/api/web/inject"),
    ("POST", "/api/web/forget"),
    # /memory sub-actions -- "memory" (GET) and "memory-add" are cataloged.
    ("POST", "/api/memory"),
    ("POST", "/api/memory/forget"),
    ("POST", "/api/memory/clear"),
    # /session sub-actions -- "session" (GET /api/sessions) and "goal" are
    # cataloged; new/read-one/rename are not separately slash-invoked.
    ("POST", "/api/sessions"),
    ("GET", "/api/sessions/{}"),
    ("POST", "/api/sessions/{}/rename"),
    # /soul sub-action -- "soul" (GET) is cataloged; the toggle is not.
    ("POST", "/api/soul"),
    # /api write sub-action of the "keys" family -- the GET status read is
    # cataloged; the credential write is deliberately not surfaced as its own
    # slash target.
    ("POST", "/api/keys"),
    # The whole /api/auth/* block: its own session+CSRF domain (auth_open /
    # auth_sess dependency lists, not `guarded`), driven by the /users command
    # (static/harness.html:1263) and the first-password bootstrap panel rather
    # than by a 1:1 /tools row.
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/whoami"),
    ("GET", "/api/auth/setup-status"),
    ("POST", "/api/auth/bootstrap-password"),
    ("GET", "/api/auth/users"),
    ("POST", "/api/auth/users"),
    ("POST", "/api/auth/users/{}/password"),
    ("POST", "/api/auth/users/{}/role"),
    ("DELETE", "/api/auth/users/{}"),
})

# The API-key dependency chain harness/server.py attaches as `dependencies=guarded`.
# It is a closure-local list built inside create_app (harness/server.py:660), so it
# cannot be imported; _require_api_key_or_optional is likewise a closure local to
# create_app (:620) with no module-level name. Matched by NAME in the route's
# dependant tree instead, mirroring gate.py's own _AUTH_DEPENDENCY_NAME /
# _dependant_call_names technique (gate.py:1150-1193) -- reimplemented locally
# rather than imported, since tests/test_harness_isolation.py enforces that
# harness and gate never import each other (I6-style isolation applies to
# production code; this test file is exempt from that rule but honors its
# intent rather than reaching for the shortcut).
_KEY_DEPENDENCY_NAME = "_require_api_key_or_optional"

# /api/auth/* authenticates by session cookie + CSRF (auth_open / auth_sess), not
# the API-key `guarded` chain -- it is a different control, not a missing one.
_KEY_EXEMPT: frozenset[tuple[str, str]] = frozenset({
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/whoami"),
    ("GET", "/api/auth/setup-status"),
    ("POST", "/api/auth/bootstrap-password"),
    ("GET", "/api/auth/users"),
    ("POST", "/api/auth/users"),
    ("POST", "/api/auth/users/{}/password"),
    ("POST", "/api/auth/users/{}/role"),
    ("DELETE", "/api/auth/users/{}"),
})


def _dependant_call_names(root: object) -> set[str]:
    """Every callable name in a FastAPI dependant tree, including nested ones.

    Iterative rather than recursive, same reason as gate.py:1178 -- a dependency
    graph is operator-shaped data, and a stack cannot blow the interpreter's
    recursion limit on a deeply nested one.
    """
    names: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        name = getattr(getattr(node, "call", None), "__name__", None)
        if name:
            names.add(name)
        stack.extend(getattr(node, "dependencies", []))
    return names


def _live_api_routes(tmp_path) -> list[APIRoute]:
    """Registered /api/* routes of the real app, params normalized to {}.

    Deliberately filtered to isinstance(route, APIRoute): production
    list_wired_tools builds its live-path set from ALL app.routes with no such
    filter (harness/server.py:786). This test asserts a stricter set on purpose
    -- a Mount or the static "/" route was never a candidate for this catalog.
    """
    app = create_app(HarnessConfig.load(tmp_path / ".CyClaw"))
    return [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/")]


def _normalized(path: str) -> str:
    """Collapse both sides of the comparison to the same placeholder shape.

    Live routes carry FastAPI's real {run_id}/{session_id}/{username}
    templates. tests/test_harness_auth.py's GUARDED list carries resolved
    dummy values instead ("0" * 32 for a run id, "does-not-exist" for a
    session/username) -- literal strings, not template syntax. Both must
    collapse to the same {} or every id-bearing route reads as untested.
    """
    path = re.sub(r"\{[^}]+\}", _PARAM, path)
    path = re.sub(r"[0-9a-f]{32}", _PARAM, path)
    return path.replace("does-not-exist", _PARAM)


def test_live_route_extraction_is_not_empty(tmp_path):
    """Rot guard: if create_app's route table ever comes back empty, every other
    assertion in this file would pass vacuously instead of catching real drift."""
    routes = _live_api_routes(tmp_path)
    assert len(routes) >= 40, f"only found {len(routes)} /api/* routes"


def test_every_catalog_row_matches_a_route_with_that_method(tmp_path):
    """The check production code does not do: _as_harness's wired flag is a
    path-only membership test (harness/tools_view.py:113), so a catalog row
    declaring the wrong HTTP method for a real path still renders wired today.
    This asserts method agreement, not just path existence."""
    routes = _live_api_routes(tmp_path)
    by_path: dict[str, set[str]] = {}
    for route in routes:
        by_path.setdefault(_normalized(route.path), set()).update(route.methods or ())

    for name, _slash, method, path, _desc in _HARNESS_SURFACES:
        methods = by_path.get(_normalized(path))
        assert methods is not None, f"catalog row {name!r} points at unregistered path {path!r}"
        assert method in methods, (
            f"catalog row {name!r} declares {method} on {path!r}, "
            f"but the live route only accepts {sorted(methods)}"
        )


def test_every_live_route_is_cataloged_or_exempt(tmp_path):
    """The direction nothing else checks: every registered /api/* route is either
    in _HARNESS_SURFACES or named in _CATALOG_EXEMPT with a stated reason. Adding a
    route now forces a deliberate choice instead of silently rendering invisible
    in `/tools` -- the drift class that left POST /api/web/search and GET/POST
    /api/keys uncataloged."""
    cataloged = {
        (method, _normalized(path))
        for _name, _slash, method, path, _desc in _HARNESS_SURFACES
    }
    live = {(method, _normalized(route.path)) for route in _live_api_routes(tmp_path) for method in route.methods or ()}

    uncovered = live - cataloged - _CATALOG_EXEMPT
    assert not uncovered, (
        f"registered /api/* route(s) neither cataloged in _HARNESS_SURFACES nor "
        f"listed in _CATALOG_EXEMPT: {sorted(uncovered)}"
    )

    # And the reverse: an exemption for a route that no longer exists is a stale
    # comment pretending to document current behavior.
    stale = _CATALOG_EXEMPT - live
    assert not stale, f"_CATALOG_EXEMPT names route(s) no longer registered: {sorted(stale)}"


def test_every_guarded_route_is_in_the_auth_test_list(tmp_path):
    """Every route carrying the harness's API-key dependency chain must appear in
    tests/test_harness_auth.py's GUARDED list, or its 401-on-missing-key behavior
    is untested. This is exactly the gap /api/keys sat in: guarded in
    harness/server.py from the start, absent from GUARDED, absent from the
    catalog, and undercounted in CLAUDE.md's guarded-route total -- one omission,
    three consequences.
    """
    from tests.test_harness_auth import GUARDED  # deliberately local; see module docstring

    tested = {(method.upper(), _normalized(path)) for method, path, _body in GUARDED}

    guarded_live = set()
    for route in _live_api_routes(tmp_path):
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        if _KEY_DEPENDENCY_NAME not in _dependant_call_names(dependant):
            continue
        for method in route.methods or ():
            key = (method, _normalized(route.path))
            if key in _KEY_EXEMPT:
                continue
            guarded_live.add(key)

    untested = guarded_live - tested
    assert not untested, (
        f"route(s) carry the harness API-key dependency but have no GUARDED entry "
        f"in tests/test_harness_auth.py, so their auth-rejection behavior is "
        f"untested: {sorted(untested)}"
    )
