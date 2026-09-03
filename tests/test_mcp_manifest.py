"""Unit tests for the MCP tool-manifest fingerprint helper (#974 E2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.mcp_manifest import (
    ManifestDriftError,
    compare_registered_tools,
    load_committed_manifest,
    manifest_fingerprint,
    verify_registered_tools,
)

_HEX64 = 64

_SEARCH = {
    "name": "hybrid_search",
    "description": "Search local corpus",
    "inputSchema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}
_PING = {
    "name": "ping",
    "description": "Liveness",
    "inputSchema": {"type": "object", "properties": {}},
}


def _tools(*items: dict[str, object]) -> list[dict[str, object]]:
    return [dict(item) for item in items]


def test_fingerprint_is_stable_for_same_tools() -> None:
    tools = _tools(_SEARCH)
    assert manifest_fingerprint(tools) == manifest_fingerprint(tools)


def test_tool_list_order_does_not_change_fingerprint() -> None:
    forward = _tools(_SEARCH, _PING)
    reverse = _tools(_PING, _SEARCH)
    assert manifest_fingerprint(forward) == manifest_fingerprint(reverse)


def test_description_change_changes_fingerprint() -> None:
    original = _tools(_SEARCH)
    edited = _tools({**_SEARCH, "description": "planted description"})
    assert manifest_fingerprint(original) != manifest_fingerprint(edited)


def test_input_schema_change_changes_fingerprint() -> None:
    original = _tools(_SEARCH)
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
        "required": ["query"],
    }
    edited = _tools({**_SEARCH, "inputSchema": schema})
    assert manifest_fingerprint(original) != manifest_fingerprint(edited)


def test_compare_matching_pin_is_ok(tmp_path: Path) -> None:
    tools = _tools(_SEARCH, _PING)
    pin = tmp_path / "mcp_manifest.json"
    pin.write_text(json.dumps(tools), encoding="utf-8")
    result = compare_registered_tools(tools, path=pin)
    assert result.ok is True
    assert result.expected == result.actual
    assert result.actual == manifest_fingerprint(tools)


def test_compare_planted_description_is_not_ok(tmp_path: Path) -> None:
    tools = _tools(_SEARCH)
    planted = _tools({**_SEARCH, "description": "planted description"})
    pin = tmp_path / "mcp_manifest.json"
    pin.write_text(json.dumps(planted), encoding="utf-8")
    result = compare_registered_tools(tools, path=pin)
    assert result.ok is False
    assert result.expected != result.actual
    assert result.expected == manifest_fingerprint(planted)
    assert result.actual == manifest_fingerprint(tools)
    assert len(result.expected) == _HEX64
    assert len(result.actual) == _HEX64
    assert result.expected.isalnum()
    assert result.actual.isalnum()


def test_verify_raises_on_planted_drift(tmp_path: Path) -> None:
    tools = _tools(_SEARCH)
    planted = _tools({**_SEARCH, "description": "planted description"})
    pin = tmp_path / "mcp_manifest.json"
    pin.write_text(json.dumps(planted), encoding="utf-8")
    with pytest.raises(ManifestDriftError) as caught:
        verify_registered_tools(tools, path=pin)
    err = caught.value
    assert err.expected == manifest_fingerprint(planted)
    assert err.actual == manifest_fingerprint(tools)
    assert err.expected != err.actual


def test_missing_pin_fails_closed(tmp_path: Path) -> None:
    tools = _tools(_SEARCH)
    missing = tmp_path / "absent.json"
    result = compare_registered_tools(tools, path=missing)
    assert result.ok is False
    assert result.expected == "missing"
    assert result.actual == manifest_fingerprint(tools)
    with pytest.raises(ManifestDriftError) as caught:
        verify_registered_tools(tools, path=missing)
    assert caught.value.expected == "missing"
    assert caught.value.actual == result.actual
    with pytest.raises(FileNotFoundError, match="MCP tool manifest pin missing"):
        load_committed_manifest(missing)


def test_load_committed_manifest_rejects_non_list(tmp_path: Path) -> None:
    pin = tmp_path / "mcp_manifest.json"
    pin.write_text(json.dumps({"tools": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON list"):
        load_committed_manifest(pin)


def test_extra_keys_are_ignored() -> None:
    bare = _tools(_SEARCH)
    padded = _tools({**_SEARCH, "annotations": {"readOnlyHint": True}, "extra": 1})
    assert manifest_fingerprint(bare) == manifest_fingerprint(padded)
