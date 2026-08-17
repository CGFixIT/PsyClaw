"""Pure compare/verify layer for the committed MCP tools manifest pin.

Fingerprints the registered ``TOOLS`` list (name / description / inputSchema)
so drift against ``mcp_manifest.json`` fails closed. No update/delete helpers.
Never logs tool dumps.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST_PATH = _REPO_ROOT / "mcp_manifest.json"

_TOOL_FIELDS = ("name", "description", "inputSchema")


class ManifestDriftError(Exception):
    """Registered tools no longer match the committed pin."""

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"MCP tool manifest drift: expected {expected}, actual {actual}")


@dataclass(frozen=True, slots=True)
class ManifestCompareResult:
    ok: bool
    expected: str
    actual: str


def canonical_tools_blob(tools: list[dict[str, Any]]) -> str:
    """Stable JSON for the three exported tool fields, sorted by name."""
    canonical = [{field: tool.get(field) for field in _TOOL_FIELDS} for tool in tools]
    canonical.sort(key=lambda tool: str(tool.get("name") or ""))
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"))


def manifest_fingerprint(tools: list[dict[str, Any]]) -> str:
    """SHA-256 hex of ``canonical_tools_blob``."""
    return hashlib.sha256(canonical_tools_blob(tools).encode("utf-8")).hexdigest()


def load_committed_manifest(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the committed JSON tool list. Missing file or non-list JSON fails closed."""
    pin = DEFAULT_MANIFEST_PATH if path is None else path
    if not pin.is_file():
        raise FileNotFoundError(f"MCP tool manifest pin missing: {pin}")
    payload = json.loads(pin.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"MCP tool manifest pin must be a JSON list: {pin}")
    return payload


def compare_registered_tools(
    tools: list[dict[str, Any]], *, path: Path | None = None
) -> ManifestCompareResult:
    """Fingerprint registered tools against the committed pin."""
    pin = DEFAULT_MANIFEST_PATH if path is None else path
    actual = manifest_fingerprint(tools)
    if not pin.is_file():
        return ManifestCompareResult(ok=False, expected="missing", actual=actual)
    expected = manifest_fingerprint(load_committed_manifest(pin))
    return ManifestCompareResult(ok=expected == actual, expected=expected, actual=actual)


def verify_registered_tools(
    tools: list[dict[str, Any]], *, path: Path | None = None
) -> ManifestCompareResult:
    """Raise ``ManifestDriftError`` when the pin is missing or drifted."""
    result = compare_registered_tools(tools, path=path)
    if not result.ok:
        raise ManifestDriftError(expected=result.expected, actual=result.actual)
    return result
