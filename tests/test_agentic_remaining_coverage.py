"""Remaining agentic coverage: call real shipped helpers; mock only gh/network."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
import yaml

from agentic.config import AgenticConfig
from agentic.deepagent_github import repo_workspace
from agentic.deepagent_github.builder import DeepAgentBuildResult
from agentic.deepagent_github.chat_client import (
    _HTTP_USAGE,
    _as_mapping,
    _capture_http_usage,
    _spend_file_from_cfg,
    _usage_from_langchain_metadata,
)
from agentic.deepagent_github.core import DeepAgentGitHubTask
from agentic.deepagent_github.model_adapter import DeepAgentModelSettings, build_chat_model
from agentic.deepagent_github.repo_workspace import RepoWorkspaceTools
from agentic.deepagent_github.runners import draft_plan, invoke_deepagent, resume_deepagent_interrupt
from agentic.deepagent_github.subagents import build_subagent_specs
from agentic.executor import Check, run_verification
from agentic.executor.apply import prove_disposable_copy
from agentic.executor.hard_sandbox import HardSandboxUnavailable
from agentic.executor.manifest import _jail, build_manifest, git_head, verify_manifest
from agentic.gh_client import build_read_argv, check_gh_version
from agentic.harness_optimizer.core import (
    CandidateDecision,
    Experiment,
    RunReport,
    Surface,
    SurfaceType,
    Variant,
    decide_candidate,
)
from agentic.harness_optimizer.governance import detect_visible_case_hardcoding, inspect_code_shape
from agentic.harness_optimizer.mcp import tools as mcp_tools
from agentic.harness_optimizer.mcp.tools import ProposerWorkspaceTools, _contains, _contains_write_target
from agentic.harness_optimizer.model_adapter import LocalProposerClient
from agentic.harness_optimizer.proposer import _resolve_child, build_proposer_workspace
from agentic.harness_optimizer.runners.base_runner import MockHarnessRunner, MockRunnerCase
from agentic.harness_optimizer.runners.github_coding_runner import (
    FixtureCase,
    GitHubCodingEvaluation,
    GitHubCodingRunner,
    _safe_child,
    fetch_github_task_context,
)
from agentic.real_repo_loop import (
    _MAX_PLAN_CHARS,
    _fs_equiv_path,
    _matches_protected_path,
    _sha256,
    generate_plan,
    run_real_repo_loop,
)
from utils.errors import AgenticError, GhVersionError
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _temp_audit(tmp_path, monkeypatch):
    cfg = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
        "policy": {"privacy": {}},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    reset_config_cache()
    from utils.logger import _get_config

    _get_config(str(path))
    check_gh_version.cache_clear()
    yield
    check_gh_version.cache_clear()
    reset_config_cache()


def _git_init(root: Path) -> None:
    git_bin = shutil.which("git")
    assert git_bin is not None
    subprocess.run([git_bin, "init"], cwd=str(root), check=True, capture_output=True)
    subprocess.run([git_bin, "config", "user.name", "t"], cwd=str(root), check=True, capture_output=True)
    subprocess.run([git_bin, "config", "user.email", "t@t"], cwd=str(root), check=True, capture_output=True)
    (root / "keep.txt").write_text("k\n", encoding="utf-8")
    subprocess.run([git_bin, "add", "keep.txt"], cwd=str(root), check=True, capture_output=True)
    subprocess.run([git_bin, "commit", "-m", "i"], cwd=str(root), check=True, capture_output=True)


def _experiment() -> Experiment:
    return Experiment(
        "exp_cov",
        "workspace",
        (Surface("planner", SurfaceType.GITHUB_CODING_PROMPT, "planner.py"),),
        train_visible=("case-1",),
        holdout_hidden=("case-h1",),
    )


# --- executor/manifest.py -----------------------------------------------------


def test_git_head_fails_closed_when_git_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentic.executor.manifest.shutil.which", lambda _name: None)
    with pytest.raises(AgenticError, match="git executable not found"):
        git_head(tmp_path)


def test_git_head_fails_closed_on_nonzero_rev_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentic.executor.manifest.shutil.which", lambda _name: "git")
    monkeypatch.setattr(
        "agentic.executor.manifest.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=128, stdout="", stderr="fatal"),
    )
    with pytest.raises(AgenticError, match="could not read worktree HEAD"):
        git_head(tmp_path)


def test_jail_rejects_resolved_path_outside_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "wt"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    orig = Path.resolve

    def fake_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        if self.name == "escape.txt":
            return outside / "escape.txt"
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    with pytest.raises(AgenticError, match="escapes worktree"):
        _jail(root, "escape.txt")


def test_verify_manifest_refuses_when_head_drifted(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    head = git_head(tmp_path)
    _, digest = build_manifest(tmp_path, ["a.txt"], run_id="0" * 32, base_head=head)
    (tmp_path / "b.txt").write_text("extra\n", encoding="utf-8")
    git_bin = shutil.which("git")
    assert git_bin is not None
    subprocess.run([git_bin, "add", "b.txt"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run([git_bin, "commit", "-m", "drift"], cwd=str(tmp_path), check=True, capture_output=True)
    with pytest.raises(AgenticError, match="HEAD drifted"):
        verify_manifest(
            tmp_path, ["a.txt"], run_id="0" * 32, base_head=head, expected_digest=digest,
        )


# --- executor/apply.py --------------------------------------------------------


def test_prove_disposable_copy_restores_preexisting_git_config_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _git_init(tmp_path)
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    head = git_head(tmp_path)
    _, digest = build_manifest(tmp_path, ["a.txt"], run_id="0" * 32, base_head=head)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "preexisting-global")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "preexisting-system")
    assert (
        prove_disposable_copy(
            tmp_path, ["a.txt"], run_id="0" * 32, base_head=head, expected_digest=digest,
        )
        == digest
    )
    assert os.environ["GIT_CONFIG_GLOBAL"] == "preexisting-global"
    assert os.environ["GIT_CONFIG_SYSTEM"] == "preexisting-system"


def test_prove_disposable_copy_wraps_copytree_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _git_init(tmp_path)
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    head = git_head(tmp_path)
    _, digest = build_manifest(tmp_path, ["a.txt"], run_id="0" * 32, base_head=head)

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("copy failed")

    monkeypatch.setattr("agentic.executor.apply.shutil.copytree", boom)
    with pytest.raises(AgenticError, match="failed to copy candidate tree"):
        prove_disposable_copy(
            tmp_path, ["a.txt"], run_id="0" * 32, base_head=head, expected_digest=digest,
        )


# --- executor/runner.py -------------------------------------------------------


def test_run_verification_requires_sandbox_backend_when_production_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agentic.executor.runner.production_sandbox", lambda: None)
    check = Check("probe", (sys.executable, "-c", "pass"), timeout_sec=5)
    with pytest.raises(HardSandboxUnavailable, match="sandbox backend"):
        run_verification(tmp_path, [check], sandbox=None)


# --- deepagent_github/model_adapter.py ----------------------------------------


def test_build_chat_model_local_import_error_names_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "langchain_openai", None)
    settings = DeepAgentModelSettings(
        provider="ollama", base_url="http://localhost:11434/v1", model="m", is_cloud=False,
    )
    with pytest.raises(AgenticError, match="optional Deep Agents runtime"):
        build_chat_model(settings)


def test_build_chat_model_claude_import_error_names_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setitem(sys.modules, "langchain_anthropic", None)
    settings = DeepAgentModelSettings(provider="claude", base_url="", model="m", is_cloud=True)
    with pytest.raises(AgenticError, match="optional Deep Agents runtime"):
        build_chat_model(settings)


def test_build_chat_model_grok_forwards_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    monkeypatch.setenv("GROK_API_KEY", "k")
    seen: dict[str, object] = {}

    class Recording:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)

    stub = types.ModuleType("langchain_xai")
    stub.ChatXAI = Recording  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_xai", stub)
    client = httpx.Client()
    try:
        settings = DeepAgentModelSettings(provider="grok", base_url="", model="m", is_cloud=True)
        build_chat_model(settings, http_client=client)
    finally:
        client.close()
    assert seen.get("http_client") is client


# --- deepagent_github/runners.py ----------------------------------------------


def test_draft_plan_rejects_empty_fields() -> None:
    with pytest.raises(AgenticError, match="non-empty"):
        draft_plan(DeepAgentGitHubTask("", "owner/repo", "do it"))
    with pytest.raises(AgenticError, match="non-empty"):
        draft_plan(DeepAgentGitHubTask("t1", "", "do it"))
    with pytest.raises(AgenticError, match="non-empty"):
        draft_plan(DeepAgentGitHubTask("t1", "owner/repo", "  "))


def test_invoke_deepagent_requires_built_agent() -> None:
    task = DeepAgentGitHubTask("t1", "owner/repo", "do it")
    with pytest.raises(AgenticError, match="not built"):
        invoke_deepagent(DeepAgentBuildResult(False, "skipped", "n/a", (), ()), task)


def test_resume_deepagent_rejects_unknown_decision() -> None:
    with pytest.raises(AgenticError, match="approve, reject, or timeout"):
        resume_deepagent_interrupt(object(), task_id="t1", decision="maybe")  # type: ignore[arg-type]


# --- deepagent_github/chat_client.py ------------------------------------------


def test_as_mapping_uses_model_dump_when_present() -> None:
    class Dumpable:
        def model_dump(self) -> dict[str, object]:
            return {"a": 1}

    assert _as_mapping(Dumpable()) == {"a": 1}
    assert _as_mapping(SimpleNamespace(model_dump=lambda: "nope")) is None


def test_usage_from_langchain_metadata_claude_shape() -> None:
    usage = _usage_from_langchain_metadata(
        "claude",
        {
            "input_tokens": 3,
            "output_tokens": 4,
            "input_token_details": {"cache_creation": 1, "cache_read": 2},
        },
    )
    assert usage["input_tokens"] == 3
    assert usage["cache_creation_input_tokens"] == 1
    assert usage["cache_read_input_tokens"] == 2


def test_capture_http_usage_ignores_non_2xx_and_swallows_body_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = httpx.Request("POST", "https://api.x.ai/v1/chat/completions")
    bad = httpx.Response(500, json={"error": "nope"}, request=req)
    token = _HTTP_USAGE.set(None)
    try:
        _capture_http_usage(bad)
        assert _HTTP_USAGE.get() is None
    finally:
        _HTTP_USAGE.reset(token)

    broken = httpx.Response(200, json={"usage": {"prompt_tokens": 1}}, request=req)
    monkeypatch.setattr(broken, "read", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    token = _HTTP_USAGE.set(None)
    try:
        _capture_http_usage(broken)
        assert _HTTP_USAGE.get() is None
    finally:
        _HTTP_USAGE.reset(token)


def test_spend_file_from_cfg_rejects_non_mapping_shapes(tmp_path: Path) -> None:
    assert _spend_file_from_cfg(None) is None
    assert _spend_file_from_cfg({"logging": "nope"}) is None  # type: ignore[arg-type]
    assert _spend_file_from_cfg({"logging": {"spend_file": "  "}}) is None
    path = _spend_file_from_cfg({"logging": {"spend_file": str(tmp_path / "spend.jsonl")}})
    assert path == tmp_path / "spend.jsonl"


# --- deepagent_github/repo_workspace.py ---------------------------------------


def _cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides: object) -> AgenticConfig:
    from agentic import config as agentic_config_module

    monkeypatch.setattr(agentic_config_module, "_repo_root", lambda: tmp_path)
    kwargs: dict = {
        "repo": "owner/repo",
        "mode": "read",
        "deepagent_github": {
            "workspace_root": str(tmp_path / "data" / "workspaces"),
            "allow_git_write_tools": True,
        },
    }
    kwargs.update(overrides)
    return AgenticConfig(**kwargs)


def _fake_clone(*, files: dict[str, str]):
    def fake(op: str, repo: str, **kwargs: object) -> dict[str, object]:
        assert op == "repo_clone"
        dest = Path(str(kwargs["dest"]))
        dest.mkdir(parents=True)
        for rel, content in files.items():
            path = dest / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="")
        git_bin = shutil.which("git")
        assert git_bin is not None
        subprocess.run([git_bin, "init", "-q"], cwd=str(dest), check=True, capture_output=True)
        subprocess.run(
            [git_bin, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"],
            cwd=str(dest), check=True, capture_output=True,
        )
        subprocess.run(
            [git_bin, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i"],
            cwd=str(dest), check=True, capture_output=True,
        )
        return {"op": op, "repo": repo, "dest": str(dest)}

    return fake


def test_write_file_ancestor_walk_when_resolve_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nearest existing ancestor must be the clone root so the returned path keeps
    # the original parts (see _validate_write_path's empty-landed branch).
    fake = _fake_clone(files={"a.txt": "k\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            orig = Path.resolve

            def fake_resolve(self: Path, *args: object, **kwargs: object) -> Path:
                strict = bool(kwargs.get("strict", False) or (args[0] if args else False))
                text = str(self).replace("\\", "/")
                if "brand/new/file.txt" in text and not strict:
                    raise OSError("simulated resolve failure")
                if ("/brand/new" in text or text.endswith("/brand") or text.endswith("\\brand")) and strict:
                    raise OSError("missing intermediate")
                return orig(self, *args, **kwargs)

            monkeypatch.setattr(Path, "resolve", fake_resolve)
            result = tools.write_file("brand/new/file.txt", "created\n")
            assert result["target"] == "brand/new/file.txt"


def test_write_file_denies_when_resolve_lands_outside_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    fake = _fake_clone(files={"a.txt": "in\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            orig = Path.resolve

            def fake_resolve(self: Path, *args: object, **kwargs: object) -> Path:
                if self.name == "evil.txt":
                    return outside / "evil.txt"
                return orig(self, *args, **kwargs)

            monkeypatch.setattr(Path, "resolve", fake_resolve)
            with pytest.raises(AgenticError, match="escaped the clone root"):
                tools.write_file("evil.txt", "x\n")


def test_write_file_denies_when_resolve_lands_in_git_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_clone(files={"a.txt": "in\n"})
    with patch.object(repo_workspace, "run_read", side_effect=fake):
        with RepoWorkspaceTools.clone(_cfg(tmp_path, monkeypatch)) as tools:
            git_config = (Path(tools.worktree) / ".git" / "config").resolve()
            orig = Path.resolve

            def fake_resolve(self: Path, *args: object, **kwargs: object) -> Path:
                if self.name == "sneaky":
                    return git_config
                return orig(self, *args, **kwargs)

            monkeypatch.setattr(Path, "resolve", fake_resolve)
            with pytest.raises(AgenticError, match="\\.git directory"):
                tools.write_file("sneaky", "x\n")


# --- deepagent_github/subagents.py --------------------------------------------


def test_build_subagent_specs_requires_unique_tool_names() -> None:
    def shared() -> None:
        return None

    with pytest.raises(AgenticError, match="unique"):
        build_subagent_specs(model=object(), tool_callables=(shared, shared), interrupt_on={})


def test_build_subagent_specs_requires_at_least_one_wired_tool() -> None:
    def unrelated_tool() -> None:
        return None

    with pytest.raises(AgenticError, match="no wired allowed tools"):
        build_subagent_specs(model=object(), tool_callables=(unrelated_tool,), interrupt_on={})


# --- gh_client.py -------------------------------------------------------------


def test_check_gh_version_retries_timeout_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    check_gh_version.cache_clear()
    calls = {"n": 0}

    def run(*_a: object, **_k: object) -> SimpleNamespace:
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(cmd="gh", timeout=10)
        return SimpleNamespace(stdout="gh version 2.55.0 (2024-08-21)\n", stderr="", returncode=0)

    monkeypatch.setattr("agentic.gh_client.shutil.which", lambda _n: "/usr/bin/gh")
    monkeypatch.setattr("agentic.gh_client.subprocess.run", run)
    monkeypatch.setattr("agentic.gh_client.time.sleep", lambda _s: None)
    assert check_gh_version(retries=1) == (2, 55, 0)
    assert calls["n"] == 2


def test_check_gh_version_timeout_exhausted_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    check_gh_version.cache_clear()

    def run(*_a: object, **_k: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(cmd="gh", timeout=10)

    monkeypatch.setattr("agentic.gh_client.shutil.which", lambda _n: "/usr/bin/gh")
    monkeypatch.setattr("agentic.gh_client.subprocess.run", run)
    monkeypatch.setattr("agentic.gh_client.time.sleep", lambda _s: None)
    with pytest.raises(GhVersionError, match="timed out"):
        check_gh_version(retries=1)


def test_check_gh_version_oserror_is_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    check_gh_version.cache_clear()

    def run(*_a: object, **_k: object) -> SimpleNamespace:
        raise OSError("cannot execute")

    monkeypatch.setattr("agentic.gh_client.shutil.which", lambda _n: "/usr/bin/gh")
    monkeypatch.setattr("agentic.gh_client.subprocess.run", run)
    from utils.errors import GhNotInstalledError

    with pytest.raises(GhNotInstalledError, match="Could not execute"):
        check_gh_version(retries=0)


def test_build_read_argv_rejects_non_integer_number_and_limit() -> None:
    with pytest.raises(AgenticError, match="'number' must be an integer"):
        build_read_argv("pr_view", "owner/repo", number="abc")  # type: ignore[arg-type]
    with pytest.raises(AgenticError, match="'limit' must be an integer"):
        build_read_argv("pr_list", "owner/repo", limit="abc")  # type: ignore[arg-type]


def test_build_issue_list_argv() -> None:
    argv = build_read_argv("issue_list", "owner/repo", limit=5)
    assert argv[1:3] == ["issue", "list"]
    assert argv[argv.index("--limit") + 1] == "5"


# --- harness_optimizer/runners/github_coding_runner.py ------------------------


def test_safe_child_denies_when_resolve_escapes_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "x.py").write_text("x", encoding="utf-8")
    outside = tmp_path / "out"
    outside.mkdir()
    orig = Path.resolve

    def fake_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        if self.name == "x.py":
            return outside
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    with pytest.raises(AgenticError, match="escaped"):
        _safe_child(root, "x.py")


def test_github_coding_evaluation_to_dict() -> None:
    report = RunReport("v1", train_passed=True, holdout_passed=True, score=0.5)
    evaluation = GitHubCodingEvaluation(report=report, context={"k": 1}, findings=())
    assert evaluation.to_dict()["variant_id"] == "v1"
    assert evaluation.to_dict()["selected_commands"] == []


def test_fetch_github_task_context_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentic.harness_optimizer.runners import github_coding_runner

    cfg = AgenticConfig(repo="owner/repo", mode="read")
    monkeypatch.setattr(github_coding_runner, "fetch_pr_context", lambda _c, n: {"pr": n})
    monkeypatch.setattr(github_coding_runner, "fetch_issue_context", lambda _c, n: {"issue": n})
    monkeypatch.setattr(github_coding_runner, "fetch_repo_context", lambda _c: {"repo": True})
    with pytest.raises(AgenticError, match="either an issue or a PR"):
        fetch_github_task_context(cfg, issue_number=1, pr_number=2)
    assert fetch_github_task_context(cfg, pr_number=9) == {"pr": 9}
    assert fetch_github_task_context(cfg, issue_number=3) == {"issue": 3}
    assert fetch_github_task_context(cfg) == {"repo": True}


def test_build_optional_deepagent_routes_through_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentic.harness_optimizer.runners import github_coding_runner as gcr

    workspace = build_proposer_workspace(tmp_path / "runs", _experiment(), "variant_1", audit=False)
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "planner.py").write_text("def render():\n    pass\n", encoding="utf-8")
    runner = GitHubCodingRunner(
        fixture_repo=fixture,
        workspace=workspace,
        cases=(FixtureCase("case-1", "train_visible", "planner.py", "def render"),),
        cfg={"logging": {"audit_file": str(tmp_path / "a.jsonl"), "audit_fields": {}}, "policy": {"privacy": {}}},
    )
    seen: dict[str, object] = {}

    def fake_build(*_a: object, **kwargs: object) -> DeepAgentBuildResult:
        seen.update(kwargs)
        return DeepAgentBuildResult(False, "skipped", "fixture", (), ())

    monkeypatch.setattr(gcr, "build_deepagent_github", fake_build)
    result = runner.build_optional_deepagent(AgenticConfig(repo="owner/repo", mode="read"))
    assert result.status == "skipped"
    assert "workspace_tools" in seen


# --- harness_optimizer/mcp/tools.py -------------------------------------------


def test_contains_file_not_found_falls_back_to_parent(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert _contains(root, root / "missing.txt") is True


def test_contains_write_target_raises_at_filesystem_root(tmp_path: Path) -> None:
    # Walked-to-root FileNotFoundError is not "Z:" (POSIX treats that as cwd).
    class _MissingRoot:
        def resolve(self, strict: bool = False) -> Path:
            raise FileNotFoundError("missing")

        @property
        def parent(self) -> _MissingRoot:
            return self

    with pytest.raises(FileNotFoundError):
        _contains_write_target(tmp_path, _MissingRoot())  # type: ignore[arg-type]


def test_workspace_read_denies_when_contains_reports_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = {"logging": {"audit_file": str(tmp_path / "a.jsonl"), "audit_fields": {}}, "policy": {"privacy": {}}}
    workspace = build_proposer_workspace(tmp_path / "runs", _experiment(), "variant_1", cfg=cfg)
    (workspace.current_dir / "note.md").write_text("hi", encoding="utf-8")
    tools = ProposerWorkspaceTools(workspace, cfg=cfg)
    monkeypatch.setattr(mcp_tools, "_contains", lambda *_a, **_k: False)
    with pytest.raises(AgenticError, match="escaped root"):
        tools.read_file("current/note.md")


def test_workspace_read_denies_resolved_holdout_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = {"logging": {"audit_file": str(tmp_path / "a.jsonl"), "audit_fields": {}}, "policy": {"privacy": {}}}
    workspace = build_proposer_workspace(tmp_path / "runs", _experiment(), "variant_1", cfg=cfg)
    secret = workspace.holdout_hidden_dir / "secret.md"
    secret.write_text("secret", encoding="utf-8")
    peek = workspace.current_dir / "peek.md"
    peek.write_text("x", encoding="utf-8")
    tools = ProposerWorkspaceTools(workspace, cfg=cfg)
    holdout = workspace.holdout_hidden_dir.resolve()
    orig = Path.resolve

    def fake_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        if self.name == "peek.md":
            return secret.resolve()
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    # Keep root containment true so the holdout check is the one that fires.
    monkeypatch.setattr(mcp_tools, "_contains", lambda *_a, **_k: True)
    with pytest.raises(AgenticError, match="holdout_hidden"):
        tools.read_file("current/peek.md")
    assert holdout in secret.resolve().parents or secret.resolve() == holdout


def test_workspace_write_denies_when_contains_write_target_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = {"logging": {"audit_file": str(tmp_path / "a.jsonl"), "audit_fields": {}}, "policy": {"privacy": {}}}
    workspace = build_proposer_workspace(tmp_path / "runs", _experiment(), "variant_1", cfg=cfg)
    tools = ProposerWorkspaceTools(workspace, cfg=cfg)
    monkeypatch.setattr(mcp_tools, "_contains_write_target", lambda *_a, **_k: False)
    with pytest.raises(AgenticError, match="escaped current"):
        tools.write_current_file("x.md", "body")


def test_workspace_write_wraps_oserror_as_agentic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = {"logging": {"audit_file": str(tmp_path / "a.jsonl"), "audit_fields": {}}, "policy": {"privacy": {}}}
    workspace = build_proposer_workspace(tmp_path / "runs", _experiment(), "variant_1", cfg=cfg)
    tools = ProposerWorkspaceTools(workspace, cfg=cfg)
    monkeypatch.setattr(mcp_tools, "_contains_write_target", lambda *_a, **_k: True)
    monkeypatch.setattr(Path, "exists", lambda self: (_ for _ in ()).throw(OSError("stat failed")))
    with pytest.raises(AgenticError, match="not accessible"):
        tools.write_current_file("boom.md", "body")


# --- harness_optimizer/core.py ------------------------------------------------


def test_core_validation_and_to_dict_branches() -> None:
    with pytest.raises(AgenticError, match="editable must be a boolean"):
        Surface("s", SurfaceType.REGISTRY_SKILL, "p.md", editable="yes")  # type: ignore[arg-type]
    with pytest.raises(AgenticError, match="surfaces must not be empty"):
        Experiment("exp", "workspace", ())
    surface = Surface("s", SurfaceType.REGISTRY_SKILL, "p.md")
    exp = Experiment("exp", "workspace", (surface,), train_visible=("c1",))
    assert exp.to_dict()["experiment_id"] == "exp"
    with pytest.raises(AgenticError, match="pass fields must be booleans"):
        RunReport("v", train_passed="yes", holdout_passed=True, score=0.1)  # type: ignore[arg-type]
    with pytest.raises(AgenticError, match="score must be numeric"):
        RunReport("v", train_passed=True, holdout_passed=True, score=True)  # type: ignore[arg-type]
    decision = CandidateDecision(True, "ok", 0.1, 0.2)
    assert decision.to_dict()["accepted"] is True
    baseline = RunReport("b", True, True, 0.1)
    candidate = RunReport("c", True, True, 0.9)
    refused = decide_candidate(
        baseline, candidate, allowed_surface_ids=frozenset(), proposal_present=False,
    )
    assert refused.accepted is False
    assert "proposal_missing" in refused.rejected_gates


# --- harness_optimizer/proposer.py --------------------------------------------


def test_proposer_workspace_to_dict_and_relative_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = build_proposer_workspace("runs_rel", _experiment(), "variant_1", audit=False)
    payload = workspace.to_dict()
    assert Path(payload["root"]).is_dir()
    assert "proposal_path" in payload


def test_resolve_child_rejects_escaped_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    orig = Path.resolve

    def fake_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        if self.name == "escaped":
            return outside / "escaped"
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    with pytest.raises(AgenticError, match="escaped root"):
        _resolve_child(root, "escaped")


# --- harness_optimizer/model_adapter.py ---------------------------------------


def test_local_proposer_rejects_blank_model_and_empty_content(tmp_path: Path) -> None:
    cfg = {"logging": {"audit_file": str(tmp_path / "a.jsonl"), "audit_fields": {}}, "policy": {"privacy": {}}}
    blank = LocalProposerClient(
        base_url="http://localhost:1234/v1",
        model="   ",
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"choices": []})),
    )
    try:
        with pytest.raises(AgenticError, match="model must be configured"):
            blank.invoke(system_prompt="s", user_prompt="u", cfg=cfg)
    finally:
        blank.close()

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]})

    client = LocalProposerClient(
        base_url="http://localhost:1234/v1",
        model="local-test",
        reasoning_effort="low",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AgenticError, match="empty content"):
            client.invoke(system_prompt="s", user_prompt="u", cfg=cfg)
    finally:
        client.close()
    assert seen["body"]["reasoning_effort"] == "low"


# --- harness_optimizer/runners/base_runner.py ---------------------------------


def test_mock_runner_rejects_undeclared_holdout_case() -> None:
    runner = MockHarnessRunner((MockRunnerCase("other-h", "holdout_hidden", True, 1.0),))
    with pytest.raises(AgenticError, match="holdout_hidden"):
        runner.run(_experiment(), Variant("v", (), "p.md", "a"))


# --- harness_optimizer/governance.py ------------------------------------------


def test_detect_visible_case_hardcoding_short_circuits_on_empty() -> None:
    assert detect_visible_case_hardcoding("", ("case-1",)) is False
    assert detect_visible_case_hardcoding("has case-1", ()) is False


def test_inspect_code_shape_short_circuits_when_disabled_or_empty() -> None:
    assert inspect_code_shape("anything", enabled=False) == ()
    assert inspect_code_shape("", enabled=True) == ()


# --- real_repo_loop.py --------------------------------------------------------


def test_fs_equiv_and_protected_path_edge_cases() -> None:
    assert _fs_equiv_path("foo/./bar//baz") == "foo/bar/baz"
    assert _matches_protected_path(".", ("tests/",)) is False
    assert _matches_protected_path("tests/x.py", ("/",)) is False
    assert _sha256("abc") == __import__("hashlib").sha256(b"abc").hexdigest()


def test_generate_plan_happy_path_and_guards(tmp_path: Path) -> None:
    from tests.test_agentic_real_repo_loop import _chat_response, _loop_client

    cfg = {"logging": {"audit_file": str(tmp_path / "a.jsonl"), "audit_fields": {}}, "policy": {"privacy": {}}}
    with pytest.raises(AgenticError, match="plan instruction"):
        generate_plan(
            _loop_client(lambda _r: _chat_response("plan")),
            instruction=" ",
            cfg=cfg,
        )
    with pytest.raises(AgenticError, match="max_tokens"):
        generate_plan(
            _loop_client(lambda _r: _chat_response("plan")),
            instruction="do it",
            max_tokens=0,
            cfg=cfg,
        )

    client = _loop_client(lambda _r: _chat_response("step one\nstep two"))
    try:
        plan = generate_plan(client, instruction="do it", context="PR body", cfg=cfg)
    finally:
        client.close()
    assert "step one" in plan

    class EmptyClient:
        def invoke(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(content="   ")

        def close(self) -> None:
            return None

    with pytest.raises(AgenticError, match="empty plan"):
        generate_plan(EmptyClient(), instruction="do it", cfg=cfg)  # type: ignore[arg-type]

    long_body = "p" * (_MAX_PLAN_CHARS + 50)
    long_client = _loop_client(lambda _r: _chat_response(long_body))
    try:
        truncated = generate_plan(long_client, instruction="do it", cfg=cfg)
    finally:
        long_client.close()
    assert "truncated" in truncated


def test_run_real_repo_loop_rejects_bad_max_tokens_and_handles_unslop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_agentic_real_repo_loop import (
        _MARKER_CHECK,
        _RIGHT_BLOCK,
        _chat_response,
        _cloned_tools,
        _loop_client,
    )

    with _cloned_tools(tmp_path, monkeypatch) as tools:
        client = _loop_client(lambda _r: _chat_response(_RIGHT_BLOCK))
        try:
            with pytest.raises(AgenticError, match="max_tokens"):
                run_real_repo_loop(
                    tools,
                    client,
                    instruction="x",
                    checks=[_MARKER_CHECK],
                    branch_name="agent/x",
                    commit_message="x",
                    max_iterations=1,
                    reason="test",
                    confirm=True,
                    max_tokens=0,
                )

            def boom(_content: str, _files: object, _step: int) -> dict[str, object]:
                raise RuntimeError("probe failed")

            result = run_real_repo_loop(
                tools,
                client,
                instruction="Add target.txt with the expected marker",
                checks=[_MARKER_CHECK],
                branch_name="agent/unslop-boom",
                commit_message="x",
                max_iterations=1,
                reason="test",
                confirm=True,
                unslop_probe=boom,
            )
            assert result.accepted is True

            seen_nudge: dict[str, object] = {}

            def nudge(content: str, _files: object, step: int) -> dict[str, object]:
                seen_nudge["step"] = step
                seen_nudge["content_len"] = len(content)
                return {"nudge": "please tighten the diff"}

            bad_client = _loop_client(
                lambda _r: _chat_response("=== FILE target.txt ===\nwrong\n=== END FILE ===\n"),
            )
            try:
                exhausted = run_real_repo_loop(
                    tools,
                    bad_client,
                    instruction="Add target.txt with the expected marker",
                    checks=[_MARKER_CHECK],
                    branch_name="agent/unslop-nudge",
                    commit_message="x",
                    max_iterations=1,
                    reason="test",
                    confirm=True,
                    unslop_probe=nudge,
                )
            finally:
                bad_client.close()
            assert exhausted.accepted is False
            assert seen_nudge["step"] == 1
            assert exhausted.iterations[0].decision.accepted is False
        finally:
            client.close()
