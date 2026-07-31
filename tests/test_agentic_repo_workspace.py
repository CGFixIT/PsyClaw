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


def _cfg_with_git_writes(tmp_path: Path, monkeypatch) -> AgenticConfig:
    return _cfg(
        tmp_path,
        monkeypatch,
        deepagent_github={
            "workspace_root": str(tmp_path / "data" / "workspaces"),
            "allow_git_write_tools": True,
        },
    )


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
