"""Read-only inventory of harness tools and whether each is actually wired.

``GET /api/tools`` (and the ``/tools`` slash command) use this view. Harness
surfaces are wired only when the live FastAPI app registered their route.
MCP tools are AST-parsed from ``mcp_hybrid_server.py`` (I6: never imported)
and are catalog-wired only — the console does not invoke them.

The payload is data. The ASCII diagram is a convenience for the console
reply and for ``curl``; it is not HTML.
"""

from __future__ import annotations

from typing import Final, TypedDict

from harness.registry_view import _DESC_KEY, _NAME_KEY, list_mcp_tools

_POST: Final = "POST"
_GET: Final = "GET"
_HARNESS: Final = "harness"
_MCP: Final = "mcp"
_BOX_BAR = "─"


class ToolRecord(TypedDict):
    """One inventory row. ``wired`` is computed; the rest is the catalog."""

    name: str
    slash: str
    method: str
    path: str
    description: str
    kind: str
    invoked: bool
    wired: bool


def _surface(
    name: str,
    slash: str,
    method: str,
    path: str,
    description: str,
) -> tuple[str, str, str, str, str]:
    return (name, slash, method, path, description)


# Paths must match FastAPI templates in harness/server.py exactly.
_HARNESS_SURFACES: tuple[tuple[str, str, str, str, str], ...] = (
    _surface("chat", "(plain text)", _POST, "/api/chat",
             "local Ollama chat turn"),
    _surface("goal", "/goal", _POST, "/api/sessions/{session_id}/goal",
             "session-scoped operator intent (not a write authorization)"),
    _surface("loop", "/loop", _POST, "/api/chat",
             "human-gated chat turns toward /goal; never starts /api/agent/*"),
    _surface("cancel", "/loop stop", _POST, "/api/chat/cancel",
             "abort the in-flight Ollama socket"),
    _surface("session", "/session", _GET, "/api/sessions",
             "list, create, switch, or rename sessions"),
    _surface("soul", "/soul", _GET, "/api/soul",
             "harness-local soul-in-prompt toggle (does not write soul.md)"),
    _surface("memory", "/memory", _GET, "/api/memory",
             "operator notes in prompt (off by default; not RAG memory/)"),
    _surface("memory-add", "/memory add", _POST, "/api/memory/add",
             "pin one injection-scanned operator note"),
    _surface("model", "/model", _POST, "/api/model",
             "select the local chat model"),
    _surface("status", "/status", _GET, "/api/status",
             "harness health, layout, and token tally"),
    _surface("registry", "/registry", _GET, "/api/registry",
             "merged skills / MCP catalog / connectors"),
    _surface("github", "/github", _GET, "/api/github/status",
             "read-only agentic GitHub status (subprocess)"),
    _surface("agent-checks", "/agent checks", _GET, "/api/agent/checks",
             "named verification profiles (no subprocess)"),
    _surface("agent-run", "/agent confirm", _POST, "/api/agent/run",
             "human-gated real-repo run (reason + confirm required)"),
    _surface("agent-status", "/agent status", _GET, "/api/agent/runs/{run_id}",
             "inspect a coding-agent run record"),
    _surface("agent-decision", "/agent approve", _POST,
             "/api/agent/runs/{run_id}/decision",
             "approve or reject a pending run (reaches a git write)"),
    _surface("agent-push", "/agent push", _POST,
             "/api/agent/runs/{run_id}/push",
             "push an approved branch (disarmed by default)"),
    _surface("agent-publish", "/agent publish", _POST,
             "/api/agent/runs/{run_id}/publish",
             "open a draft PR (disarmed by default; reason required)"),
    _surface("agent-discard", "/agent discard", _POST,
             "/api/agent/runs/{run_id}/discard",
             "reclaim the clone of a decided run"),
    _surface("harness", "/harness", _GET, "/api/harness/runs",
             "local harness-optimizer run listing"),
    _surface("tools", "/tools", _GET, "/api/tools",
             "this inventory — wired-tool diagram"),
    _surface("skills", "/skills", _GET, "/api/skills",
             "wired skill diagram (prompt + agent-check)"),
    _surface("web", "/web", _GET, "/api/web",
             "allowlist-only web fetch (off until /web on)"),
    _surface("web-fetch", "/web fetch", _POST, "/api/web/fetch",
             "GET one allowlisted URL; no crawl, no search engine"),
    _surface("web-search", "/web search", _POST, "/api/web/search",
             "grep allowlisted pages for a query (no search engine)"),
    _surface("keys", "/api", _GET, "/api/keys",
             "managed credential status (masked tail only, never a value)"),
)


