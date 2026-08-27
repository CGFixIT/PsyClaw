"""Phase 5 ToolBroker adversarial pack (issue #1134 §14 tool subset).

This is the name-gate pack, not the whole §14 list. Deferred owners:

- Unicode / injection prompts → ``utils/sanitizer.py`` + injection-redteam
- Soul/system leak → #1155 ``detect_soul_leak``
- Sandbox / descendant processes → #1153 Job Object
- Approval / manifest TOCTOU → #1154
- Live groundedness judge → #1048 ``tests/judge_eval.py`` (``CYCLAW_EVAL_LIVE=1``)
- Host/network SSRF → ``harness/web_search.py`` (not ``decide()``)

``decide()`` is a name-gate: allowlisted names with hostile argv stay allowed
here. Argv is digested, never executed, never interpreted as a path or shell.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from utils.tool_broker import ToolDenied, assert_allowed, decide

_WEB = frozenset({"web_fetch"})
_CYRILLIC_E = "w\u0435b_fetch"  # lookalike of web_fetch (Cyrillic ye)


@pytest.mark.parametrize(
    ("name", "argv", "allowlist", "allowed", "reason"),
    [
        ("shell", ("id",), _WEB, False, "unknown tool"),
        ("rm", ("-rf", "/"), _WEB, False, "unknown tool"),
        ("eval", ("1",), _WEB, False, "unknown tool"),
        ("subprocess", ("run",), _WEB, False, "unknown tool"),
        ("mcp_call", ("hybrid_search",), _WEB, False, "unknown tool"),
        ("", (), _WEB, False, "empty tool name"),
        ("   ", (), _WEB, False, "empty tool name"),
        (_CYRILLIC_E, ("https://example.com/",), _WEB, False, "unknown tool"),
        ("Web_Fetch", ("https://example.com/",), _WEB, False, "unknown tool"),
        ("web_fetch", ("; rm -rf /",), _WEB, True, "allowlisted"),
        ("web_fetch", ("../etc/passwd",), _WEB, True, "allowlisted"),
        ("web_search", ("q",), frozenset({"web_search"}), True, "allowlisted"),
        ("harness_loop", ("sess",), frozenset({"harness_loop"}), True, "allowlisted"),
        ("agent_run", ("real-repo-run",), frozenset({"agent_run"}), True, "allowlisted"),
        ("agent_run", ("real-repo-run",), _WEB, False, "unknown tool"),
    ],
)
def test_adversarial_names(
    name: str,
    argv: tuple[str, ...],
    allowlist: frozenset[str],
    allowed: bool,
    reason: str,
) -> None:
    verdict = decide(name, argv, allowlist=allowlist)
    assert verdict.allowed is allowed
    assert verdict.reason == reason
    assert len(verdict.argv_digest) == 64
    joined = " ".join(argv)
    # Hex digest can coincidentally contain a one-character token.
    if len(joined) >= 4:
        assert joined not in verdict.argv_digest


def test_audit_event_has_no_raw_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict[str, Any]] = []

    def _capture(event: dict[str, Any], **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr("utils.tool_broker.audit_log", _capture)
    argv = ("https://evil.example/token=sekrit; rm -rf /",)
    with pytest.raises(ToolDenied):
        assert_allowed("shell", argv, allowlist=_WEB)
    assert len(events) == 1
    payload = events[0]
    assert payload["event"] == "tool_broker_decision"
    assert payload["tool"] == "shell"
    assert payload["allowed"] is False
    blob = repr(payload)
    assert "evil.example" not in blob
    assert "sekrit" not in blob
    assert "; rm" not in blob
    assert argv[0] not in blob


def test_fake_nemo_allow_still_cannot_grant() -> None:
    class _Rails:
        status = "ALLOW"

    with pytest.raises(TypeError):
        decide("shell", ("id",), allowlist=_WEB, rails=_Rails())  # type: ignore[call-arg]
    assert "rails" not in inspect.signature(decide).parameters


def test_decide_does_not_spawn() -> None:
    """AST pin: ``from subprocess import run`` would bypass a module monkeypatch."""
    source = (Path(__file__).resolve().parent.parent / "utils" / "tool_broker.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert "subprocess" not in imported
    decide("web_fetch", ("; rm -rf /",), allowlist=_WEB)
    decide("shell", ("id",), allowlist=_WEB)
