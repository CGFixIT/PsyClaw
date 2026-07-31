"""Tests for the `real-repo-run`/`real-repo-run-status`/`real-repo-run-decide`
CLI subcommands -- the first live wiring of agentic.real_repo_loop.

Three things are mocked (no live network, no gh/git subprocess for the
context-fetch leg, no live model): agentic.context.run_read (task context),
agentic.deepagent_github.repo_workspace.run_read (the clone), and
LocalProposerClient.invoke (the planner model call, patched on the class so
the lazy per-call import inside cli.py still gets the patched method). Actual
`git` subprocesses run for real against the fixture repo the clone mock
populates, matching this session's "real subprocess, not a double" testing
discipline for anything downstream of those three mock points.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agentic.cli import EXIT_ENV, EXIT_FAIL, EXIT_OK, EXIT_REFUSED, main
from agentic.harness_optimizer.model_adapter import LocalProposerClient, LocalProposerResponse

_RIGHT_BLOCK = "=== FILE target.txt ===\nexpected marker\n=== END FILE ===\nfix"
_WRONG_BLOCK = "=== FILE target.txt ===\nwrong content\n=== END FILE ===\nattempt"


@pytest.fixture()
def cfg_path(tmp_path, monkeypatch):
    """A config.yaml with the agentic layer + git write tools on.

    agentic.config._resolve_data_path forces workspace_root to resolve
    inside the REPO's own data/ tree (config.py's own real containment, not
    a test-only rule) -- repoint what "the repo root" means for this
    construction only, mirroring tests/test_agentic_repo_workspace.py's own
    fixture, so workspace_root safely resolves under tmp_path/data instead.
    """
    from agentic import config as agentic_config_module
    from utils.logger import reset_config_cache

    monkeypatch.setattr(agentic_config_module, "_repo_root", lambda: tmp_path)
    src = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    src["logging"]["audit_file"] = str(tmp_path / "audit.jsonl")
    src["agentic"]["enabled"] = True
    src["agentic"]["deepagent_github"]["allow_git_write_tools"] = True
    src["agentic"]["deepagent_github"]["workspace_root"] = str(tmp_path / "data" / "workspaces")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(src), encoding="utf-8")
    reset_config_cache()
    yield str(path)
    reset_config_cache()


@pytest.fixture(autouse=True)
def _fake_context_reads(monkeypatch):
    """Stub the context-fetch leg (agentic.context's own run_read reference)."""
    from agentic import context

    def fake(op, repo, **kwargs):
        if op == "pr_diff":
            return {"op": op, "repo": repo, "diff": "diff --git a/f b/f\n+x"}
        if op in ("pr_list", "issue_list"):
            return {"op": op, "repo": repo, "data": [{"number": 1, "title": "clean title"}]}
        return {"op": op, "repo": repo, "data": {"title": "clean", "body": "a normal description"}}

    monkeypatch.setattr(context, "run_read", fake)


@pytest.fixture(autouse=True)
def _fake_clone(monkeypatch):
    """Stub the clone leg with a real git repo (repo_workspace's own run_read reference)."""
    from agentic.deepagent_github import repo_workspace

    def fake(op, repo, **kwargs):
        assert op == "repo_clone"
        dest = Path(kwargs["dest"])
        dest.mkdir(parents=True)
        (dest / "README.md").write_text("hello\n", encoding="utf-8")

        def run(*argv: str) -> None:
            subprocess.run(argv, cwd=str(dest), check=True, capture_output=True, text=True)

        run("git", "init", "-q")
        run("git", "-c", "user.email=fixture@example.com", "-c", "user.name=Fixture", "add", "-A")
        run("git", "-c", "user.email=fixture@example.com", "-c", "user.name=Fixture", "commit", "-q", "-m", "initial")
        return {"dest": str(dest)}

    monkeypatch.setattr(repo_workspace, "run_read", fake)


@pytest.fixture()
def checks_file(tmp_path):
    marker_check = {
        "name": "marker_check",
        "argv": [
            sys.executable, "-c",
            "import pathlib,sys; sys.exit(0 if 'expected marker' in pathlib.Path('target.txt').read_text() else 1)",
        ],
    }
    path = tmp_path / "checks.json"
    path.write_text(json.dumps([marker_check]), encoding="utf-8")
    return str(path)


def _fake_model(block: str):
    def fake_invoke(self, *, system_prompt, user_prompt, max_tokens=2048, temperature=0.0, config_path="config.yaml",
                     cfg=None):
        return LocalProposerResponse(content=block, model=self.model)

    return fake_invoke


def _run_start(cfg_path, checks_file, *, block=_RIGHT_BLOCK, extra=()):
    return main([
        "--config", cfg_path, "real-repo-run",
        "--repo", "--instruction", "add the marker",
        "--checks-file", checks_file,
        "--branch", "claude/fixture-topic", "--commit-message", "add target.txt",
        "--reason", "test run", "--confirm", *extra,
    ])


# --- real-repo-run: happy path -----------------------------------------------


def test_run_accepts_and_persists_a_pending_decision(cfg_path, checks_file, monkeypatch, capsys):
    monkeypatch.setattr(LocalProposerClient, "invoke", _fake_model(_RIGHT_BLOCK))
    assert _run_start(cfg_path, checks_file) == EXIT_OK
    record = json.loads(capsys.readouterr().out)
    assert record["status"] == "pending_decision"
    assert record["branch_name"] == "claude/fixture-topic"
    assert record["changed_files"] == ["target.txt"]
    assert Path(record["dest"]).is_dir()


def test_run_exhausts_and_discards_the_clone(cfg_path, checks_file, monkeypatch, capsys):
    monkeypatch.setattr(LocalProposerClient, "invoke", _fake_model(_WRONG_BLOCK))
    assert _run_start(cfg_path, checks_file, extra=("--max-iterations", "1")) == EXIT_OK
    record = json.loads(capsys.readouterr().out)
    assert record["status"] == "exhausted"
    assert not Path(record["dest"]).exists()  # nothing accepted -- clone discarded


def test_run_disabled_layer_is_a_clean_noop(tmp_path, checks_file, capsys):
    from utils.logger import reset_config_cache

    src = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    src["agentic"]["enabled"] = False
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(src), encoding="utf-8")
    reset_config_cache()
    try:
        assert _run_start(str(path), checks_file) == EXIT_OK
        assert "disabled" in capsys.readouterr().out.lower()
    finally:
        reset_config_cache()