def _as_harness(spec: tuple[str, str, str, str, str], paths: frozenset[str]) -> ToolRecord:
    return {
        _NAME_KEY: spec[0],
        "slash": spec[1],
        "method": spec[2],
        "path": spec[3],
        _DESC_KEY: spec[4],
        "kind": _HARNESS,
        "invoked": True,
        "wired": spec[3] in paths,
    }


def _as_mcp(tool: dict) -> ToolRecord:
    name = str(tool.get(_NAME_KEY, "") or "")
    return {
        _NAME_KEY: name,
        "slash": "(mcp catalog)",
        "method": "AST",
        "path": f"mcp://{name}",
        _DESC_KEY: str(tool.get(_DESC_KEY, "") or ""),
        "kind": _MCP,
        "invoked": False,
        "wired": bool(name),
    }


def _tree_pair(row: ToolRecord, last: bool, indent: str) -> tuple[str, str]:
    branch = "└─" if last else "├─"
    mark = "●" if row["wired"] else "○"
    child = "  " if last else "│ "
    head = f"{indent}{branch}[{row['name']}] {mark}  {row['method']} {row['path']}"
    detail = f"{indent}{child} {row['slash']:<16} {row['description']}"
    return head, detail


def _box(row: ToolRecord) -> str:
    invoked = "yes" if row["invoked"] else "no (catalog only)"
    wired_label = "yes" if row["wired"] else "no"
    inner = (
        f" name        {row['name']}",
        f" slash       {row['slash']}",
        f" route       {row['method']} {row['path']}",
        f" kind        {row['kind']}",
        f" invoked     {invoked}",
        f" wired       {wired_label}",
        "",
        f" {row['description']}",
    )
    width = max(len(line) for line in inner)
    edge = _BOX_BAR * (width + 1)
    title = f" /{row['name']} "
    prefix = title if len(title) < len(edge) else ""
    rest = edge[len(prefix):]
    head = f"┌{prefix}{rest}┐"
    body = [f"│{line.ljust(width)} │" for line in inner]
    return "\n".join((head, *body, f"└{edge}┘"))


def render_tools_diagram(tools: list[ToolRecord], *, wired: int, total: int) -> str:
    """Monospaced wiring diagram for the console reply (text, never HTML)."""
    if len(tools) == 1:
        return _box(tools[0])
    lines = [f"HARNESS TOOLS — {wired} wired / {total} listed", ""]
    harness = [row for row in tools if row["kind"] == _HARNESS]
    mcp = [row for row in tools if row["kind"] == _MCP]
    _append_harness_tree(lines, harness, has_mcp=bool(mcp))
    _append_mcp_tree(lines, mcp)
    if not harness and not mcp:
        lines.append("(none)")
    return "\n".join(lines)


def _append_harness_tree(
    lines: list[str],
    harness: list[ToolRecord],
    *,
    has_mcp: bool,
) -> None:
    if not harness:
        return
    lines.append("console")
    last_index = len(harness) - 1
    for index, row in enumerate(harness):
        last = index == last_index and not has_mcp
        head, detail = _tree_pair(row, last, "")
        lines.append(head)
        lines.append(detail)


def _append_mcp_tree(lines: list[str], mcp: list[ToolRecord]) -> None:
    if not mcp:
        return
    lines.append("└─ mcp (AST catalog; this console does not invoke these)")
    last_index = len(mcp) - 1
    for index, row in enumerate(mcp):
        head, detail = _tree_pair(row, index == last_index, "   ")
        lines.append(head)
        lines.append(detail)


def list_wired_tools(registered_paths: frozenset[str]) -> dict[str, object]:
    """Full inventory plus a default diagram of the wired subset."""
    tools = [_as_harness(spec, registered_paths) for spec in _HARNESS_SURFACES]
    tools.extend(_as_mcp(tool) for tool in list_mcp_tools() if tool.get(_NAME_KEY))
    wired_rows = [row for row in tools if row["wired"]]
    count = len(wired_rows)
    return {
        "tools": tools,
        "wired": count,
        "total": len(tools),
        "diagram": render_tools_diagram(wired_rows, wired=count, total=len(tools)),
    }
