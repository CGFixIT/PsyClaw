"""Windows-runnable unit tests for fsconnect fail-closed edge cases.

The larger fsconnect fixtures are POSIX-only, but these small tests exercise the
pure Python logic directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentic.fsconnect import quota
from agentic.fsconnect.writer import FsWriter
from utils.errors import FsConnectError


def test_walk_usage_raises_when_root_is_unreadable():
    """A permission-denied/missing root must not silently report zero usage."""
    with pytest.raises(OSError):
        quota._walk_usage("/nonexistent/quota/root")


def test_purge_tree_refuses_unbounded_depth():
    """A trash tree deeper than _MAX_PURGE_DEPTH must raise FsConnectError, not
    RecursionError.
    """
    roots = MagicMock()
    roots.stat.return_value = {"type": "dir"}
    roots.list_dir.return_value = [{"name": "x"}]

    writer = FsWriter.__new__(FsWriter)
    writer._roots = roots

    with pytest.raises(FsConnectError):
        # Start one level below the ceiling; the next recursive call must breach it.
        writer._purge_tree(None, "trash/payload", None, _depth=writer._MAX_PURGE_DEPTH - 1)