def test_run_refuses_without_confirm(cfg_path, checks_file, monkeypatch, capsys):
    monkeypatch.setattr(LocalProposerClient, "invoke", _fake_model(_RIGHT_BLOCK))
    code = main([
        "--config", cfg_path, "real-repo-run", "--repo", "--instruction", "x",
        "--checks-file", checks_file, "--branch", "claude/x", "--commit-message", "x", "--reason", "test",
    ])
    assert code == EXIT_REFUSED
    assert "confirm" in capsys.readouterr().err.lower()


def test_run_refuses_on_a_critical_governance_finding_in_context(cfg_path, checks_file, monkeypatch, capsys):
    from agentic import context
    from agentic.context import INJECTION_FINDING_CODE

    monkeypatch.setattr(
        context, "_injection_findings",
        lambda *a, **k: [{"severity": "critical", "code": INJECTION_FINDING_CODE,
                          "field": "repo.description", "patterns": ["x"]}],
    )
    assert _run_start(cfg_path, checks_file) == EXIT_FAIL
    assert "refusing to run" in capsys.readouterr().err


def test_run_env_errors_on_a_missing_checks_file(cfg_path):
    code = main([
        "--config", cfg_path, "real-repo-run", "--repo", "--instruction", "x",
        "--checks-file", "/no/such/file.json", "--branch", "claude/x", "--commit-message", "x",
        "--reason", "test", "--confirm",
    ])
    assert code == EXIT_ENV


def test_run_env_errors_on_an_empty_checks_list(cfg_path, tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    code = main([
        "--config", cfg_path, "real-repo-run", "--repo", "--instruction", "x",
        "--checks-file", str(empty), "--branch", "claude/x", "--commit-message", "x",
        "--reason", "test", "--confirm",
    ])
    assert code == EXIT_ENV


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ('{"not": "a list"}', "non-empty JSON list"),
        ("[{\"name\": \"x\"}]", "'name' and 'argv'"),
        ("[{\"name\": \"x\", \"argv\": \"not-a-list\"}]", "non-empty list of strings"),
        ("[{\"name\": \"x\", \"argv\": []}]", "non-empty list of strings"),
    ],
)
def test_run_env_errors_on_a_malformed_checks_manifest(cfg_path, tmp_path, content, match):
    bad = tmp_path / "bad.json"
    bad.write_text(content, encoding="utf-8")
    code = main([
        "--config", cfg_path, "real-repo-run", "--repo", "--instruction", "x",
        "--checks-file", str(bad), "--branch", "claude/x", "--commit-message", "x",
        "--reason", "test", "--confirm",
    ])
    assert code == EXIT_ENV


