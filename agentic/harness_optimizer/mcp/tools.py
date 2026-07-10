"""Scoped proposer workspace tools for local harness optimization.

These are plain Python wrappers in phase 4. They enforce the same boundary a
future MCP server must expose: no shell, no GitHub writes, no unrestricted file
tool, no holdout reads, and writes only under ``current/`` or via the explicit
``finish_proposal`` proposal tool.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import NoReturn

from agentic.harness_optimizer.proposer import ProposerWorkspace
from utils.errors import AgenticError
from utils.logger import audit_log, hash_query

_SEP_RE = re.compile(r"[\\/]+")
_DEFAULT_MAX_READ_BYTES = 256_000


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parts(target: str) -> tuple[str, ...]:
    if not isinstance(target, str) or "\x00" in target:
        raise AgenticError("workspace target must be a non-empty string without NUL bytes")
    if target.startswith(("\\\\", "//")) or Path(target).is_absolute() or PureWindowsPath(target).is_absolute():
        raise AgenticError("workspace target must be relative")
    parts = tuple(part for part in _SEP_RE.split(target) if part not in {"", "."})
    for part in parts:
        if part == "..":
            raise AgenticError("'..' is not allowed in workspace targets")
        if ":" in part:
            raise AgenticError("':' is not allowed in workspace path components")
        if part != part.rstrip(" ."):
            raise AgenticError("trailing dot or space is not allowed in workspace path components")
    return parts


def _contains(root: Path, candidate: Path) -> bool:
    root_resolved = root.resolve()
    try:
        candidate_resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        candidate_resolved = candidate.parent.resolve(strict=True) / candidate.name
    return candidate_resolved == root_resolved or root_resolved in candidate_resolved.parents


def _contains_write_target(root: Path, candidate: Path) -> bool:
    """Containment check for a write target that may not exist yet.

    Nested write targets (``sub/dir/file.md``) have no existing parent, so
    unlike ``_contains`` this walks up to the nearest ancestor that *does*
    exist and resolves that one strictly. An existing directory inside
    ``root`` that is itself a symlink escaping ``root`` is still caught,
    because the walk resolves it on the way up; components that don't exist
    yet can't be symlinks, so no further check is needed for them.
    """
    root_resolved = root.resolve()
    ancestor = candidate
    while True:
        try:
            ancestor_resolved = ancestor.resolve(strict=True)
            break
        except FileNotFoundError:
            if ancestor.parent == ancestor:
                raise
            ancestor = ancestor.parent
    return ancestor_resolved == root_resolved or root_resolved in ancestor_resolved.parents


@dataclass(frozen=True)
class ProposerWorkspaceTools:
    """Audited tool boundary for one proposer workspace."""

    workspace: ProposerWorkspace
    config_path: str = "config.yaml"
    cfg: dict | None = None
    rag_search: Callable[[str], list[dict]] | None = None
    max_read_bytes: int = _DEFAULT_MAX_READ_BYTES

    def _audit(self, allowed: bool, tool: str, **extra: object) -> None:
        audit_log(
            {
                "event": "agentic_harness_workspace_tool_allowed"
                if allowed
                else "agentic_harness_workspace_tool_denied",
                "tool": tool,
                "workspace_root": str(self.workspace.root),
                **extra,
            },
            config_path=self.config_path,
            cfg=self.cfg,
        )

    # NoReturn (not None): _deny always raises. Without this, the two
    # `-> Path` resolvers below would have an implicit-None fall-through on
    # their `except OSError` branches that type checkers can't see, and a
    # future refactor that made _deny not raise would silently hand callers
    # a None path.
    def _deny(self, tool: str, message: str, **extra: object) -> NoReturn:
        self._audit(False, tool, reason=message, **extra)
        raise AgenticError(message, details={"tool": tool, **extra})

    def _resolve_existing_read(self, target: str, tool: str) -> Path:
        try:
            parts = _parts(target)
            if parts and parts[0] == "holdout_hidden":
                self._deny(tool, "holdout_hidden is not readable by proposer tools", target=target)
            path = self.workspace.root.joinpath(*parts)
            resolved = path.resolve(strict=True)
            if not _contains(self.workspace.root, resolved):
                self._deny(tool, "workspace read escaped root", target=target)
            return resolved
        except AgenticError:
            raise
        except OSError as exc:
            self._deny(tool, "workspace read target is not accessible", target=target, error_type=type(exc).__name__)

    def _resolve_current_write(self, target: str, tool: str) -> Path:
        try:
            parts = _parts(target)
            path = self.workspace.current_dir.joinpath(*parts)
            if not _contains_write_target(self.workspace.current_dir, path):
                self._deny(tool, "workspace write escaped current/", target=target)
            if path.exists() and path.is_dir():
                self._deny(tool, "workspace write target is a directory", target=target)
            return path
        except AgenticError:
            raise
        except OSError as exc:
            self._deny(tool, "workspace write target is not accessible", target=target, error_type=type(exc).__name__)

    def _read_text(self, path: Path, tool: str, target: str) -> str:
        size = path.stat().st_size
        if size > self.max_read_bytes:
            self._deny(tool, "workspace read target exceeds max_read_bytes", target=target, size=size)
        text = path.read_text(encoding="utf-8")
        self._audit(True, tool, target=target, bytes=len(text.encode("utf-8")), sha256=_sha256_text(text))
        return text

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.cyclaw-tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    def list_workspace(self, target: str = "") -> list[dict]:
        """List readable workspace entries without exposing holdout contents."""

        tool = "list_workspace"
        path = self.workspace.root if not target else self._resolve_existing_read(target, tool)
        if not path.is_dir():
            self._deny(tool, "workspace list target is not a directory", target=target)
        entries: list[dict] = []
        for entry in sorted(path.iterdir(), key=lambda item: item.name):
            if entry.name == "holdout_hidden":
                continue
            entries.append(
                {
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size": entry.stat().st_size,
                }
            )
        self._audit(True, tool, target=target, entries=len(entries))
        return entries

    def read_file(self, target: str) -> str:
        """Read a visible workspace file."""

        tool = "read_file"
        path = self._resolve_existing_read(target, tool)
        if not path.is_file():
            self._deny(tool, "workspace read target is not a file", target=target)
        return self._read_text(path, tool, target)

    def write_current_file(self, target: str, content: str) -> dict:
        """Write a candidate file under current/ only."""

        tool = "write_current_file"
        if not isinstance(content, str):
            self._deny(tool, "workspace write content must be a string", target=target)
        path = self._resolve_current_write(target, tool)
        self._atomic_write(path, content)
        result = {"target": target, "bytes": len(content.encode("utf-8")), "sha256": _sha256_text(content)}
        self._audit(True, tool, **result)
        return result

    def read_surface_manifest(self) -> dict:
        """Read the local surface manifest."""

        text = self._read_text(self.workspace.manifest_path, "read_surface_manifest", "surface_manifest.json")
        return json.loads(text)

    def read_train_failures(self) -> dict[str, str]:
        """Read visible train artifacts."""

        out: dict[str, str] = {}
        for path in sorted(self.workspace.train_visible_dir.glob("*")):
            if path.is_file():
                rel = path.relative_to(self.workspace.root).as_posix()
                out[path.name] = self.read_file(rel)
        self._audit(True, "read_train_failures", files=len(out))
        return out

    def read_visible_history(self) -> dict[str, str]:
        """Read visible prior-attempt artifacts."""

        out: dict[str, str] = {}
        for path in sorted(self.workspace.history_dir.glob("*")):
            if path.is_file():
                rel = path.relative_to(self.workspace.root).as_posix()
                out[path.name] = self.read_file(rel)
        self._audit(True, "read_visible_history", files=len(out))
        return out

    def rag_search_readonly(self, query: str) -> dict:
        """Call an injected read-only RAG search function, or return no results."""

        tool = "rag_search_readonly"
        if not isinstance(query, str) or not query.strip():
            self._deny(tool, "RAG query must be a non-empty string")
        results = self.rag_search(query) if self.rag_search else []
        self._audit(True, tool, query_hash=hash_query(query), results=len(results))
        return {"query_hash": hash_query(query), "results": results}

    def finish_proposal(self, content: str) -> dict:
        """Write proposal.md through the explicit proposal tool."""

        tool = "finish_proposal"
        if not isinstance(content, str) or not content.strip():
            self._deny(tool, "proposal content must be non-empty")
        self._atomic_write(self.workspace.proposal_path, content)
        result = {"bytes": len(content.encode("utf-8")), "sha256": _sha256_text(content)}
        self._audit(True, tool, **result)
        return result
