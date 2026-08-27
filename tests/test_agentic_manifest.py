"""Acceptance-manifest TOCTOU: digest mismatch refuses approve."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agentic.executor.manifest import build_manifest, git_head, verify_manifest
from agentic.real_repo_loop import finalize_real_repo_change
from utils.errors import AgenticError

_RUN = "0123456789abcdef0123456789abcdef"


def _git_init(root: Path) -> None:
    git_bin = shutil.which("git")
    assert git_bin is not None
    subprocess.run([git_bin, "init"], cwd=str(root), check=True, capture_output=True)
    subprocess.run([git_bin, "config", "user.name", "t"], cwd=str(root), check=True, capture_output=True)
    subprocess.run([git_bin, "config", "user.email", "t@t"], cwd=str(root), check=True, capture_output=True)
    (root / "keep.txt").write_text("k\n", encoding="utf-8")
    subprocess.run([git_bin, "add", "keep.txt"], cwd=str(root), check=True, capture_output=True)
    subprocess.run([git_bin, "commit", "-m", "i"], cwd=str(root), check=True, capture_output=True)


def test_build_and_verify_round_trip(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    head = git_head(tmp_path)
    _payload, digest = build_manifest(tmp_path, ["a.txt"], run_id=_RUN, base_head=head)
    assert len(digest) == 64
    assert verify_manifest(
        tmp_path, ["a.txt"], run_id=_RUN, base_head=head, expected_digest=digest,
    ) == digest


def test_mutated_bytes_fail_closed(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    head = git_head(tmp_path)
    _, digest = build_manifest(tmp_path, ["a.txt"], run_id=_RUN, base_head=head)
    (tmp_path / "a.txt").write_text("mutated\n", encoding="utf-8")
    with pytest.raises(AgenticError, match="digest mismatch"):
        verify_manifest(
            tmp_path, ["a.txt"], run_id=_RUN, base_head=head, expected_digest=digest,
        )


def test_missing_path_fail_closed(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    head = git_head(tmp_path)
    _, digest = build_manifest(tmp_path, ["a.txt"], run_id=_RUN, base_head=head)
    (tmp_path / "a.txt").unlink()
    with pytest.raises(AgenticError, match="missing"):
        verify_manifest(
            tmp_path, ["a.txt"], run_id=_RUN, base_head=head, expected_digest=digest,
        )


def test_path_escape_fail_closed(tmp_path: Path) -> None:
    _git_init(tmp_path)
    with pytest.raises(AgenticError, match="unsafe|escapes"):
        build_manifest(tmp_path, ["../outside.txt"], run_id=_RUN, base_head="abc")


def test_missing_digest_refuses_approve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_agentic_real_repo_loop import _cloned_tools

    with _cloned_tools(tmp_path, monkeypatch) as tools:
        (tools.worktree / "a.txt").write_text("changed\n", encoding="utf-8")
        with pytest.raises(AgenticError, match="acceptance_digest"):
            finalize_real_repo_change(
                tools,
                branch_name="agent/no-digest",
                commit_message="no",
                changed_files=["a.txt"],
                decision="approve",
                protected_write_paths=(),
            )


def test_manifest_json_is_canonical(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    head = git_head(tmp_path)
    payload, digest = build_manifest(
        tmp_path, ["b.txt", "a.txt"], run_id=_RUN, base_head=head,
    )
    files = payload["files"]
    assert isinstance(files, list)
    assert [row["path"] for row in files] == ["a.txt", "b.txt"]
    rebuilt = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert digest == __import__("hashlib").sha256(rebuilt.encode()).hexdigest()