def test_load_checks_file_honors_a_custom_timeout(tmp_path):
    from agentic.cli import _load_checks_file

    manifest = tmp_path / "checks.json"
    manifest.write_text(json.dumps([{"name": "slow", "argv": ["true"], "timeout_sec": 5}]), encoding="utf-8")
    checks = _load_checks_file(str(manifest))
    assert checks[0].timeout_sec == 5


def test_run_pr_and_issue_targets_are_accepted(cfg_path, checks_file, monkeypatch, capsys):
    monkeypatch.setattr(LocalProposerClient, "invoke", _fake_model(_RIGHT_BLOCK))
    assert main([
        "--config", cfg_path, "real-repo-run", "--pr", "1", "--instruction", "x",
        "--checks-file", checks_file, "--branch", "claude/pr-topic", "--commit-message", "x",
        "--reason", "test", "--confirm",
    ]) == EXIT_OK
    capsys.readouterr()
    assert main([
        "--config", cfg_path, "real-repo-run", "--issue", "9", "--instruction", "x",
        "--checks-file", checks_file, "--branch", "claude/issue-topic", "--commit-message", "x",
        "--reason", "test", "--confirm",
    ]) == EXIT_OK


def test_run_env_errors_when_the_clone_fails(cfg_path, checks_file, monkeypatch):
    from agentic.deepagent_github import repo_workspace
    from utils.errors import GhNotInstalledError

    def failing_clone(op, repo, **kwargs):
        raise GhNotInstalledError("gh is not installed")

    monkeypatch.setattr(repo_workspace, "run_read", failing_clone)
    assert _run_start(cfg_path, checks_file) == EXIT_ENV


def test_run_fails_when_the_clone_raises_a_generic_agentic_error(cfg_path, checks_file, monkeypatch, capsys):
    from agentic.deepagent_github import repo_workspace
    from utils.errors import AgenticError

    def failing_clone(op, repo, **kwargs):
        raise AgenticError("clone blew up")

    monkeypatch.setattr(repo_workspace, "run_read", failing_clone)
    assert _run_start(cfg_path, checks_file) == EXIT_FAIL
    assert "clone blew up" in capsys.readouterr().err


def test_run_fails_when_pr_context_fetch_raises(cfg_path, checks_file, monkeypatch, capsys):
    from agentic import context
    from utils.errors import AgenticError

    def failing_read(op, repo, **kwargs):
        raise AgenticError("pr fetch blew up")

    monkeypatch.setattr(context, "run_read", failing_read)
    code = main([
        "--config", cfg_path, "real-repo-run", "--pr", "1", "--instruction", "x",
        "--checks-file", checks_file, "--branch", "claude/x", "--commit-message", "x",
        "--reason", "test", "--confirm",
    ])
    assert code == EXIT_FAIL
    assert "pr fetch blew up" in capsys.readouterr().err


def test_run_env_errors_when_issue_context_fetch_reports_gh_missing(cfg_path, checks_file, monkeypatch):
    from agentic import context
    from utils.errors import GhNotInstalledError

    def failing_read(op, repo, **kwargs):
        raise GhNotInstalledError("gh is not installed")

    monkeypatch.setattr(context, "run_read", failing_read)
    code = main([
        "--config", cfg_path, "real-repo-run", "--issue", "9", "--instruction", "x",
        "--checks-file", checks_file, "--branch", "claude/x", "--commit-message", "x",
        "--reason", "test", "--confirm",
    ])
    assert code == EXIT_ENV


def test_run_persists_a_failed_record_on_an_unexpected_loop_error(cfg_path, checks_file, monkeypatch, capsys):
    from agentic import real_repo_loop
    from utils.errors import AgenticError

    def explode(*a, **k):
        raise AgenticError("simulated unexpected failure")

    # run_real_repo_loop is imported lazily inside cmd_real_repo_run (from
    # agentic.real_repo_loop import run_real_repo_loop), so it must be
    # patched at its own module -- that's what the lazy import re-resolves
    # against on each call, not any name on agentic.cli itself.
    monkeypatch.setattr(real_repo_loop, "run_real_repo_loop", explode)
    code = _run_start(cfg_path, checks_file)
    assert code == EXIT_FAIL
    assert "simulated unexpected failure" in capsys.readouterr().err


# --- status -------------------------------------------------------------


