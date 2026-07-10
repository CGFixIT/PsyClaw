"""Local-only memory path helpers for future Deep Agents integration."""

from __future__ import annotations

from pathlib import Path


def default_memory_dir(repo_root: Path) -> Path:
    """Return the local-only Deep Agent memory directory."""

    return repo_root / "data" / "agentic" / "deepagent_github"
