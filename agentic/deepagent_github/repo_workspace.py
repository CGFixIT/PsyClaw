"""Read-only real-repo workspace surface for the Deep Agents GitHub harness.

Closes the gap the audit found: containment-by-copy already existed
(``harness_optimizer``'s fixture runner), but nothing in the codebase could
clone a REAL repository. This module does exactly that, and nothing more --
clone, jail, read. It does not write, does not touch GitHub state, and is not
wired into ``build_deepagent_github`` in this change: that wiring needs a
second tool-source parameter threaded through ``builder.py``/``tools.py``
(today's ``workspace_tools`` is hard-typed to a single ``ProposerWorkspaceTools``
instance), and there is no live caller yet to wire it FOR -- the planner/loop
driver that would consume a real-repo read surface doesn't exist until a later
phase. Shipping the capability now, fully tested and independently usable, and
deferring the wiring, mirrors how the injection scanner and the deepagent-plan
CLI probe were each shipped ahead of their eventual consumers earlier in this
same effort.

No optional dependency: unlike the rest of ``deepagent_github/``, this module
needs nothing beyond ``gh`` (already required for every other agentic read) and
the stdlib. It is fully testable without ``deepagents``/``langchain`` installed.

Containment: :class:`agentic.fsconnect.pathsafe.ScopedRoots` -- the strongest
primitive in the repo (POSIX ``openat``/``O_NOFOLLOW``/held ``dir_fd``, zero
TOCTOU window) -- jails every read to the exact directory the clone populated.
The clone itself runs entirely through ``agentic.gh_client``'s existing
chokepoint: same binary resolution, version floor, and transient-retry policy
every other read op gets, via the ``repo_clone`` op this change adds there.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agentic.config import AgenticConfig, DeepAgentGitHubConfig
from agentic.fsconnect.pathsafe import ScopedRoots
from agentic.gh_client import DEFAULT_CLONE_TIMEOUT_SEC, run_read
from utils.errors import AgenticError, FsConnectError
from utils.logger import audit_log

# Matches harness_optimizer/mcp/tools.py's ProposerWorkspaceTools ceiling --
# same "one file at a time, bounded" read discipline, same number, so an
# operator sees one consistent per-file cap across both tool surfaces rather
# than two unexplained different limits.
DEFAULT_MAX_READ_BYTES = 256_000


def _clone(cfg: AgenticConfig, deep_cfg: DeepAgentGitHubConfig, *, config_path: str, app_cfg: dict | None) -> Path:
    """Clone ``cfg.repo`` into a fresh directory under the workspace root.

    Returns the populated clone directory. The ``TemporaryDirectory`` is
    intentionally created INSIDE ``deep_cfg.workspace_root`` (which
    ``agentic.config._resolve_data_path`` already forces under the repo's own
    ``data/`` tree) rather than the OS default temp location, so every
    filesystem side effect this module can have stays under one
    already-validated, already-gitignored root.
    """
    root = Path(deep_cfg.workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    # On SUCCESS, ownership of this directory passes to RepoWorkspaceTools.close();
    # not cleaning it up here is deliberate in that path. On FAILURE it is this
    # function's own responsibility -- nothing downstream ever receives a
    # reference to `tmp` to clean up later, so leaving it here would leak one
    # empty stray directory under workspace_root per failed clone attempt.
    tmp = tempfile.mkdtemp(dir=str(root), prefix="cyclaw-repo-clone-")
    # git/gh refuse to clone into a non-empty directory, so the destination must
    # not exist yet -- a subdirectory of the fresh tmpdir, never the tmpdir
    # itself (which already exists, empty, from mkdtemp above).
    dest = Path(tmp) / "repo"
    try:
        run_read(
            "repo_clone",
            cfg.repo,
            min_version=cfg.gh_min_tuple,
            timeout=DEFAULT_CLONE_TIMEOUT_SEC,
            retries=cfg.gh_retries,
            dest=str(dest),
        )
    except AgenticError:
        shutil.rmtree(tmp, ignore_errors=True)
        audit_log({"event": "agentic_repo_workspace_clone_failed", "repo": cfg.repo},
                   config_path=config_path, cfg=app_cfg)
        raise
    audit_log({"event": "agentic_repo_workspace_cloned", "repo": cfg.repo, "dest": str(dest)},
              config_path=config_path, cfg=app_cfg)
    return dest


@dataclass
class RepoWorkspaceTools:
    """A read-only, jailed view over one freshly-cloned repository.

    Construct via :meth:`clone`, not the constructor directly -- that is what
    actually performs and audits the clone before wrapping it in containment.
    Use as a context manager (or call :meth:`close`) so the held directory fd
    AND the temporary clone directory are both released deterministically.
    """

    _scoped: ScopedRoots
    _dest: Path
    config_path: str = "config.yaml"
    cfg: dict | None = None
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES

    @classmethod
    def clone(
        cls,
        agentic_cfg: AgenticConfig,
        *,
        config_path: str = "config.yaml",
        cfg: dict | None = None,
    ) -> RepoWorkspaceTools:
        """Clone ``agentic_cfg.repo`` and return a jailed read surface over it.

        Raises ``AgenticError`` on any failure (gh missing/too old, clone
        failed, network error) -- the same exception vocabulary every other
        ``agentic/`` read path uses, so a caller never needs to know this
        module reaches into ``agentic.fsconnect.pathsafe`` internally.
        """
        deep_cfg = agentic_cfg.deepagent_github
        dest = _clone(agentic_cfg, deep_cfg, config_path=config_path, app_cfg=cfg)
        try:
            scoped = ScopedRoots([str(dest)])
        except Exception as exc:
            # Broad on purpose, mirroring ScopedRoots.__init__'s own posture
            # toward its partially-opened roots: the clone itself succeeded --
            # only jailing it failed -- and nothing else holds a reference to
            # `dest` at this point, so ANY failure here (a documented
            # FsPathError, or a raw OSError from the underlying os.open) must
            # still clean it up, or a successful-but-unjailable clone leaks on
            # disk under workspace_root forever.
            shutil.rmtree(dest.parent, ignore_errors=True)
            raise AgenticError(
                "failed to jail the cloned repository",
                details={"repo": agentic_cfg.repo, "error": str(exc)},
            ) from exc
        return cls(_scoped=scoped, _dest=dest, config_path=config_path, cfg=cfg)

    def _audit(self, event: str, **fields: object) -> None:
        audit_log({"event": event, **fields}, config_path=self.config_path, cfg=self.cfg)

    def read_file(self, target: str) -> str:
        """Read one text file from the clone, decoded as UTF-8."""
        try:
            data = self._scoped.read_bytes(target, max_bytes=self.max_read_bytes)
        except FsConnectError as exc:
            self._audit("agentic_repo_workspace_denied", op="read_file", target=target, reason=str(exc))
            raise AgenticError(
                f"cannot read {target!r} from the cloned repository",
                details={"target": target, "error": str(exc)},
            ) from exc
        self._audit("agentic_repo_workspace_read", op="read_file", target=target)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AgenticError(f"{target!r} is not valid UTF-8 text", details={"target": target}) from exc

    def list_dir(self, target: str = ".") -> list[dict]:
        """List one directory of the clone (non-recursive)."""
        try:
            entries = self._scoped.list_dir(target)
        except FsConnectError as exc:
            self._audit("agentic_repo_workspace_denied", op="list_dir", target=target, reason=str(exc))
            raise AgenticError(
                f"cannot list {target!r} in the cloned repository",
                details={"target": target, "error": str(exc)},
            ) from exc
        self._audit("agentic_repo_workspace_read", op="list_dir", target=target)
        return entries

    def stat_file(self, target: str) -> dict:
        """Stat one path in the clone without reading its content."""
        try:
            info = self._scoped.stat(target)
        except FsConnectError as exc:
            self._audit("agentic_repo_workspace_denied", op="stat_file", target=target, reason=str(exc))
            raise AgenticError(
                f"cannot stat {target!r} in the cloned repository",
                details={"target": target, "error": str(exc)},
            ) from exc
        self._audit("agentic_repo_workspace_read", op="stat_file", target=target)
        return info

    def close(self) -> None:
        """Release the held directory fd and delete the clone from disk.

        Idempotent-ish: closing twice is harmless (ScopedRoots.close() and a
        missing directory are both tolerated), but this is not itself
        thread-safe -- callers own their own instance's lifecycle.
        """
        self._scoped.close()
        # ScopedRoots holds a dir_fd on `_dest`, not a lock on its parent, so
        # removing the parent (which also removes `_dest`) after close() is
        # safe -- ignore_errors handles the case where the clone step itself
        # failed partway and left nothing, or a caller already cleaned up.
        shutil.rmtree(self._dest.parent, ignore_errors=True)

    def __enter__(self) -> RepoWorkspaceTools:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = ["DEFAULT_MAX_READ_BYTES", "RepoWorkspaceTools"]
