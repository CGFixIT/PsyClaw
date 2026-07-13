"""Local-only memory path helpers for future Deep Agents integration."""

from __future__ import annotations

from pathlib import Path

from utils.errors import AgenticError

_MEMORY_FILENAME = "AGENTS.md"
_VIRTUAL_MEMORY_PATH = "/memory/AGENTS.md"
_MAX_MEMORY_BYTES = 64_000


def default_memory_dir(repo_root: Path) -> Path:
    """Return the local-only Deep Agent memory directory."""

    return repo_root / "data" / "agentic" / "deepagent_github"


def load_local_memory_files(repo_root: Path) -> dict[str, str]:
    """Return one bounded local AGENTS.md memory file, without creating it."""

    memory_root = default_memory_dir(repo_root).resolve()
    memory_path = (memory_root / _MEMORY_FILENAME).resolve()
    if memory_root not in memory_path.parents:
        raise AgenticError("Deep Agent memory path escaped its local root")
    if not memory_path.exists():
        return {}
    if not memory_path.is_file():
        raise AgenticError("Deep Agent memory path must be a file", details={"path": str(memory_path)})
    if memory_path.stat().st_size > _MAX_MEMORY_BYTES:
        raise AgenticError("Deep Agent memory file exceeds the 64 KB limit", details={"path": str(memory_path)})
    return {_VIRTUAL_MEMORY_PATH: memory_path.read_text(encoding="utf-8")}
