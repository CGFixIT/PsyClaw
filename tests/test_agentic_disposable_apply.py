"""Disposable-copy acceptance proof before real-repo finalize."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agentic.executor.apply import prove_disposable_copy
from agentic.executor.manifest import build_manifest, git_head
from utils.errors import AgenticError

# Matches RUN_ID_RE (32 hex). Repeated zeros, not a high-entropy token (DS173237).
_RUN = "0" * 32


def _git_init(root: Path) -> None:
    git_bin = shutil.which("git")
    assert git_bin is not None
    subprocess.run([git_bin, "init"], cwd=str(root), check=True, capture_output=True)
    subprocess.run([git_bin, "config", "user.name", "t"], cwd=str(root), check=True, capture_output=True)
    subprocess.run([git_bin, "config", "user.email", "t@t"], cwd=str(root), check=True, capture_output=True)
    (root / "keep.txt").write_text("k\n", encoding="utf-8")
    subprocess.run([git_bin, "add", "keep.txt"], cwd=str(root), check=True, capture_output=True)
    subprocess.run([git_bin, "commit", "-m", "i"], cwd=str(root), check=True, capture_output=True)


def test_matching_copy_allows(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    head = git_head(tmp_path)
    _, digest = build_manifest(tmp_path, ["a.txt"], run_id=_RUN, base_head=head)
    assert (
        prove_disposable_copy(
            tmp_path,
            ["a.txt"],
            run_id=_RUN,
            base_head=head,
            expected_digest=digest,
        )
        == digest
    )


def test_mutated_copy_denies(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    head = git_head(tmp_path)
    _, digest = build_manifest(tmp_path, ["a.txt"], run_id=_RUN, base_head=head)
    (tmp_path / "a.txt").write_text("mutated\n", encoding="utf-8")
    with pytest.raises(AgenticError, match="digest mismatch"):
        prove_disposable_copy(
            tmp_path,
            ["a.txt"],
            run_id=_RUN,
            base_head=head,
            expected_digest=digest,
        )


def test_missing_worktree_denies(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-tree"
    with pytest.raises(AgenticError, match="not a directory"):
        prove_disposable_copy(
            missing,
            ["a.txt"],
            run_id=_RUN,
            base_head="abc",
            expected_digest="0" * 64,
        )
