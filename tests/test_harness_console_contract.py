"""Contract tests between the harness console (static/harness.html) and harness/server.py.

Same gap tests/test_terminal_contract.py closes for the gateway console, applied
to the second one. harness.html is a static file served by the harness app
itself, so nothing ties its api() targets to the app's registered routes:
rename or remove an endpoint and the full suite stays green while the matching
slash command 404s in the browser.

These tests extract every path the console calls -- including the ones built by
string concatenation (``api('/api/sessions/' + id + '/rename', 'POST')``) --
and assert each exists on the app with the method the console uses.

Runs entirely offline against the app factory (no server, no LLM, no Ollama).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from harness.config import HarnessConfig
from harness.server import create_app

_HARNESS_HTML = Path(__file__).resolve().parent.parent / "static" / "harness.html"
# A path segment the console interpolates (a session id) and that the route
# declares as a parameter. Both sides normalize to this before comparison.
_PARAM = "{}"
_API_CALL_START_RE = re.compile(r"\bapi\(")
_STRING_LITERAL_RE = re.compile(r"'((?:[^'\\]|\\.)*)'")
_METHOD_ARG_RE = re.compile(r"^\s*'([A-Z]+)'\s*$")


def _split_api_args(html: str, open_paren: int) -> list[str]:
    """Split the argument list of the ``api(`` call whose '(' is at ``open_paren``.

    A regex cannot do this: the path expression legitimately contains nested
    parentheses (``encodeURIComponent(currentSession)``) and the body argument
    contains commas and braces. Walks the source tracking string state and
    bracket depth, splitting only on top-level commas.
    """
    args: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for char in html[open_paren + 1:]:
        if quote is not None:
            current.append(char)
            if char == quote and current[-2:-1] != ["\\"]:
                quote = None
            continue
        if char in "'\"`":
            quote = char
            current.append(char)
        elif char in "([{":
            depth += 1
            current.append(char)
        elif char in ")]}":
            if depth == 0:
                args.append("".join(current))
                return args
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            args.append("".join(current))
            current = []
        else:
            current.append(char)
    raise AssertionError(f"unbalanced api( call at offset {open_paren}")


def _split_top_level_concat(expr: str) -> list[str]:
    """Split a JS expression on its top-level ``+`` operators."""
    terms: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for char in expr:
        if quote is not None:
            current.append(char)
            if char == quote and current[-2:-1] != ["\\"]:
                quote = None
        elif char in "'\"`":
            quote = char
            current.append(char)
        elif char in "([{":
            depth += 1
            current.append(char)
        elif char in ")]}":
            depth -= 1
            current.append(char)
        elif char == "+" and depth == 0:
            terms.append("".join(current))
            current = []
        else:
            current.append(char)
    terms.append("".join(current))
    return [term for term in terms if term.strip()]


def _normalize_path_expression(expr: str) -> str:
    """Collapse a JS path expression into a route-comparable path.

    ``'/api/sessions/' + currentSession + '/rename'`` -> ``/api/sessions/{}/rename``
    Every non-literal term becomes the parameter placeholder, so a concatenated
    path is compared against the templated route rather than skipped.
    """
    parts: list[str] = []
    for term in _split_top_level_concat(expr):
        literal = _STRING_LITERAL_RE.fullmatch(term.strip())
        # Only a whole-term literal is path text. A literal NESTED in a call --
        # the '' in encodeURIComponent(rest[1] || '') -- is part of the
        # interpolated expression and must collapse to the placeholder, not
        # contribute an extra empty segment.
        parts.append(literal.group(1) if literal else _PARAM)
    joined = re.sub(re.escape(_PARAM) + "{2,}", _PARAM, "".join(parts))
    # '/api/sessions/' + id  ->  '/api/sessions/{}' (not a trailing empty segment)
    return re.sub(r"/+", "/", joined).rstrip("/") or "/"


def _console_calls() -> set[tuple[str, str]]:
    """Every (path, method) pair static/harness.html invokes."""
    html = _HARNESS_HTML.read_text(encoding="utf-8")
    calls: set[tuple[str, str]] = set()
    for match in _API_CALL_START_RE.finditer(html):
        args = _split_api_args(html, match.end() - 1)
        path = _normalize_path_expression(args[0])
        if not path.startswith("/api/"):
            continue
        method_arg = _METHOD_ARG_RE.match(args[1]) if len(args) > 1 else None
        calls.add((path, method_arg.group(1) if method_arg else "GET"))
    return calls


def _harness_routes(tmp_path) -> dict[str, set[str]]:
    """Registered routes of the real app, with {param} normalized to {}."""
    app = create_app(HarnessConfig.load(tmp_path / ".CyClaw"))
    routes: dict[str, set[str]] = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            key = re.sub(r"\{[^}]+\}", _PARAM, route.path)
            routes.setdefault(key, set()).update(route.methods or ())
    return routes


def test_console_call_extraction_is_not_empty():
    """Regex-rot guard: if harness.html's api() idiom changes and extraction
    breaks, this fails loudly instead of the per-path tests passing vacuously."""
    calls = _console_calls()
    paths = {path for path, _ in calls}
    assert len(paths) >= 8, f"extracted only {sorted(paths)} from harness.html"
    assert "/api/status" in paths
    assert "/api/chat" in paths
    # the concatenated forms specifically -- these are the ones a naive
    # literal-only extractor would silently drop
    assert "/api/sessions/{}" in paths
    assert "/api/sessions/{}/rename" in paths
    assert "/api/sessions/{}/goal" in paths
    assert "/api/tools" in paths
    assert ("/api/chat", "POST") in calls
    assert ("/api/sessions/{}/goal", "POST") in calls
    assert ("/api/tools", "GET") in calls


@pytest.mark.parametrize("path,method", sorted(_console_calls()))
def test_console_endpoint_exists_on_harness_app(path, method, tmp_path):
    routes = _harness_routes(tmp_path)
    assert path in routes, (
        f"static/harness.html calls {path!r} but harness/server.py registers no "
        f"such route — the console would get a 404 at runtime"
    )


@pytest.mark.parametrize("path,method", sorted(_console_calls()))
def test_console_endpoint_accepts_console_method(path, method, tmp_path):
    routes = _harness_routes(tmp_path)
    if path not in routes:
        pytest.skip("missing route reported by test_console_endpoint_exists_on_harness_app")
    assert method in routes[path], (
        f"console calls {method} {path} but the route only allows "
        f"{sorted(routes[path])} — the console would get a 405 at runtime"
    )


def test_session_id_is_url_encoded_on_every_interpolated_path():
    """Operator-typed ids reach the URL; encode them all, not most of them.

    /session use encodeURIComponent()s its argument, three other call sites
    interpolate the id bare. Server-side _ID_RE re-validates so this is a
    consistency defect rather than a live traversal -- but the encoded and
    unencoded forms sitting side by side is exactly how the unencoded one
    survives review.
    """
    html = _HARNESS_HTML.read_text(encoding="utf-8")
    interpolations = re.findall(r"api\('/api/sessions/'\s*\+\s*([A-Za-z_][A-Za-z0-9_]*)", html)
    assert interpolations, "extraction found no interpolated session paths"
    assert not [name for name in interpolations if name != "encodeURIComponent"], (
        "static/harness.html interpolates a session id into a URL without "
        f"encodeURIComponent(): {sorted(set(interpolations))}"
    )


def test_pending_agent_diff_is_shown_before_approval():
    # The browser human gate must display the backend-rendered diff first.
    html = _HARNESS_HTML.read_text(encoding="utf-8")
    start = html.index("function showAgentRecord(rec)")
    end = html.index("/* ── slash commands ── */", start)
    body = html[start:end]

    assert "'candidate diff\\n' + rec.diff" in body
    assert body.index("rec.diff") < body.index("/agent approve ")
    assert "diff not loaded — inspect with /agent status " in body


def test_staged_agent_plan_read_paths_and_checks_reach_confirmation():
    # Console must stage reviewed plan/read/checks, cap reads, and keep the
    # staged run when confirm fails (422) instead of wiping operator state.
    from harness.schemas import _MAX_PLAN_CHARS, _MAX_READ_FILES

    html = _HARNESS_HTML.read_text(encoding="utf-8")
    helper_start = html.index("function showPendingAgentRun()")
    helper_end = html.index("function showAgentRecord(rec)", helper_start)
    helper = html[helper_start:helper_end]

    assert "agentPlanInput.addEventListener('change'" in helper
    assert f"const MAX_AGENT_PLAN_CHARS = {_MAX_PLAN_CHARS};" in html
    assert f"const MAX_AGENT_READ_FILES = {_MAX_READ_FILES};" in html
    assert "function isSafeRepoRelativePath(" in html
    assert "function canonicalRepoRelativePath(" in html
    assert "pendingAgentRun.plan = plan" in helper
    assert "pendingAgentRun !== stagedRun" in helper

    commands_start = html.index("case 'agent':")
    commands_end = html.index("case 'harness':", commands_start)
    commands = html[commands_start:commands_end]
    assert "read_files: []" in commands
    assert "pendingAgentRun.read_files.push(canonical)" in commands
    assert "read_files.length >= MAX_AGENT_READ_FILES" in commands
    assert "pendingAgentRun.checks = requested" in commands
    assert "Object.assign({}, stagedRun, { reason: why, confirm: true })" in commands
    assert "confirm failed — staged run kept" in commands
    assert "pendingAgentRun = stagedRun" in commands


def test_console_documents_goal_and_loop_slash_commands():
    from harness.schemas import _MAX_GOAL_LEN

    html = _HARNESS_HTML.read_text(encoding="utf-8")
    assert "['/goal [text]|clear'" in html
    assert "['/loop [n]|stop|auto'" in html
    assert "case 'goal':" in html
    assert "case 'loop':" in html
    assert f"const MAX_GOAL_CHARS = {_MAX_GOAL_LEN};" in html
    assert "const MAX_LOOP_TURNS = 5;" in html
    assert "const DEFAULT_LOOP_TURNS = 3;" in html
    assert "const LOOP_COOLDOWN_MS = 2000;" in html
    assert "const MAX_LOOP_COMPLETION_TOKENS = 12000;" in html
    assert "body.loop = true" in html
    assert "LOOP_RATE_LIMIT" in html
    assert "CHAT_BUSY" in html
    assert "function requestLoopStop(" in html
    assert "api('/api/chat/cancel', 'POST')" in html
    assert "new AbortController()" in html
    assert "aborting the in-flight turn" in html
    assert "function paintGoalLoop()" in html


def test_console_documents_tools_slash_command():
    html = _HARNESS_HTML.read_text(encoding="utf-8")
    assert "['/tools [all|<name>]'" in html
    assert "case 'tools':" in html
    assert "api('/api/tools')" in html
    assert "function renderToolsDiagram(" in html
    assert "innerHTML" not in html
    start = html.index("case 'tools':")
    end = html.index("case 'github':", start)
    body = html[start:end]
    assert "/api/agent/" not in body
    assert "renderToolsDiagram(" in body
    assert "unknown tool:" in body



def test_loop_command_never_starts_a_real_repo_run():
    """ /loop is chat-only. Auto-invoking /api/agent/* would bypass the human
    /agent confirm + reason gate. """
    html = _HARNESS_HTML.read_text(encoding="utf-8")
    start = html.index("case 'loop':")
    end = html.index("case 'tokens':", start)
    body = html[start:end]
    assert "/api/agent/" not in body
    assert "GOAL_DONE" in html
    assert "function runLoopTurns()" in html
    assert "function replyHasGoalDone(" in html
    assert "never starts a real-repo run" in body

