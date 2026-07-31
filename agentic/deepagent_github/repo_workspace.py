"""Real-repo workspace surface for the Deep Agents GitHub harness.

Closes the gap the audit found: containment-by-copy already existed
(``harness_optimizer``'s fixture runner), but nothing in the codebase could
clone a REAL repository. This module clones, jails, and reads -- and, as of the
git-write addition below, can also make local commits inside that same jailed
clone. As of P10 it can also PUSH one ``claude/`` branch to origin
(:meth:`RepoWorkspaceTools.push_branch`) -- the single method here that reaches
the network, gated on the same ``allow_git_write_tools`` flag (default
``False``) as every other write, and passing no credential of its own. It still
opens no pull request and makes no GitHub API call; that is
``agentic/writer.py``'s gate. It is not wired into
``build_deepagent_github`` in this change: that wiring needs a
second tool-source parameter threaded through ``builder.py``/``tools.py``
(today's ``workspace_tools`` is hard-typed to a single ``ProposerWorkspaceTools``
instance), and there is no live caller yet to wire it FOR -- the planner/loop
driver that would consume a real-repo read-and-write surface doesn't exist
until a later phase. Shipping the capability now, fully tested and
independently usable, and deferring the wiring, mirrors how the injection
scanner and the deepagent-plan CLI probe were each shipped ahead of their
eventual consumers earlier in this same effort.

No optional dependency: unlike the rest of ``deepagent_github/``, this module
needs nothing beyond ``gh``/``git`` (already required for every other agentic
read, and for the local ``git clone`` machinery generally) and the stdlib. It
is fully testable without ``deepagents``/``langchain`` installed.

Containment for READS: :class:`agentic.fsconnect.pathsafe.ScopedRoots` -- the
strongest primitive in the repo (POSIX ``openat``/``O_NOFOLLOW``/held
``dir_fd``, zero TOCTOU window) -- jails every read to the exact directory the
clone populated. The clone itself runs entirely through ``agentic.gh_client``'s
existing chokepoint: same binary resolution, version floor, and
transient-retry policy every other read op gets, via the ``repo_clone`` op this
change adds there.

Containment for git WRITES: ``ScopedRoots`` is a read-only file-descriptor
primitive, not a subprocess sandbox, so it isn't the mechanism here. Each git
write method instead runs ``git`` as an argv-list subprocess with ``cwd``
pinned to the exact clone directory the ``repo_clone`` op populated, a scrubbed
environment allowlist, and a wall-clock timeout -- the same house pattern
``agentic/executor/runner.py`` and ``agentic/fsconnect/indexer.py::_run_reindex``
already established. Every write method is additionally gated on
``deepagent_github.allow_git_write_tools`` (default ``False``), a config field
of its own -- see the field's docstring in ``agentic/config.py`` for why this
is deliberately NOT a reuse of ``allow_shell_execution``.

``write_file`` is not a git operation at all -- it is a plain, path-validated
filesystem write (create or overwrite) inside the clone, the step that
actually puts candidate content on disk before ``add``/``commit`` stage and
record it. It shares the same ``allow_git_write_tools`` gate and the same
path-safety validation ``add`` uses, extended to tolerate a target that does
not exist yet (a new file), by walking up to the nearest existing ancestor
the same way ``harness_optimizer/mcp/tools.py``'s write-target check does.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess  # nosec B404 - argv-list only, no shell, fixed binary + fixed subcommands
import tempfile
import unicodedata
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import NoReturn

from agentic.config import AgenticConfig, DeepAgentGitHubConfig
from agentic.fsconnect.pathsafe import ScopedRoots
from agentic.gh_client import DEFAULT_CLONE_TIMEOUT_SEC, run_read
from utils.errors import AgenticError, AgenticWriteRefused, FsConnectError
from utils.logger import audit_log

# Matches harness_optimizer/mcp/tools.py's ProposerWorkspaceTools ceiling --
# same "one file at a time, bounded" read discipline, same number, so an
# operator sees one consistent per-file cap across both tool surfaces rather
# than two unexplained different limits.
DEFAULT_MAX_READ_BYTES = 256_000

# Same ceiling as reads, applied to write_file: no evidence either direction
# needs a different number, and one cap is one thing to explain to an operator.
DEFAULT_MAX_WRITE_BYTES = 256_000

# Local git plumbing only -- no network, so this is generous headroom, not a
# tuned ceiling like DEFAULT_CLONE_TIMEOUT_SEC.
DEFAULT_GIT_WRITE_TIMEOUT_SEC = 30

# The NTFS 8.3 short name for ".git" (the leading dot is dropped, and the index
# rises on collision). Windows resolves it to the same directory, which is the
# CVE-2019-1352/1353 class git itself guards with is_ntfs_dotgit().
_NTFS_DOTGIT_RE = re.compile(r"\.?git~[0-9]+\Z", re.IGNORECASE)


def _is_dotgit_name(part: str) -> bool:
    """True when a filesystem could resolve ``part`` to the ``.git`` directory.

    A literal ``part == ".git"`` compare is only correct on Linux. Every other
    platform CyClaw runs on has name-equivalence rules that make a DIFFERENT
    string open the SAME directory, and this jail must hold on all of them --
    `harness/` is explicitly a Windows/PowerShell operator surface:

    * Windows strips trailing dots and spaces from a path component, so
      ``.git.`` and ``.git `` both open ``.git``.
    * NTFS keeps 8.3 short names, so ``git~1`` opens ``.git``.
    * HFS+/APFS ignore a set of formatting codepoints when comparing names, so
      ``.gi<U+200C>t`` opens ``.git``. Every codepoint in git's own
      is_hfs_dotgit() list is Unicode category Cf, which is what is stripped
      here (a superset, also covering U+200B and the bidi overrides).
    * Windows and macOS are both case-insensitive by default, so ``.GIT`` opens
      ``.git`` -- handled by casefold rather than lower() so non-ASCII case
      folding is right too.

    Deliberately platform-INDEPENDENT: the same refusal applies everywhere
    rather than keying off sys.platform, because a jail that is weaker on the
    developer's machine than in production is a jail whose tests lie.
    """
    trimmed = part.rstrip(". ")
    cleaned = "".join(ch for ch in trimmed if unicodedata.category(ch) != "Cf")
    folded = cleaned.casefold()
    return folded == ".git" or _NTFS_DOTGIT_RE.fullmatch(folded) is not None

# push_branch is the one method here that reaches the network, so the
# local-only reasoning above does not apply to it. Sized like
# DEFAULT_CLONE_TIMEOUT_SEC rather than the local-plumbing budget.
DEFAULT_PUSH_TIMEOUT_SEC = 120

# CLAUDE.md Section 10's exact committer identity strings. Forced via `-c` on
# every commit invocation (scoped to that one subprocess, never written to the
# clone's or the operator's git config), so a commit made through this surface
# is always attributable to this layer, never to whatever `user.*` the clone
# happened to inherit (a fresh clone has none).
_COMMIT_EMAIL = "noreply@anthropic.com"
_COMMIT_NAME = "Claude"

# Mirrors CLAUDE.md's own branch convention ("short-lived claude/<topic>").
# Anchoring the required prefix is also the argv-injection defense: `git
# checkout -b <name>` would let a name starting with "-" be parsed as a `git`
# option instead of a branch name, and no string starting with "claude/" can
# start with "-".
BRANCH_NAME_RE = re.compile(r"^claude/[A-Za-z0-9][A-Za-z0-9._/-]{0,79}$")

# Same allowlist discipline as agentic/executor/runner.py's _scrubbed_env, kept
# as an independent (smaller) copy rather than an import: these four git
# subcommands are local-only and never touch the network, so this module has
# no need for that module's proxy/PIP_NO_INDEX handling, and importing across
# sibling capability modules before either needs the other would be exactly
# the premature coupling this effort has avoided everywhere else.
_GIT_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL")


def _git_env() -> dict[str, str]:
    return {name: os.environ[name] for name in _GIT_ENV_ALLOWLIST if name in os.environ}


def _resolve_git_binary() -> str:
    binary = shutil.which("git")
    if not binary:
        raise AgenticError("git binary not found on PATH", details={"looked_for": "git"})
    return binary


def _clear_readonly_and_retry(func: Callable[[str], object], path: str, exc: BaseException) -> None:
    """``rmtree`` error hook: clear a Windows read-only bit, then retry once.

    git writes loose objects and pack files read-only by design (they are meant
    to be immutable) -- on POSIX that never blocks a delete (only the parent
    directory's write permission matters there), but on Windows the read-only
    *attribute* itself blocks ``os.unlink``/``os.rmdir``, raising
    ``PermissionError``. This is the standard recovery for a git-populated tree
    on Windows: clear the attribute and retry the exact operation that failed.
    """
    del exc
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _rmtree_best_effort(path: str | Path) -> None:
    """Recursively delete ``path``, best-effort, git-read-only-aware.

    Replaces a bare ``ignore_errors=True``: that swallows a
    ``PermissionError`` from a git-created read-only file on Windows
    *silently*, leaving the clone on disk with no exception raised -- exactly
    the symptom the windows-latest CI leg caught once a real (not mocked) git
    clone was exercised for the first time. ``onexc`` runs
    :func:`_clear_readonly_and_retry` on each failure instead, and the outer
    ``suppress`` preserves the original "never raise, this is cleanup" contract
    for whatever a retry still can't remove (e.g. a genuinely locked file).
    """
    with suppress(OSError):
        shutil.rmtree(path, onexc=_clear_readonly_and_retry)


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
        _rmtree_best_effort(tmp)
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
    max_write_bytes: int = DEFAULT_MAX_WRITE_BYTES
    allow_git_write_tools: bool = False

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
            _rmtree_best_effort(dest.parent)
            raise AgenticError(
                "failed to jail the cloned repository",
                details={"repo": agentic_cfg.repo, "error": str(exc)},
            ) from exc
        return cls(
            _scoped=scoped,
            _dest=dest,
            config_path=config_path,
            cfg=cfg,
            allow_git_write_tools=deep_cfg.allow_git_write_tools,
        )

    @classmethod
    def attach(
        cls,
        agentic_cfg: AgenticConfig,
        dest: Path,
        *,
        config_path: str = "config.yaml",
        cfg: dict | None = None,
    ) -> RepoWorkspaceTools:
        """Re-open an EXISTING clone directory a prior ``clone()`` call populated.

        Does not clone anything new. A CLI-subprocess-per-call model (the only
        way ``harness/server.py`` may reach ``agentic/`` at all -- see I6) can't
        hold a live ``RepoWorkspaceTools`` object in memory between a "start a
        run" call and a later "decide" call; the clone directory's path,
        persisted on disk by whatever started the run, is the only thing that
        survives between them. This is how a later call reattaches to it.

        ``dest`` must resolve under ``agentic_cfg.deepagent_github.workspace_root``
        -- defense in depth against a corrupted/tampered persisted path, even
        though in practice this module is always the one that wrote it.
        """
        deep_cfg = agentic_cfg.deepagent_github
        workspace_root = Path(deep_cfg.workspace_root).resolve()
        dest_resolved = dest.resolve()
        if dest_resolved != workspace_root and workspace_root not in dest_resolved.parents:
            raise AgenticError(
                "attach target is outside the configured workspace root",
                details={"dest": str(dest), "workspace_root": str(workspace_root)},
            )
        if not dest_resolved.is_dir():
            raise AgenticError("cannot attach: clone directory does not exist", details={"dest": str(dest)})
        try:
            scoped = ScopedRoots([str(dest_resolved)])
        except Exception as exc:
            raise AgenticError(
                "failed to jail the existing cloned repository",
                details={"dest": str(dest), "error": str(exc)},
            ) from exc
        return cls(
            _scoped=scoped,
            _dest=dest_resolved,
            config_path=config_path,
            cfg=cfg,
            allow_git_write_tools=deep_cfg.allow_git_write_tools,
        )

    def _audit(self, event: str, **fields: object) -> None:
        audit_log({"event": event, **fields}, config_path=self.config_path, cfg=self.cfg)

    def _deny_git(self, tool: str, message: str, **extra: object) -> NoReturn:
        self._audit("agentic_repo_workspace_denied", op=tool, reason=message, **extra)
        raise AgenticError(message, details={"tool": tool, **extra})

    def _require_git_writes_enabled(self, tool: str) -> None:
        if not self.allow_git_write_tools:
            self._audit("agentic_repo_workspace_denied", op=tool, reason="git write tools disabled")
            raise AgenticWriteRefused(
                "git write operations are disabled (deepagent_github.allow_git_write_tools is False)",
                details={"tool": tool},
            )

    def _validate_write_path(self, tool: str, target: str, *, must_exist: bool = True) -> str:
        """Validate one repo-relative path: relative, safe, and inside the clone.

        Mirrors ``harness_optimizer/runners/github_coding_runner.py::_safe_child``'s
        cross-platform reasoning (a driveless-but-rooted Windows path like
        ``\\Windows\\system.ini`` must be rejected on POSIX too, and vice
        versa), plus a leading-dash rejection so a path can never be parsed as
        a ``git`` option instead of a pathspec.

        ``must_exist=True`` (``add``'s case) resolves the target directly, since
        staging a nonexistent path is already a user error. ``must_exist=False``
        (``write_file``'s case, which may create a brand-new file) instead walks
        up to the nearest ancestor that DOES exist and resolves that one --
        mirroring ``harness_optimizer/mcp/tools.py::_contains_write_target``'s
        reasoning: a not-yet-existing leaf can't itself be a symlink, but an
        existing ancestor directory on the way there still could be, so that is
        the one that must be checked.
        """
        if not isinstance(target, str) or not target or "\x00" in target:
            self._deny_git(tool, "git path must be a non-empty string", target=target)
        normalized = target.replace("\\", "/")
        if normalized.startswith(("/", "-")) or PureWindowsPath(target).is_absolute():
            self._deny_git(tool, "git path must be a safe relative path", target=target)
        parts = tuple(part for part in normalized.split("/") if part not in {"", "."})
        if not parts or any(part == ".." or ":" in part for part in parts):
            self._deny_git(tool, "git path escaped the clone root", target=target)
        # The clone's own git metadata is never a legitimate write target, and
        # writing it is not merely out of scope -- it is arbitrary command
        # execution. A model-authored `.git/config` carrying a filter definition
        # plus a `.gitattributes` assigning that filter runs its command during
        # the plain `git add` that finalize_real_repo_change performs, in THIS
        # process, at human-approval time, with the operator's own PATH and HOME
        # (the executor's scrubbed env does not apply to _run_git). `git diff`
        # cannot render `.git/config`, so no diff-based human review can catch
        # it. The resolve() check below does not help: `.git/config` legitimately
        # resolves to a path inside the clone.
        #
        # Matched on EVERY segment, not just the first (a nested `submodule/.git`
        # is a gitdir too), and through _is_dotgit_name so that the filesystem's
        # own name-equivalence rules cannot smuggle one past a literal compare.
        # `.gitattributes`/`.gitignore` are unaffected -- whole segments are
        # compared, never prefixes.
        if any(_is_dotgit_name(part) for part in parts):
            self._deny_git(tool, "git path may not touch the clone's .git directory", target=target)
        dest_resolved = self._dest.resolve()
        candidate = dest_resolved.joinpath(*parts)
        if must_exist:
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                self._deny_git(
                    tool, "git path does not exist in the clone", target=target, error_type=type(exc).__name__,
                )
        else:
            ancestor = candidate
            while True:
                try:
                    resolved = ancestor.resolve(strict=True)
                    break
                except OSError:
                    if ancestor.parent == ancestor:  # pragma: no cover - dest_resolved always exists first
                        self._deny_git(tool, "git path escaped the clone root", target=target)
                    ancestor = ancestor.parent
        if resolved != dest_resolved and dest_resolved not in resolved.parents:
            self._deny_git(tool, "git path escaped the clone root", target=target)
        # The segment check above inspects the REQUESTED name; this inspects
        # where the filesystem actually lands. They are not the same question,
        # and the gap between them was a complete bypass: a repository can
        # legitimately contain a symlink named anything at all pointing at
        # `.git` (git refuses to check out a path NAMED `.git`, but not one
        # POINTING at it), so `write_file("docs/config", ...)` with `docs` ->
        # `.git` resolved cleanly inside the clone, passed every check, and
        # wrote `.git/config` -- verified by execution. The ancestor walk above
        # was built to catch symlinks escaping OUTSIDE the clone, so a symlink
        # to a directory INSIDE it never tripped it.
        git_dir = (dest_resolved / ".git").resolve()
        if resolved == git_dir or git_dir in resolved.parents:
            self._deny_git(tool, "git path may not touch the clone's .git directory", target=target)
        return "/".join(parts)

    def _run_git(
        self,
        tool: str,
        argv: list[str],
        *,
        timeout_sec: int = DEFAULT_GIT_WRITE_TIMEOUT_SEC,
        extra_env: dict[str, str] | None = None,
    ) -> str:
        binary = _resolve_git_binary()
        env = _git_env()
        if extra_env:
            # Additive, and only from this module's own fixed literals -- never
            # from caller data. push_branch uses it for GIT_TERMINAL_PROMPT=0;
            # nothing here widens _GIT_ENV_ALLOWLIST itself.
            env.update(extra_env)
        try:
            completed = subprocess.run(  # noqa: S603 -- argv list, no shell, fixed binary
                [binary, *argv],
                cwd=str(self._dest),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self._audit("agentic_repo_workspace_git_failed", op=tool, reason="timeout")
            raise AgenticError(f"git {tool} timed out after {timeout_sec}s", details={"tool": tool}) from exc
        if completed.returncode != 0:
            self._audit("agentic_repo_workspace_git_failed", op=tool, exit_code=completed.returncode)
            raise AgenticError(
                f"git {tool} failed with exit code {completed.returncode}",
                details={"tool": tool, "exit_code": completed.returncode, "stderr": completed.stderr[-2000:]},
            )
        self._audit("agentic_repo_workspace_git_ok", op=tool, exit_code=completed.returncode)
        return completed.stdout

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

    @property
    def worktree(self) -> Path:
        """The real filesystem path of the jailed clone.

        The read/write tool methods above are the intended boundary for
        reading or mutating individual files; this exists only for a caller
        that needs an actual ``cwd`` to hand a subprocess it runs itself --
        namely ``agentic.executor.run_verification``, which pins its checks'
        working directory to a worktree it does not otherwise have a way to
        obtain from this class.
        """
        return self._dest

    # --- git writes: scoped to this clone; only push_branch leaves the box ---

    def checkout_branch(self, name: str) -> dict:
        """Create and switch to a new branch. ``name`` must be ``claude/<topic>``."""
        tool = "checkout_branch"
        self._require_git_writes_enabled(tool)
        if not isinstance(name, str) or not BRANCH_NAME_RE.match(name):
            self._deny_git(
                tool,
                "branch name must start with 'claude/' and use only [A-Za-z0-9._/-]",
                target=name,
            )
        self._run_git(tool, ["checkout", "-b", name])
        self._audit("agentic_repo_workspace_git_op", op=tool, branch=name)
        return {"branch": name}

    def write_file(self, target: str, content: str) -> dict:
        """Write (create or overwrite) one text file's full content in the clone.

        Unlike ``add``, ``target`` need not already exist -- a candidate patch
        may introduce a brand-new file. This is a plain filesystem write, not a
        git operation, but it shares ``add``/``commit``'s gate: it is the step
        that puts model-authored bytes on disk inside the clone, so it is
        exactly the risk ``allow_git_write_tools`` exists to gate.
        """
        tool = "write_file"
        self._require_git_writes_enabled(tool)
        if not isinstance(content, str):
            self._deny_git(tool, "write content must be a string", target=target)
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_write_bytes:
            self._deny_git(tool, "write content exceeds max_write_bytes", target=target, bytes=len(encoded))
        relative = self._validate_write_path(tool, target, must_exist=False)
        path = self._dest / relative
        # Path validation proves the target is INSIDE the clone; it does not
        # prove the filesystem will accept a write there. Two planner-reachable
        # shapes pass validation and then raise out of the stdlib: a target
        # whose parent is an existing FILE ("README.md/x.txt" -- the ancestor
        # walk stops at README.md, which resolves inside the clone) makes mkdir
        # raise FileExistsError, and a target that IS an existing directory
        # makes write_text raise IsADirectoryError. Neither is an AgenticError,
        # so both used to escape run_real_repo_loop's `except AgenticError`
        # entirely -- crashing the run, leaking the clone, and persisting no run
        # record, all from model output alone. Converting here (rather than
        # widening the loop's handler) keeps the contract _parse_file_blocks'
        # docstring already states: a malformed path is a rejected iteration,
        # not a crash.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            self._deny_git(tool, "git path is not writable in the clone", target=target,
                           error_type=type(exc).__name__)
        self._audit(
            "agentic_repo_workspace_write",
            target=relative,
            bytes=len(encoded),
            sha256=hashlib.sha256(encoded).hexdigest(),
        )
        return {"target": relative, "bytes": len(encoded)}

    def add(self, paths: Sequence[str]) -> dict:
        """Stage one or more existing paths in the clone (``git add --``)."""
        tool = "add"
        self._require_git_writes_enabled(tool)
        if not paths:
            self._deny_git(tool, "git add requires at least one path")
        validated = [self._validate_write_path(tool, path) for path in paths]
        self._run_git(tool, ["add", "--", *validated])
        self._audit("agentic_repo_workspace_git_op", op=tool, paths=validated)
        return {"paths": validated}

    def commit(self, message: str) -> dict:
        """Commit staged changes, identity forced to CyClaw's committer convention.

        ``-c user.email=...``/``-c user.name=...`` scope the identity override to
        this one subprocess invocation -- never written to the clone's
        ``.git/config`` or any global git config -- and set both author and
        committer, since a fresh clone carries no ``user.*`` of its own.

        ``--no-verify`` is defense in depth, not the primary control: the clone's
        ``.git/`` is unwritable through ``write_file`` (see
        ``_validate_write_path``), so no model-authored hook should exist to run.
        This makes a hook that arrived some other way -- a future code path, or a
        remote that shipped one -- fail to execute at commit time as well.
        """
        tool = "commit"
        self._require_git_writes_enabled(tool)
        if not isinstance(message, str) or not message.strip():
            self._deny_git(tool, "commit message must be a non-empty string")
        self._run_git(
            tool,
            [
                "-c", f"user.email={_COMMIT_EMAIL}",
                "-c", f"user.name={_COMMIT_NAME}",
                "commit", "--no-verify", "-m", message,
            ],
        )
        # Never audit the raw message -- same "hash, don't persist" discipline
        # utils/logger.py applies to query text. A local hexdigest avoids
        # importing utils.logger.hash_query (a query-shaped helper) for a
        # plain string.
        message_sha256 = hashlib.sha256(message.encode("utf-8")).hexdigest()
        self._audit("agentic_repo_workspace_git_op", op=tool, message_sha256=message_sha256)
        return {}

    def push_branch(self, name: str) -> dict:
        """Push one ``claude/`` branch to origin. The only network write here.

        Separate from the local git methods above in three ways that matter:

        * Its own timeout. ``DEFAULT_GIT_WRITE_TIMEOUT_SEC``'s comment justifies
          30s on "local git plumbing only -- no network"; that reasoning does
          not survive a push, so this uses the clone-sized budget instead.
        * ``GIT_TERMINAL_PROMPT=0``. Without it a missing credential makes git
          block on an interactive username prompt until the timeout, turning a
          clean auth failure into a hang.
        * A re-validated branch name, and ``--`` before it, so the value can
          never be reparsed as an option or a refspec with extra parts.

        CREDENTIALS: this passes none. ``_GIT_ENV_ALLOWLIST`` deliberately does
        not carry ``GH_TOKEN``/``GITHUB_TOKEN``, and adding one here would put a
        GitHub credential into an environment that ``agentic.executor`` runs
        model-proposed check commands under -- a credential-exfiltration path in
        exchange for convenience. A push therefore authenticates only via the
        operator's own HOME-resident git credential helper (what ``gh auth
        setup-git`` configures), or it fails. A token-only environment with no
        helper is the expected first failure on a fresh machine, and that is the
        documented, intended behavior rather than a bug to patch around.
        """
        tool = "push_branch"
        self._require_git_writes_enabled(tool)
        if not isinstance(name, str) or not BRANCH_NAME_RE.match(name):
            self._deny_git(
                tool,
                "push branch must start with 'claude/' and use only [A-Za-z0-9._/-]",
                target=name,
            )
        self._run_git(
            tool,
            ["push", "--set-upstream", "origin", "--", name],
            timeout_sec=DEFAULT_PUSH_TIMEOUT_SEC,
            extra_env={"GIT_TERMINAL_PROMPT": "0"},
        )
        self._audit("agentic_repo_workspace_git_op", op=tool, branch=name)
        return {"pushed": name}

    def diff(self, *, cached: bool = False) -> str:
        """Return the worktree (or, with ``cached=True``, staged) diff as text.

        ``git diff`` shows nothing for a brand-new file that has never been
        staged -- it isn't in the index or the committed tree to diff
        against, so a candidate that only CREATES files renders an empty
        string here despite having genuinely changed something. Callers
        reviewing a candidate before approval (``agentic.cli``'s
        ``real-repo-run-status``) must pair this with :meth:`untracked_files`
        to see new-file content, or an empty diff can be misread as "nothing
        to review" when a new file is in fact about to be committed unseen.
        """
        tool = "diff"
        self._require_git_writes_enabled(tool)
        argv = ["diff", "--cached"] if cached else ["diff"]
        output = self._run_git(tool, argv)
        self._audit("agentic_repo_workspace_git_op", op=tool, cached=cached, bytes=len(output))
        return output

    def untracked_files(self) -> list[str]:
        """List paths in the clone that git does not yet track.

        Read-only: ``git status`` mutates nothing (unlike ``git add -N``,
        which would also make an untracked path visible to ``git diff`` but
        by writing a placeholder entry into the index -- a real mutation this
        method deliberately avoids so it stays safe to call from a pure
        status check).
        """
        tool = "status"
        self._require_git_writes_enabled(tool)
        output = self._run_git(tool, ["status", "--porcelain=v1", "--untracked-files=all"])
        paths = []
        for line in output.splitlines():
            if line.startswith("??"):
                paths.append(line[3:].strip())
        self._audit("agentic_repo_workspace_git_op", op=tool, count=len(paths))
        return paths

    def close(self) -> None:
        """Release the held directory fd and delete the clone from disk.

        Idempotent-ish: closing twice is harmless (ScopedRoots.close() and a
        missing directory are both tolerated), but this is not itself
        thread-safe -- callers own their own instance's lifecycle.
        """
        self._scoped.close()
        # ScopedRoots holds a dir_fd on `_dest`, not a lock on its parent, so
        # removing the parent (which also removes `_dest`) after close() is
        # safe -- _rmtree_best_effort handles both the case where the clone
        # step itself failed partway and left nothing (or a caller already
        # cleaned up) and the case where git left read-only objects behind.
        _rmtree_best_effort(self._dest.parent)

    def __enter__(self) -> RepoWorkspaceTools:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = [
    "BRANCH_NAME_RE",
    "DEFAULT_GIT_WRITE_TIMEOUT_SEC",
    "DEFAULT_MAX_READ_BYTES",
    "DEFAULT_MAX_WRITE_BYTES",
    "DEFAULT_PUSH_TIMEOUT_SEC",
    "RepoWorkspaceTools",
]
