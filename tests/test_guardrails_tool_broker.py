"""ToolBroker: deny unknown names; NeMo cannot grant."""

from __future__ import annotations

import inspect

import pytest

from guardrails.tool_broker import ToolDenied, assert_allowed, decide
from harness.config import HarnessConfig
from harness.web_search import WebTool, WebToolError


def test_decide_has_no_rails_parameter() -> None:
    assert "rails" not in inspect.signature(decide).parameters
    assert "nemo" not in inspect.signature(decide).parameters


def test_allowlisted_name_is_allowed() -> None:
    v = decide("web_fetch", ("https://example.com/x",), allowlist=frozenset({"web_fetch"}))
    assert v.allowed is True
    assert len(v.argv_digest) == 64
    assert "example.com" not in v.argv_digest


def test_unknown_name_is_denied() -> None:
    v = decide("shell", ("rm", "-rf", "/"), allowlist=frozenset({"web_fetch"}))
    assert v.allowed is False
    assert v.reason == "unknown tool"


def test_empty_allowlist_is_denied() -> None:
    v = decide("web_fetch", ("https://example.com/",), allowlist=frozenset())
    assert v.allowed is False


def test_assert_allowed_raises_on_unknown() -> None:
    with pytest.raises(ToolDenied, match="unknown tool"):
        assert_allowed("shell", ("id",), allowlist=frozenset({"web_fetch"}))


def test_fake_nemo_allow_cannot_be_passed_to_decide() -> None:
    class _Rails:
        status = "ALLOW"

    with pytest.raises(TypeError):
        decide("shell", (), allowlist=frozenset({"web_fetch"}), rails=_Rails())  # type: ignore[call-arg]


def test_web_fetch_denied_when_broker_allowlist_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CYCLAW_HOME", str(tmp_path / ".CyClaw"))
    cfg = HarnessConfig.load()
    cfg.web_enabled = True
    tool = WebTool(cfg)
    monkeypatch.setattr(tool, "_web_tool_allowlist", lambda: frozenset())
    monkeypatch.setattr(
        tool,
        "_require_enabled",
        lambda: [{"host": "example.com", "path": "/", "scheme": "https", "raw": "https://example.com/"}],
    )
    with pytest.raises(WebToolError) as exc:
        tool.fetch("https://example.com/")
    assert exc.value.code == "WEB_TOOL_DENIED"