def test_status_reports_a_persisted_run(cfg_path, checks_file, monkeypatch, capsys):
    monkeypatch.setattr(LocalProposerClient, "invoke", _fake_model(_RIGHT_BLOCK))
    _run_start(cfg_path, checks_file)
    run_id = json.loads(capsys.readouterr().out)["run_id"]

    assert main(["--config", cfg_path, "real-repo-run-status", "--run-id", run_id]) == EXIT_OK
    record = json.loads(capsys.readouterr().out)
    assert record["run_id"] == run_id
    assert record["status"] == "pending_decision"


def test_status_fails_for_an_unknown_run_id(cfg_path, capsys):
    import uuid

    code = main(["--config", cfg_path, "real-repo-run-status", "--run-id", uuid.uuid4().hex])
    assert code == EXIT_FAIL
    assert "not found" in capsys.readouterr().err


def test_status_disabled_layer_is_a_clean_noop(tmp_path, capsys):
    from utils.logger import reset_config_cache

    src = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    src["agentic"]["enabled"] = False
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(src), encoding="utf-8")
    reset_config_cache()
    try:
        assert main(["--config", str(path), "real-repo-run-status", "--run-id", "a" * 32]) == EXIT_OK
    finally:
        reset_config_cache()


# --- decide ---------------------------------------------------------------


def test_decide_approve_commits_and_updates_status(cfg_path, checks_file, monkeypatch, capsys):
    monkeypatch.setattr(LocalProposerClient, "invoke", _fake_model(_RIGHT_BLOCK))
    _run_start(cfg_path, checks_file)
    record = json.loads(capsys.readouterr().out)
    run_id, dest = record["run_id"], record["dest"]

    code = main(["--config", cfg_path, "real-repo-run-decide", "--run-id", run_id, "--decision", "approve"])
    assert code == EXIT_OK
    decided = json.loads(capsys.readouterr().out)
    assert decided["status"] == "approved"

    git_bin = __import__("shutil").which("git")
    log = subprocess.run(
        [git_bin, "log", "-1", "--format=%an <%ae> %s"], cwd=dest, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert log == "Claude <noreply@anthropic.com> add target.txt"


def test_decide_reject_never_commits_and_discards_the_clone(cfg_path, checks_file, monkeypatch, capsys):
    monkeypatch.setattr(LocalProposerClient, "invoke", _fake_model(_RIGHT_BLOCK))
    _run_start(cfg_path, checks_file)
    record = json.loads(capsys.readouterr().out)
    run_id, dest = record["run_id"], record["dest"]

    code = main(["--config", cfg_path, "real-repo-run-decide", "--run-id", run_id, "--decision", "reject"])
    assert code == EXIT_OK
    decided = json.loads(capsys.readouterr().out)
    assert decided["status"] == "rejected"
    assert not Path(dest).exists()


def test_decide_refuses_a_second_decision_on_the_same_run(cfg_path, checks_file, monkeypatch, capsys):
    monkeypatch.setattr(LocalProposerClient, "invoke", _fake_model(_RIGHT_BLOCK))
    _run_start(cfg_path, checks_file)
    run_id = json.loads(capsys.readouterr().out)["run_id"]

    assert main(["--config", cfg_path, "real-repo-run-decide", "--run-id", run_id, "--decision", "approve"]) == EXIT_OK
    capsys.readouterr()
    code = main(["--config", cfg_path, "real-repo-run-decide", "--run-id", run_id, "--decision", "reject"])
    assert code == EXIT_FAIL
    assert "already decided" in capsys.readouterr().err


def test_decide_refuses_when_git_write_tools_are_off(tmp_path, checks_file, monkeypatch, capsys):
    """allow_git_write_tools flips off between run and decide -- an operator
    could plausibly do this; the low-level gate must still catch it."""
    from agentic import config as agentic_config_module
    from utils.logger import reset_config_cache

    monkeypatch.setattr(agentic_config_module, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(LocalProposerClient, "invoke", _fake_model(_RIGHT_BLOCK))
    src = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    src["agentic"]["enabled"] = True
    src["agentic"]["deepagent_github"]["allow_git_write_tools"] = True
    src["agentic"]["deepagent_github"]["workspace_root"] = str(tmp_path / "data" / "workspaces")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(src), encoding="utf-8")
    reset_config_cache()
    try:
        _run_start(str(path), checks_file)
        run_id = json.loads(capsys.readouterr().out)["run_id"]

        src["agentic"]["deepagent_github"]["allow_git_write_tools"] = False
        path.write_text(yaml.safe_dump(src), encoding="utf-8")
        reset_config_cache()

        code = main(["--config", str(path), "real-repo-run-decide", "--run-id", run_id, "--decision", "approve"])
        assert code == EXIT_REFUSED
    finally:
        reset_config_cache()
