"""Tests for agentic.deepagent_github.repo_workspace -- the real-repo read surface.

``run_read`` (the gh_client chokepoint) is mocked throughout, so no gh binary,
subprocess, or network is involved. The mock's side effect populates the
destination directory exactly as a real ``gh repo clone`` would -- writing files
into ``dest`` before returning -- so ``RepoWorkspaceTools`` exercises its real
containment path (``ScopedRoots`` over an actually-populated directory), not a
faked-out double.

No optional dependency is needed: this module imports nothing from
``deepagents``/``langchain``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agentic import deepagent_github
from agentic.config import AgenticConfig
from agentic.deepagent_github import repo_workspace
from agentic.deepagent_github.repo_workspace import DEFAULT_MAX_READ_BYTES, RepoWorkspaceTools
from utils.errors import AgenticError, AgenticWriteRefused


def _cfg(tmp_path: Path, monkeypatch, **overrides) -> AgenticConfig:
    # agentic.config._resolve_data_path forces workspace_root to resolve inside
    # <repo_root>/data/ -- real, deliberate containment (config.py:95-113), not
    # a test-only rule. Rather than write test clones into the actual repo's
    # data/ directory, repoint what "the repo root" means for this construction
    # only, so workspace_root safely resolves under tmp_path/data instead.
    from agentic import config as agentic_config_module

    monkeypatch.setattr(agentic_config_module, "_repo_root", lambda: tmp_path)
    kwargs: dict = {
        "repo": "owner/repo",
        "mode": "read",
        "deepagent_github": {"workspace_root": str(tmp_path / "data" / "workspaces")},
    }
    kwargs.update(overrides)
    return AgenticConfig(**kwargs)


def _fake_clone_populating(*, files: dict[str, str]):
    """A run_read stub that populates `dest` with `files` before returning.

    Mirrors what a real `gh repo clone` does: create the destination directory
    and fill it with a working tree. Refuses to run twice against the same
    dest (git/gh would refuse a non-empty destination too).
    """

    def fake(op, repo, **kwargs):
        assert op == "repo_clone"
        dest = Path(kwargs["dest"])
        assert not dest.exists(), "dest must not pre-exist, exactly like a real clone target"
        dest.mkdir(parents=True)
        for rel, content in files.items():
            path = dest / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            # newline="" writes `content` byte-for-byte: without it, Path.write_text's
            # universal-newlines default silently turns \n into \r\n on Windows, so a
            # fixture declared as "print(1)\n" lands on disk as "print(1)\r\n" -- then
            # read_file() (which reads real bytes, no text-mode translation, matching
            # what a real git clone would hand back) correctly returns THAT, and an
            # assertion comparing it to the original "\n" string fails. Caught by the
            # windows-latest CI job; a real clone's line endings depend on the
            # repository's own content, not the OS running the clone.
            path.write_text(content, encoding="utf-8", newline="")
        return {"op": op, "repo": repo, "dest": str(dest)}

    return fake


@pytest.fixture(autouse=True)
def _temp_audit(tmp_path, monkeypatch):
    import yaml

    from utils.logger import _get_config, reset_config_cache

    cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
           "policy": {"privacy": {}}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    reset_config_cache()
    _get_config(str(path))
    yield
    reset_config_cache()


# --- clone + containment ----------------------------------------------------


def test_clone_creates_a_readable_jailed_workspace(tmp_path, monkeypatch):
    fake = _fake_clone_populating(files={"README.md": "hello", "src/app.py": "print(1)\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            assert tools.read_file("README.md") == "hello"
            assert tools.read_file("src/app.py") == "print(1)\n"
            names = {e["name"] for e in tools.list_dir(".")}
            assert names == {"README.md", "src"}
            info = tools.stat_file("README.md")
            assert info["type"] == "file"
            assert info["size"] == len("hello")


def test_workspace_root_is_created_if_missing(tmp_path, monkeypatch):
    workspace_root = tmp_path / "data" / "does" / "not" / "exist" / "yet"
    assert not workspace_root.exists()
    fake = _fake_clone_populating(files={"a.txt": "x"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch, deepagent_github={"workspace_root": str(workspace_root)})):
            assert workspace_root.is_dir()


def test_clone_lands_inside_the_configured_workspace_root(tmp_path, monkeypatch):
    """Reconciles the two P5 requirements: TemporaryDirectory, but under workspace_root."""
    workspace_root = tmp_path / "data" / "workspaces"
    fake = _fake_clone_populating(files={"a.txt": "x"})
    with patch.object(repo_workspace, "run_read", side_effect=fake) as mrun:
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch, deepagent_github={"workspace_root": str(workspace_root)})):
            dest = Path(mrun.call_args.kwargs["dest"])
            assert workspace_root.resolve() in dest.resolve().parents


def test_read_file_cannot_escape_the_clone_root(tmp_path, monkeypatch):
    fake = _fake_clone_populating(files={"a.txt": "inside"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError):
                tools.read_file("../escape.txt")
            with pytest.raises(AgenticError):
                tools.read_file("/etc/passwd")


def test_read_file_rejects_a_symlink_escape(tmp_path, monkeypatch):
    """The whole point of ScopedRoots over a plain path-join guard: O_NOFOLLOW."""
    fake = _fake_clone_populating(files={"a.txt": "inside"})

    def fake_with_symlink(op, repo, **kwargs):
        result = fake(op, repo, **kwargs)
        dest = Path(result["dest"])
        outside = dest.parent / "outside-secret.txt"
        outside.write_text("do not read me", encoding="utf-8")
        (dest / "link").symlink_to(outside)
        return result

    with patch.object(repo_workspace, "run_read", side_effect=fake_with_symlink):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError):
                tools.read_file("link")


def test_read_file_enforces_the_size_ceiling(tmp_path, monkeypatch):
    fake = _fake_clone_populating(files={"big.txt": "x" * (DEFAULT_MAX_READ_BYTES + 1)})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError):
                tools.read_file("big.txt")


def test_read_file_rejects_non_utf8_content(tmp_path, monkeypatch):
    def fake(op, repo, **kwargs):
        dest = Path(kwargs["dest"])
        dest.mkdir(parents=True)
        (dest / "bin.dat").write_bytes(b"\xff\xfe\x00\x01")
        return {"op": op, "repo": repo, "dest": str(dest)}

    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError):
                tools.read_file("bin.dat")


def test_list_dir_missing_path_raises(tmp_path, monkeypatch):
    fake = _fake_clone_populating(files={"a.txt": "x"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError):
                tools.list_dir("nonexistent-dir")


def test_stat_file_missing_path_raises_and_is_audited(tmp_path, monkeypatch):
    fake = _fake_clone_populating(files={"a.txt": "x"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            with patch.object(repo_workspace, "audit_log") as maudit:
                with pytest.raises(AgenticError):
                    tools.stat_file("nonexistent.txt")
    events = [c.args[0]["event"] for c in maudit.call_args_list]
    assert "agentic_repo_workspace_denied" in events


# --- failure paths -----------------------------------------------------------


def test_clone_failure_raises_agentic_error_and_leaves_no_directory(tmp_path, monkeypatch):
    def failing(op, repo, **kwargs):
        raise AgenticError("gh repo_clone failed with exit code 1", details={"op": op, "repo": repo})

    workspace_root = tmp_path / "data" / "workspaces"
    with patch.object(repo_workspace, "run_read", side_effect=failing):
        with pytest.raises(AgenticError):
            RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch, deepagent_github={"workspace_root": str(workspace_root)}))
    # workspace_root itself is created (mkdir happens before the clone attempt),
    # but it must be empty -- no half-populated clone directory left behind.
    assert list(workspace_root.iterdir()) == []


def test_jail_failure_after_a_successful_clone_leaves_no_directory(tmp_path, monkeypatch):
    """A clone that succeeds but cannot be jailed must not leak on disk either.

    Regression guard: the first implementation cleaned up a FAILED clone but
    not a clone that succeeded and then failed to be jailed by ScopedRoots.
    """
    workspace_root = tmp_path / "data" / "workspaces"
    fake = _fake_clone_populating(files={"a.txt": "x"})
    with patch.object(repo_workspace, "run_read", side_effect=fake), \
         patch.object(repo_workspace, "ScopedRoots", side_effect=OSError("boom")):
        with pytest.raises(AgenticError):
            RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch, deepagent_github={"workspace_root": str(workspace_root)}))
    assert list(workspace_root.iterdir()) == []


# --- lifecycle / cleanup -----------------------------------------------------


def test_close_removes_the_clone_from_disk(tmp_path, monkeypatch):
    fake = _fake_clone_populating(files={"a.txt": "x"})
    with patch.object(repo_workspace, "run_read", side_effect=fake) as mrun:
        tools = RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch))
        dest = Path(mrun.call_args.kwargs["dest"])
        assert dest.is_dir()
        tools.close()
    assert not dest.exists()
    assert not dest.parent.exists()


def test_close_is_safe_to_call_twice(tmp_path, monkeypatch):
    fake = _fake_clone_populating(files={"a.txt": "x"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        tools = RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch))
        tools.close()
        tools.close()  # must not raise


def test_context_manager_cleans_up_on_exception(tmp_path, monkeypatch):
    # Plain try/except, not pytest.raises, around the with-block whose body
    # unconditionally raises: CodeQL can prove a bare `raise` always raises,
    # but doesn't model pytest.raises.__exit__ as suppressing it, so it flagged
    # the two lines after the pytest.raises block as unreachable (false
    # positive -- the test passes; pytest.raises does suppress a matching
    # exception). A standard try/except is unambiguous to any static analyzer.
    fake = _fake_clone_populating(files={"a.txt": "x"})
    with patch.object(repo_workspace, "run_read", side_effect=fake) as mrun:
        try:
            with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)):
                raise ValueError("boom")
        except ValueError:
            pass
        dest = Path(mrun.call_args.kwargs["dest"])
    assert not dest.exists()


# --- audit -------------------------------------------------------------------


def test_reads_are_audited(tmp_path, monkeypatch):
    fake = _fake_clone_populating(files={"a.txt": "x"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            with patch.object(repo_workspace, "audit_log") as maudit:
                tools.read_file("a.txt")
    events = [c.args[0]["event"] for c in maudit.call_args_list]
    assert "agentic_repo_workspace_read" in events


def test_denied_reads_are_audited(tmp_path, monkeypatch):
    fake = _fake_clone_populating(files={"a.txt": "x"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            with patch.object(repo_workspace, "audit_log") as maudit:
                with pytest.raises(AgenticError):
                    tools.read_file("../escape.txt")
    events = [c.args[0]["event"] for c in maudit.call_args_list]
    assert "agentic_repo_workspace_denied" in events


# --- no optional dependency required -----------------------------------------


# --- git writes ---------------------------------------------------------


def _fake_clone_populating_git_repo(*, files: dict[str, str]):
    """Like ``_fake_clone_populating``, but also ``git init``s the destination.

    RepoWorkspaceTools' git-write methods need a real git repository to act
    on; the read-only tests above never needed one since they only exercise
    ScopedRoots. Uses real ``git`` subprocesses (not a mock) to build that
    repo and give it an initial commit -- the same "real subprocess, not a
    double" discipline ``tests/test_agentic_executor.py`` uses, since the
    point of these tests is the actual git argv/cwd/gate plumbing.
    """

    base = _fake_clone_populating(files=files)

    def fake(op, repo, **kwargs):
        result = base(op, repo, **kwargs)
        dest = result["dest"]

        def run(*argv: str) -> None:
            subprocess.run(argv, cwd=dest, check=True, capture_output=True, text=True)

        run("git", "init", "-q")
        run("git", "-c", "user.email=fixture@example.com", "-c", "user.name=Fixture", "add", "-A")
        run("git", "-c", "user.email=fixture@example.com", "-c", "user.name=Fixture", "commit", "-q", "-m", "initial")
        return result

    return fake


def _git(*argv: str, cwd: str | Path | None = None) -> str:
    """Run one real git command for test setup/inspection.

    argv is passed as a variable rather than a literal list for the same reason
    the ``run()`` closure in _fake_clone_populating_git_repo above does: a
    literal ["git", ...] trips ruff's S607 (partial executable path), and
    suppressing it per-call would be noise on four lines that are all doing the
    same benign thing.
    """
    completed = subprocess.run(
        list(argv), cwd=None if cwd is None else str(cwd),
        check=True, capture_output=True, text=True,
    )
    return completed.stdout


def _fake_clone_with_local_origin(*, files: dict[str, str], remote: Path):
    """A git-initialised clone whose ``origin`` is a local bare repo.

    push_branch cannot be exercised against the shipped fixture: it does
    ``git init`` with no remote at all, so a push would fail with "No
    configured push destination" and prove nothing about the argv, the gate,
    or the branch scoping. A ``file://`` bare remote makes the push REAL --
    real git, real refs, verifiable afterwards -- while touching no network
    and needing no credential, which is exactly the part of a live push that
    cannot be tested here (see push_branch's own docstring).
    """
    base = _fake_clone_populating_git_repo(files=files)

    def fake(op, repo, **kwargs):
        result = base(op, repo, **kwargs)
        _git("git", "init", "--bare", "-q", str(remote))
        _git("git", "remote", "add", "origin", str(remote), cwd=result["dest"])
        return result

    return fake


def _cfg_with_git_writes(tmp_path: Path, monkeypatch) -> AgenticConfig:
    return _cfg(
        tmp_path,
        monkeypatch,
        deepagent_github={
            "workspace_root": str(tmp_path / "data" / "workspaces"),
            "allow_git_write_tools": True,
        },
    )


def test_push_branch_pushes_a_real_ref_to_origin(tmp_path, monkeypatch):
    """The one method here that leaves the box. Verified against a real remote."""
    remote = tmp_path / "origin.git"
    fake = _fake_clone_with_local_origin(files={"a.txt": "hello\n"}, remote=remote)
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            tools.checkout_branch("claude/pushed")
            assert tools.push_branch("claude/pushed") == {"pushed": "claude/pushed"}
    assert "claude/pushed" in _git("git", "--git-dir", str(remote), "branch", "--list")


def test_push_branch_is_refused_when_git_writes_are_disabled(tmp_path, monkeypatch):
    """Same allow_git_write_tools gate as every other write here -- the network
    one is not an exception to it."""
    remote = tmp_path / "origin.git"
    fake = _fake_clone_with_local_origin(files={"a.txt": "hello\n"}, remote=remote)
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticWriteRefused):
                tools.push_branch("claude/pushed")


@pytest.mark.parametrize("bad", ["main", "feature/x", "--force", "claude/has space", "", "HEAD"])
def test_push_branch_refuses_anything_outside_the_claude_namespace(tmp_path, monkeypatch, bad):
    """Scoping is enforced here, not by convention.

    Nothing else in the repo statically prevents a push to an arbitrary branch,
    so this parametrize IS the enforcement of the claude/* claim.
    """
    remote = tmp_path / "origin.git"
    fake = _fake_clone_with_local_origin(files={"a.txt": "hello\n"}, remote=remote)
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError):
                tools.push_branch(bad)
    assert "claude" not in _git("git", "--git-dir", str(remote), "branch", "--list")


def test_push_branch_disables_the_terminal_credential_prompt(tmp_path, monkeypatch):
    """Without GIT_TERMINAL_PROMPT=0 a missing credential makes git block on an
    interactive prompt until the timeout, turning a clean auth failure into a
    hang. Asserted on the env actually handed to the subprocess."""
    seen: dict = {}
    real_run = repo_workspace.subprocess.run

    def _capture(argv, **kwargs):
        if "push" in argv:
            seen.update(kwargs.get("env") or {})
        return real_run(argv, **kwargs)

    remote = tmp_path / "origin.git"
    fake = _fake_clone_with_local_origin(files={"a.txt": "hello\n"}, remote=remote)
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            tools.checkout_branch("claude/pushed")
            with patch.object(repo_workspace.subprocess, "run", side_effect=_capture):
                tools.push_branch("claude/pushed")
    assert seen.get("GIT_TERMINAL_PROMPT") == "0"
    # and the allowlist itself was not widened to carry a GitHub credential
    assert "GH_TOKEN" not in seen
    assert "GITHUB_TOKEN" not in seen


def test_git_writes_are_refused_by_default(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticWriteRefused):
                tools.checkout_branch("claude/topic")
            with pytest.raises(AgenticWriteRefused):
                tools.add(["a.txt"])
            with pytest.raises(AgenticWriteRefused):
                tools.commit("message")
            with pytest.raises(AgenticWriteRefused):
                tools.diff()


def test_checkout_add_commit_and_diff_happy_path(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake) as mrun:
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            dest = Path(mrun.call_args.kwargs["dest"])
            assert tools.checkout_branch("claude/fixture-topic") == {"branch": "claude/fixture-topic"}
            (dest / "a.txt").write_text("hello world\n", encoding="utf-8")
            tools.add(["a.txt"])
            assert "hello world" in tools.diff(cached=True)
            assert tools.diff() == ""  # nothing unstaged once added
            tools.commit("update a.txt")
            assert tools.diff(cached=True) == ""  # nothing left staged after commit


def test_checkout_branch_rejects_names_without_the_claude_prefix(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError):
                tools.checkout_branch("feature/not-allowed")
            with pytest.raises(AgenticError):
                tools.checkout_branch("-x")
            with pytest.raises(AgenticError):
                tools.checkout_branch("claude/has a space")


def test_add_rejects_paths_outside_the_clone(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError):
                tools.add(["../escape.txt"])
            with pytest.raises(AgenticError):
                tools.add(["/etc/passwd"])
            with pytest.raises(AgenticError):
                tools.add(["-x"])
            with pytest.raises(AgenticError):
                tools.add(["nonexistent.txt"])


def test_add_rejects_empty_or_nul_paths(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError):
                tools.add([""])
            with pytest.raises(AgenticError):
                tools.add(["a.\x00txt"])


def test_add_rejects_a_symlink_escape(tmp_path, monkeypatch):
    """The final containment check, not just the literal '..' rejection.

    A symlink inside the clone pointing outside it carries no ".." component
    once normalized, so it must be caught by resolving the real path and
    checking containment -- mirrors test_read_file_rejects_a_symlink_escape's
    reasoning for the read side.
    """
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake) as mrun:
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            dest = Path(mrun.call_args.kwargs["dest"])
            outside = dest.parent / "outside-secret.txt"
            outside.write_text("do not add me", encoding="utf-8")
            (dest / "escape-link").symlink_to(outside)
            with pytest.raises(AgenticError, match="escaped the clone root"):
                tools.add(["escape-link"])


def test_add_requires_at_least_one_path(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError):
                tools.add([])


def test_commit_rejects_empty_message(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError):
                tools.commit("   ")


def test_commit_forces_the_configured_committer_identity(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake) as mrun:
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            dest = Path(mrun.call_args.kwargs["dest"])
            tools.checkout_branch("claude/identity-check")
            (dest / "a.txt").write_text("changed\n", encoding="utf-8")
            tools.add(["a.txt"])
            tools.commit("test commit")
            log = subprocess.run(
                [shutil.which("git"), "log", "-1", "--format=%an <%ae>"],
                cwd=str(dest), capture_output=True, text=True, check=True,
            ).stdout.strip()
    assert log == "Claude <noreply@anthropic.com>"


def test_commit_message_is_hashed_not_logged_raw(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake) as mrun:
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            dest = Path(mrun.call_args.kwargs["dest"])
            tools.checkout_branch("claude/secret-check")
            (dest / "a.txt").write_text("changed\n", encoding="utf-8")
            tools.add(["a.txt"])
            with patch.object(repo_workspace, "audit_log") as maudit:
                tools.commit("super secret message text")
    calls = [c.args[0] for c in maudit.call_args_list]
    assert not any("super secret" in str(call) for call in calls)
    commit_events = [c for c in calls if c["event"] == "agentic_repo_workspace_git_op" and c.get("op") == "commit"]
    assert commit_events and "message_sha256" in commit_events[0]


def test_run_git_raises_a_typed_error_when_the_binary_is_missing(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            with patch.object(repo_workspace.shutil, "which", return_value=None):
                with pytest.raises(AgenticError, match="git binary not found"):
                    tools.diff()


def test_run_git_times_out_without_hanging(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            timeout_exc = subprocess.TimeoutExpired(cmd=["git", "diff"], timeout=1)
            with patch.object(repo_workspace.subprocess, "run", side_effect=timeout_exc):
                with pytest.raises(AgenticError, match="timed out"):
                    tools.diff()


def test_git_op_failure_surfaces_stderr_in_the_error_details(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            tools.checkout_branch("claude/no-changes")
            # Nothing staged -- git commit fails deterministically, no extra setup.
            with pytest.raises(AgenticError, match="git commit failed"):
                tools.commit("nothing to see here")


# --- write_file ----------------------------------------------------------


def test_write_file_creates_a_new_file(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake) as mrun:
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            dest = Path(mrun.call_args.kwargs["dest"])
            assert tools.write_file("new.txt", "brand new content\n") == {"target": "new.txt", "bytes": 18}
            assert (dest / "new.txt").read_text(encoding="utf-8") == "brand new content\n"


def test_write_file_overwrites_an_existing_file(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake) as mrun:
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            dest = Path(mrun.call_args.kwargs["dest"])
            tools.write_file("a.txt", "replaced\n")
            assert (dest / "a.txt").read_text(encoding="utf-8") == "replaced\n"


def test_write_file_creates_parent_directories(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake) as mrun:
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            dest = Path(mrun.call_args.kwargs["dest"])
            tools.write_file("nested/dir/new.txt", "deep\n")
            assert (dest / "nested" / "dir" / "new.txt").read_text(encoding="utf-8") == "deep\n"


def test_write_file_refused_by_default(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticWriteRefused):
                tools.write_file("new.txt", "content\n")


def test_write_file_rejects_paths_outside_the_clone(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError):
                tools.write_file("../escape.txt", "x")
            with pytest.raises(AgenticError):
                tools.write_file("/etc/passwd", "x")
            with pytest.raises(AgenticError):
                tools.write_file("-x", "x")
            with pytest.raises(AgenticError):
                tools.write_file("nested/../../escape.txt", "x")


@pytest.mark.parametrize(
    "target",
    [".git/config", ".git/hooks/pre-commit", ".git/info/attributes", ".GIT/config", ".Git/config",
     "nested/.git/config", "./.git/config"],
)
def test_write_file_refuses_the_clones_git_directory(tmp_path, monkeypatch, target):
    """The clone's own git metadata is never a writable target.

    Writing `.git/` is not merely out of scope -- see
    test_git_filter_injection_through_dotgit_config_is_refused for why it is
    arbitrary command execution. Case variants are covered because a
    case-insensitive filesystem resolves `.GIT` to the same directory, and a
    nested segment because a submodule's `.git` is a gitdir too.
    """
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError, match="\\.git directory"):
                tools.write_file(target, "x")


def test_add_refuses_the_clones_git_directory(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError, match="\\.git directory"):
                tools.add([".git/config"])


@pytest.mark.parametrize("target", [".gitattributes", ".gitignore", ".gitmodules", "docs/.gitkeep"])
def test_write_file_still_allows_dotgit_prefixed_filenames(tmp_path, monkeypatch, target):
    # The refusal compares whole path SEGMENTS, so ordinary dotfiles whose name
    # merely starts with ".git" stay writable -- they are normal tracked files.
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            assert tools.write_file(target, "x\n")["target"] == target


def test_git_filter_injection_through_dotgit_config_is_refused(tmp_path, monkeypatch):
    """Reproduces the concrete attack the .git refusal exists to stop.

    A repo-local clean filter defined in `.git/config`, plus a `.gitattributes`
    assigning it, executes its shell command during the plain `git add` that
    finalize_real_repo_change performs -- in the CLI process, at human-approval
    time, with the operator's own PATH and HOME, and invisible to `git diff`
    (which cannot render `.git/config`). This test writes the `.gitattributes`
    half through the real write_file (it is a legitimate tracked file), proves
    the `.git/config` half is refused, and then runs a real `git add` to confirm
    no command executed.
    """
    sentinel = tmp_path / "PWNED"
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            tools.write_file(".gitattributes", "* filter=pwn\n")
            with pytest.raises(AgenticError, match="\\.git directory"):
                tools.write_file(
                    ".git/config",
                    f'[filter "pwn"]\n\tclean = sh -c "touch {sentinel}; cat"\n',
                )
            tools.add([".gitattributes", "a.txt"])
    assert not sentinel.exists(), "a repo-local git filter executed during git add"


def test_commit_does_not_run_repo_hooks(tmp_path, monkeypatch):
    """--no-verify: a hook that arrived by some other path must not execute.

    write_file can no longer create one, so this installs the hook directly on
    disk -- the point is that commit() itself refuses to run hooks, independent
    of how one got there.
    """
    sentinel = tmp_path / "HOOK_RAN"
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            hook = Path(tools.worktree) / ".git" / "hooks" / "pre-commit"
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text(f"#!/bin/sh\ntouch {sentinel}\n", encoding="utf-8")
            hook.chmod(0o755)
            tools.write_file("a.txt", "changed\n")
            tools.add(["a.txt"])
            tools.commit("test: hooks must not run")
    assert not sentinel.exists(), "a repo-local pre-commit hook executed during commit"


def test_write_file_rejects_content_exceeding_max_write_bytes(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            tools.max_write_bytes = 10
            with pytest.raises(AgenticError, match="max_write_bytes"):
                tools.write_file("new.txt", "x" * 11)


def test_write_file_rejects_non_string_content(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            with pytest.raises(AgenticError):
                tools.write_file("new.txt", b"not a string")  # type: ignore[arg-type]


def test_write_file_rejects_a_symlink_escape_via_existing_ancestor(tmp_path, monkeypatch):
    """A new file whose PARENT directory is a symlink escaping the clone.

    The leaf itself doesn't exist yet, so must_exist=False's ancestor walk is
    what has to catch this -- the nearest existing ancestor (the symlinked
    directory) resolves outside dest_resolved.
    """
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake) as mrun:
        with RepoWorkspaceTools.clone(_cfg_with_git_writes(tmp_path, monkeypatch)) as tools:
            dest = Path(mrun.call_args.kwargs["dest"])
            outside = dest.parent / "outside-dir"
            outside.mkdir()
            (dest / "escape-link").symlink_to(outside, target_is_directory=True)
            with pytest.raises(AgenticError, match="escaped the clone root"):
                tools.write_file("escape-link/new.txt", "x")


# --- attach ----------------------------------------------------------------


def test_attach_reopens_an_existing_clone_and_can_read_and_write(tmp_path, monkeypatch):
    fake = _fake_clone_populating_git_repo(files={"a.txt": "hello\n"})
    cfg = _cfg_with_git_writes(tmp_path, monkeypatch)
    with patch.object(repo_workspace, "run_read", side_effect=fake) as mrun:
        original = RepoWorkspaceTools.clone(cfg)
        dest = Path(mrun.call_args.kwargs["dest"])
        assert original.worktree == dest
        original._scoped.close()  # simulate the process that cloned it having exited

    with RepoWorkspaceTools.attach(cfg, dest) as reattached:
        assert reattached.read_file("a.txt") == "hello\n"
        assert reattached.allow_git_write_tools is True
        reattached.write_file("new.txt", "attached write\n")
        assert (dest / "new.txt").read_text(encoding="utf-8") == "attached write\n"


def test_attach_rejects_a_destination_outside_the_workspace_root(tmp_path, monkeypatch):
    cfg = _cfg_with_git_writes(tmp_path, monkeypatch)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(AgenticError, match="outside the configured workspace root"):
        RepoWorkspaceTools.attach(cfg, outside)


def test_attach_rejects_a_missing_directory(tmp_path, monkeypatch):
    cfg = _cfg_with_git_writes(tmp_path, monkeypatch)
    workspace_root = Path(cfg.deepagent_github.workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
    missing = workspace_root / "does-not-exist"
    with pytest.raises(AgenticError, match="does not exist"):
        RepoWorkspaceTools.attach(cfg, missing)


def test_attach_wraps_a_jail_failure_as_agentic_error(tmp_path, monkeypatch):
    cfg = _cfg_with_git_writes(tmp_path, monkeypatch)
    workspace_root = Path(cfg.deepagent_github.workspace_root)
    existing = workspace_root / "already-here"
    existing.mkdir(parents=True)
    with patch.object(repo_workspace, "ScopedRoots", side_effect=OSError("boom")):
        with pytest.raises(AgenticError, match="failed to jail"):
            RepoWorkspaceTools.attach(cfg, existing)


def test_module_imports_without_deepagents_or_langchain():
    # Confirms the module docstring's claim: no deepagents/langchain import at
    # module scope. If this ever changed it would silently require
    # pytest.importorskip everywhere this module is imported.
    import ast
    import inspect

    src = inspect.getsource(deepagent_github.repo_workspace)
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert "deepagents" not in names
    assert "langchain" not in names
