"""Disposable-copy proof before real-repo finalize (issue #1134 §10 leftover).

``verify_manifest`` binds the acceptance digest against the live worktree.
That still leaves a same-process TOCTOU if hooks or ambient git config mutate
bytes between the check and ``git add``/``commit``. This module copies the
candidate tree into a throwaway directory, disables user/system git config and
pins ``core.hooksPath`` at an empty directory for that proof, re-runs
``verify_manifest`` there, and always destroys the copy. Digests stay the same
format as ``agentic.executor.manifest``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from agentic.executor.manifest import verify_manifest
from utils.errors import AgenticError


def _null_config_path() -> str:
    return "NUL" if os.name == "nt" else os.devnull


@contextmanager
def _scrubbed_git_config_env(*, hooks_path: Path) -> Iterator[None]:
    """Ignore user/system git config; pin ``core.hooksPath`` to an empty dir."""
    null = _null_config_path()
    overlay = {
        "GIT_CONFIG_GLOBAL": null,
        "GIT_CONFIG_SYSTEM": null,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(hooks_path),
    }
    prior = {key: os.environ.get(key) for key in overlay}
    try:
        os.environ.update(overlay)
        yield
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def prove_disposable_copy(
    worktree: Path,
    paths: Sequence[str],
    *,
    run_id: str,
    base_head: str,
    expected_digest: str,
) -> str:
    """Copy ``worktree`` to a temp dir, re-verify the acceptance digest, destroy.

    Raises ``AgenticError`` if the copy fails or the disposable digest drifts.
    Does not commit and does not touch ``origin``.
    """
    root = Path(worktree)
    if not root.is_dir():
        raise AgenticError(
            "disposable-copy proof: worktree is not a directory",
            details={"worktree": str(root)},
        )
    with tempfile.TemporaryDirectory(prefix="cyclaw-disposable-apply-") as tmp:
        base = Path(tmp)
        dest = base / "tree"
        empty_hooks = base / "empty-hooks"
        empty_hooks.mkdir()
        try:
            shutil.copytree(
                root,
                dest,
                symlinks=True,
                ignore_dangling_symlinks=True,
            )
        except OSError as exc:
            raise AgenticError(
                "disposable-copy proof: failed to copy candidate tree",
                details={"worktree": str(root), "error": str(exc)[:200]},
            ) from exc
        with _scrubbed_git_config_env(hooks_path=empty_hooks):
            return verify_manifest(
                dest,
                paths,
                run_id=run_id,
                base_head=base_head,
                expected_digest=expected_digest,
            )
