"""Immutable acceptance digest for real-repo approve (issue #1134 §10 slice).

A human reviews a pending run, then a later process calls ``finalize``.
Between those, the worktree or the on-disk record can change. This module
snapshots ``run_id + base HEAD + path→sha256`` at propose time and refuses
approve when a rebuild does not match the stored digest.

Not a signature: an attacker who rewrites both the JSON record and the
files can mint a new digest. This closes the ordinary TOCTOU (reviewed
diff, then files mutated, JSON left alone). Audit logs the digest only.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess  # noqa: S404 -- argv-list git only
from collections.abc import Sequence
from pathlib import Path

from utils.errors import AgenticError


def git_head(worktree: Path) -> str:
    """Return ``HEAD`` of ``worktree``. Fail closed if git cannot answer."""
    git_bin = shutil.which("git")
    if not git_bin:
        raise AgenticError("git executable not found on PATH")
    proc = subprocess.run(  # noqa: S603 -- absolute git from shutil.which
        [git_bin, "rev-parse", "HEAD"],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    sha = (proc.stdout or "").strip()
    if proc.returncode != 0 or len(sha) < 7:
        raise AgenticError(
            "could not read worktree HEAD for acceptance manifest",
            details={"stderr": (proc.stderr or "")[:200]},
        )
    return sha


def _jail(worktree: Path, rel: str) -> Path:
    root = worktree.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise AgenticError(
            "manifest path escapes worktree",
            details={"path": rel},
        ) from exc
    return candidate


def build_manifest(
    worktree: Path,
    paths: Sequence[str],
    *,
    run_id: str,
    base_head: str,
) -> tuple[dict[str, object], str]:
    """Return (canonical payload, sha256 hex of canonical JSON)."""
    files: list[dict[str, str]] = []
    for rel in sorted({p.replace("\\", "/") for p in paths}):
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            raise AgenticError("refusing unsafe manifest path", details={"path": rel})
        target = _jail(worktree, rel)
        if not target.is_file():
            raise AgenticError("manifest path missing from worktree", details={"path": rel})
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        files.append({"path": rel, "sha256": digest})
    payload: dict[str, object] = {
        "base_head": base_head,
        "files": files,
        "run_id": run_id,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload, digest


def verify_manifest(
    worktree: Path,
    paths: Sequence[str],
    *,
    run_id: str,
    base_head: str,
    expected_digest: str,
) -> str:
    """Rebuild the digest. Raise ``AgenticError`` on any drift. Return digest."""
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise AgenticError(
            "run record is missing a valid acceptance_digest; refusing approve",
            details={"run_id": run_id},
        )
    live_head = git_head(worktree)
    if live_head != base_head:
        raise AgenticError(
            "worktree HEAD drifted from the accepted base; refusing approve",
            details={"run_id": run_id},
        )
    _payload, digest = build_manifest(
        worktree, paths, run_id=run_id, base_head=base_head,
    )
    if digest != expected_digest:
        raise AgenticError(
            "acceptance manifest digest mismatch; refusing approve",
            details={"run_id": run_id},
        )
    return digest
