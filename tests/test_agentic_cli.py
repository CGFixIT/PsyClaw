"""Tests for agentic.cli -- subcommands, disabled no-op, exit codes."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

import utils.logger as logger_mod
from agentic import cli
from utils.errors import (
    AgenticConfigError,
    AgenticError,
    AgenticWriteRefused,
    GhNotInstalledError,
    GhVersionError,
    SkillRegistryError,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_config(tmp_path: Path, *, enabled: bool) -> str:
    registry_path = f"data/agentic/_pytest_cli_{uuid.uuid4().hex}.json"
    cfg = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
        "policy": {"prompt_filter": {"banned_patterns": ["ignore previous instructions"]},
                   "privacy": {}},
        "agentic": {
            "enabled": enabled,
            "repo": "CGFixIT/CyClaw",
            "mode": "read",
            "writes_enabled": False,
            "gh_min_version": "2.40.0",
            "registry_path": registry_path,
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def _reset():
    logger_mod.reset_config_cache()
    yield
    logger_mod.reset_config_cache()
    for path in (REPO_ROOT / "data" / "agentic").glob("_pytest_cli_*.json*"):
        path.unlink(missing_ok=True)


def test_status_runs(tmp_path, capsys):
    code = cli.main(["--config", _write_config(tmp_path, enabled=False), "status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "CGFixIT/CyClaw" in out
    assert "registry_version" in out


def test_context_disabled_is_noop(tmp_path, capsys):
    # enabled=false -> clean exit 0 without ever touching gh.
    code = cli.main(["--config", _write_config(tmp_path, enabled=False), "context", "--repo"])
    assert code == 0
    assert "disabled" in capsys.readouterr().out.lower()


def test_bad_config_returns_env_exit(tmp_path):
    cfg = {"logging": {"audit_file": str(tmp_path / "a.jsonl")}}  # no agentic block
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert cli.main(["--config", str(path), "status"]) == 3


def test_apply_skill_requires_confirm(tmp_path):
    code = cli.main(["--config", _write_config(tmp_path, enabled=True),
                     "apply-skill", "--name", "x", "--desc", "d", "--body", "safe body",
                     "--reason", "r"])
    assert code == 4  # EXIT_REFUSED (no --confirm)


def test_propose_skill_runs(tmp_path, capsys):
    code = cli.main(["--config", _write_config(tmp_path, enabled=True),
                     "propose-skill", "--name", "x", "--desc", "d",
                     "--body", "a safe body", "--reason", "r"])
    assert code == 0
    assert "proposed" in capsys.readouterr().out


def test_propose_skill_disabled_is_noop(tmp_path, capsys):
    # enabled=false -> registry op is a clean no-op (matches context).
    code = cli.main(["--config", _write_config(tmp_path, enabled=False),
                     "propose-skill", "--name", "x", "--desc", "d",
                     "--body", "a safe body", "--reason", "r"])
    assert code == 0
    assert "disabled" in capsys.readouterr().out.lower()


def test_apply_skill_disabled_does_not_write(tmp_path, capsys):
    # The master switch must block the registry WRITE even with --confirm + reason
    # + a clean (injection-free) body. agentic.enabled=false means the layer is off,
    # so the registry JSON must never be created. registry_path is validated to live
    # under the repo data/ tree, so use a unique repo-relative path (the gate fires
    # before any SkillRegistry is constructed, so nothing is ever written there).
    rel = "data/agentic/_test_disabled_noop_registry.json"
    registry = REPO_ROOT / rel
    registry.unlink(missing_ok=True)  # defensive: never pre-exist
    cfg = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
        "policy": {"prompt_filter": {"banned_patterns": ["ignore previous instructions"]},
                   "privacy": {}},
        "agentic": {
            "enabled": False, "repo": "CGFixIT/CyClaw", "mode": "read",
            "writes_enabled": False, "gh_min_version": "2.40.0",
            "registry_path": rel,
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    try:
        code = cli.main(["--config", str(path), "apply-skill", "--name", "demo",
                         "--desc", "d", "--body", "a safe body", "--reason", "r", "--confirm"])
        assert code == 0
        assert "disabled" in capsys.readouterr().out.lower()
        assert not registry.exists()  # nothing was written while the layer is off
    finally:
        registry.unlink(missing_ok=True)


def test_unknown_subcommand_errors(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["--config", _write_config(tmp_path, enabled=True), "bogus"])


# ── exit codes are an API, so every failure must land in the documented set ──
# utils/ops_runner.py's _AGENTIC_LABELS maps 0/2/3/4 to
# ok/failed/env_config/write_refused and EVERYTHING ELSE to "unknown", which is
# what /ops/agentic then reports to the console. An untyped exception escaping
# to a traceback exits 1 and lands in that "unknown" bucket.

@pytest.mark.parametrize(
    "entry, why",
    [
        ({"name": "lint", "argv": ["ruff"], "timeout_sec": "soon"}, "non-numeric timeout_sec"),
        ({"name": "lint", "argv": ["ruff"], "timeout_sec": None}, "null timeout_sec"),
        ({"name": "lint", "argv": ["ruff"], "timeout_sec": [30]}, "list timeout_sec"),
        ({"name": "", "argv": ["ruff"]}, "empty name reaches Check.__post_init__"),
    ],
)
def test_bad_checks_file_entry_raises_a_typed_error(tmp_path, entry, why):
    """int() and Check.__post_init__ both raise bare ValueError/TypeError.

    Those escape every `except AgenticError` between _load_checks_file and
    main(), so an operator-supplied checks file could exit 1 with a traceback.
    The checks file is operator input, not model output -- a typo in it is an
    ordinary mistake, and it should report as a failure rather than a crash.
    """
    import json

    path = tmp_path / "checks.json"
    path.write_text(json.dumps([entry]), encoding="utf-8")
    with pytest.raises(AgenticError) as excinfo:
        cli._load_checks_file(str(path))
    assert "invalid check entry" in excinfo.value.message, why


def test_valid_checks_file_entry_still_loads(tmp_path):
    """Mutation guard: the conversion above must not swallow good input."""
    import json

    path = tmp_path / "checks.json"
    path.write_text(json.dumps([{"name": "lint", "argv": ["ruff", "check"], "timeout_sec": 30}]),
                    encoding="utf-8")
    checks = cli._load_checks_file(str(path))
    assert len(checks) == 1
    assert checks[0].name == "lint"
    assert checks[0].argv == ("ruff", "check")
    assert checks[0].timeout_sec == 30


@pytest.mark.parametrize(
    "exc, expected",
    [
        (AgenticError("persist failed"), cli.EXIT_FAIL),
        (AgenticWriteRefused("refused"), cli.EXIT_REFUSED),
        (AgenticConfigError("bad config"), cli.EXIT_ENV),
    ],
)
def test_main_maps_typed_errors_onto_documented_exit_codes(tmp_path, exc, expected, monkeypatch):
    """A typed error escaping a subcommand must not become exit 1.

    Thirteen save_run call sites and one Path.write_text sit outside any try in
    this module. real_repo_run_store.save_run's own comment states its
    OSError-to-AgenticError conversion exists so the failure will not "escape
    past every caller's `except AgenticError` in agentic/cli.py, reach main()
    uncaught, and exit 1" -- which is exactly what happened, because main() had
    no handler to land in. Catching at the dispatch point covers the whole
    class rather than the sites that happen to be known today.
    """
    def boom(_args):
        raise exc

    monkeypatch.setattr(cli, "cmd_status", boom)
    assert cli.main(["--config", _write_config(tmp_path, enabled=True), "status"]) == expected


def test_main_does_not_mask_an_untyped_bug(tmp_path, monkeypatch):
    """The handler is deliberately narrow.

    Catching bare Exception would turn a genuine bug into a tidy exit 2 and
    hide it. Only the typed hierarchy is classified; anything else still
    surfaces as a traceback.
    """
    def bug(_args):
        raise RuntimeError("a real bug, not an operational failure")

    monkeypatch.setattr(cli, "cmd_status", bug)
    with pytest.raises(RuntimeError, match="a real bug"):
        cli.main(["--config", _write_config(tmp_path, enabled=True), "status"])


def test_main_wires_logging_before_dispatch(tmp_path, monkeypatch):
    """main() must call setup_logging before dispatch, not leave it uncalled.

    Before this fix, agentic.cli never called setup_logging: every agentic.*
    logger reached only Python's stderr last-resort handler regardless of
    config.yaml's logging.log_file. Does not re-test setup_logging's own
    mechanics (covered by test_logger.py) -- only that THIS entrypoint calls
    it, with the loaded config, before the subcommand runs.
    """
    log_path = tmp_path / "cyclaw.log"
    registry_path = f"data/agentic/_pytest_cli_{uuid.uuid4().hex}.json"
    doc = {
        "logging": {
            "audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {},
            "log_file": str(log_path), "capture_third_party": True, "third_party_level": "INFO",
        },
        "policy": {"prompt_filter": {"banned_patterns": ["ignore previous instructions"]}, "privacy": {}},
        "agentic": {
            "enabled": False, "repo": "CGFixIT/CyClaw", "mode": "read",
            "writes_enabled": False, "gh_min_version": "2.40.0", "registry_path": registry_path,
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    monkeypatch.setattr(logger_mod, "_logging_initialized", False)
    real_root = logging.getLogger()
    before = list(real_root.handlers)
    try:
        assert cli.main(["--config", str(cfg_path), "status"]) == 0

        logging.getLogger("agentic.wiring_regression_test").warning("agentic-cli-wiring-marker")
        for handler in real_root.handlers:
            handler.flush()
        assert log_path.exists(), "main() did not call setup_logging with the loaded config"
        assert "agentic-cli-wiring-marker" in log_path.read_text(encoding="utf-8")
    finally:
        for handler in list(real_root.handlers):
            if handler not in before:
                real_root.removeHandler(handler)
                handler.close()
        logger_mod._logging_initialized = False


def test_main_maps_a_broken_log_file_to_env_exit_not_a_crash(tmp_path, monkeypatch):
    """setup_logging's own OSError must map onto the exit-code API.

    Before this fix, main() called setup_logging(_get_config(args.config))
    OUTSIDE the dispatch try -- a misconfigured logging.log_file (here: it
    names a directory, so opening it as a file raises IsADirectoryError)
    escaped straight out of main() as an uncaught traceback with exit code
    1, which _AGENTIC_LABELS has no entry for, so ops_runner reported
    "unknown" instead of the environment/config problem this actually is
    (codex review on #1239).
    """
    registry_path = f"data/agentic/_pytest_cli_{uuid.uuid4().hex}.json"
    doc = {
        "logging": {
            "audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {},
            "log_file": str(tmp_path),  # a directory, not a file
        },
        "policy": {"prompt_filter": {"banned_patterns": ["ignore previous instructions"]}, "privacy": {}},
        "agentic": {
            "enabled": False, "repo": "CGFixIT/CyClaw", "mode": "read",
            "writes_enabled": False, "gh_min_version": "2.40.0", "registry_path": registry_path,
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    monkeypatch.setattr(logger_mod, "_logging_initialized", False)
    cyclaw_logger = logging.getLogger("cyclaw")
    agentic_logger = logging.getLogger("agentic")
    before_cyclaw = list(cyclaw_logger.handlers)
    before_agentic = list(agentic_logger.handlers)
    try:
        assert cli.main(["--config", str(cfg_path), "status"]) == cli.EXIT_ENV
    finally:
        for logger_obj, before in ((cyclaw_logger, before_cyclaw), (agentic_logger, before_agentic)):
            for handler in list(logger_obj.handlers):
                if handler not in before:
                    logger_obj.removeHandler(handler)
                    handler.close()
        logger_mod._logging_initialized = False


def test_main_maps_a_malformed_logging_block_to_env_exit_not_a_crash(tmp_path, monkeypatch):
    """setup_logging's own AttributeError/TypeError must map onto the exit-code API.

    A malformed logging: block (here: a bool instead of a mapping) makes
    setup_logging's log_cfg.get(...) raise AttributeError before any handler
    is attached -- this must not escape main() as an uncaught traceback with
    exit code 1, which _AGENTIC_LABELS has no entry for (codex review on
    #1239, fourth round).
    """
    registry_path = f"data/agentic/_pytest_cli_{uuid.uuid4().hex}.json"
    doc = {
        "logging": True,  # malformed: not a mapping
        "policy": {"prompt_filter": {"banned_patterns": ["ignore previous instructions"]}, "privacy": {}},
        "agentic": {
            "enabled": False, "repo": "CGFixIT/CyClaw", "mode": "read",
            "writes_enabled": False, "gh_min_version": "2.40.0", "registry_path": registry_path,
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    monkeypatch.setattr(logger_mod, "_logging_initialized", False)
    cyclaw_logger = logging.getLogger("cyclaw")
    agentic_logger = logging.getLogger("agentic")
    before_cyclaw = list(cyclaw_logger.handlers)
    before_agentic = list(agentic_logger.handlers)
    try:
        assert cli.main(["--config", str(cfg_path), "status"]) == cli.EXIT_ENV
    finally:
        for logger_obj, before in ((cyclaw_logger, before_cyclaw), (agentic_logger, before_agentic)):
            for handler in list(logger_obj.handlers):
                if handler not in before:
                    logger_obj.removeHandler(handler)
                    handler.close()
        logger_mod._logging_initialized = False


def test_main_maps_an_invalid_log_path_to_env_exit_not_a_crash(tmp_path, monkeypatch):
    """setup_logging's own ValueError must map onto the exit-code API.

    logging.log_file containing an embedded NUL byte makes
    logging.FileHandler raise ValueError -- a third exception type this
    narrow config-load-and-logging-init call site can raise, beyond the
    OSError and AttributeError/TypeError already handled. This must not
    escape main() as an uncaught traceback with exit code 1, which
    _AGENTIC_LABELS has no entry for (codex review on #1239, fifth round).
    """
    registry_path = f"data/agentic/_pytest_cli_{uuid.uuid4().hex}.json"
    doc = {
        "logging": {
            "audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {},
            "log_file": "bad\x00path",  # embedded NUL -> ValueError
        },
        "policy": {"prompt_filter": {"banned_patterns": ["ignore previous instructions"]}, "privacy": {}},
        "agentic": {
            "enabled": False, "repo": "CGFixIT/CyClaw", "mode": "read",
            "writes_enabled": False, "gh_min_version": "2.40.0", "registry_path": registry_path,
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    monkeypatch.setattr(logger_mod, "_logging_initialized", False)
    cyclaw_logger = logging.getLogger("cyclaw")
    agentic_logger = logging.getLogger("agentic")
    before_cyclaw = list(cyclaw_logger.handlers)
    before_agentic = list(agentic_logger.handlers)
    try:
        assert cli.main(["--config", str(cfg_path), "status"]) == cli.EXIT_ENV
    finally:
        for logger_obj, before in ((cyclaw_logger, before_cyclaw), (agentic_logger, before_agentic)):
            for handler in list(logger_obj.handlers):
                if handler not in before:
                    logger_obj.removeHandler(handler)
                    handler.close()
        logger_mod._logging_initialized = False


def test_status_ok_gh_and_registry_error(tmp_path, capsys):
    with (
        patch("agentic.gh_client.check_gh_version", return_value=(2, 50, 0)),
        patch("agentic.registry.SkillRegistry") as reg_cls,
    ):
        reg_cls.side_effect = SkillRegistryError("registry broken")
        code = cli.main(["--config", _write_config(tmp_path, enabled=True), "status"])
    assert code == 0
    out = capsys.readouterr()
    assert "[OK  ] gh 2.50.0" in out.out
    assert "registry broken" in out.err


def test_context_bad_config_is_env(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"logging": {"audit_file": str(tmp_path / "a.jsonl")}}), encoding="utf-8")
    assert cli.main(["--config", str(path), "context", "--repo"]) == cli.EXIT_ENV


@pytest.mark.parametrize(
    "argv_extra, fetch_name",
    [
        (["--issue", "7"], "fetch_issue_context"),
        (["--repo"], "fetch_repo_context"),
        (["--pr", "3"], "fetch_pr_context"),
    ],
)
def test_context_fetch_paths_and_errors(tmp_path, argv_extra, fetch_name):
    cfg = _write_config(tmp_path, enabled=True)
    with patch(f"agentic.context.{fetch_name}", return_value={"ok": True, "kind": fetch_name}) as fetch:
        assert cli.main(["--config", cfg, "context", *argv_extra]) == 0
        fetch.assert_called_once()

    with patch(f"agentic.context.{fetch_name}", side_effect=GhNotInstalledError("no gh")):
        assert cli.main(["--config", cfg, "context", *argv_extra]) == cli.EXIT_ENV

    with patch(f"agentic.context.{fetch_name}", side_effect=AgenticError("gh failed")):
        assert cli.main(["--config", cfg, "context", *argv_extra]) == cli.EXIT_FAIL


def test_propose_skill_reads_body_file(tmp_path, capsys):
    body = tmp_path / "body.md"
    body.write_text("safe body from file", encoding="utf-8")
    code = cli.main(
        [
            "--config",
            _write_config(tmp_path, enabled=True),
            "propose-skill",
            "--name",
            "x",
            "--desc",
            "d",
            "--body-file",
            str(body),
            "--reason",
            "r",
        ]
    )
    assert code == 0
    assert "proposed" in capsys.readouterr().out


def test_blocking_context_findings_non_list():
    assert cli._blocking_context_findings({"governance_findings": {"not": "a list"}}) == []
    assert cli._blocking_context_findings({"governance_findings": []}) == []


def test_resolve_cloud_provider_gates(tmp_path):
    import argparse

    cfg_path = _write_config(tmp_path, enabled=True)
    from agentic.config import load_agentic_config

    cfg = load_agentic_config(cfg_path)
    app_cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}}
    args = argparse.Namespace(provider="grok", confirm_online=False)

    with patch.object(cfg.deepagent_github, "cloud_provider", return_value=None):
        assert cli._resolve_cloud_provider_gates(cfg, args, app_cfg) == cli.EXIT_ENV

    with (
        patch.object(cfg.deepagent_github, "cloud_provider", return_value=MagicMock()),
        patch("agentic.deepagent_github.model_adapter.cloud_key_available", return_value=False),
    ):
        assert cli._resolve_cloud_provider_gates(cfg, args, app_cfg) == cli.EXIT_ENV

    with (
        patch.object(cfg.deepagent_github, "cloud_provider", return_value=MagicMock()),
        patch("agentic.deepagent_github.model_adapter.cloud_key_available", return_value=True),
    ):
        assert cli._resolve_cloud_provider_gates(cfg, args, app_cfg) == cli.EXIT_REFUSED
        args.confirm_online = True
        assert cli._resolve_cloud_provider_gates(cfg, args, app_cfg) is None


def _write_deepagent_config(tmp_path: Path, *, enabled: bool = True, deep_enabled: bool = True) -> str:
    registry_path = f"data/agentic/_pytest_cli_{uuid.uuid4().hex}.json"
    cfg = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
        "policy": {
            "prompt_filter": {"banned_patterns": ["ignore previous instructions"]},
            "privacy": {},
        },
        "agentic": {
            "enabled": enabled,
            "repo": "CGFixIT/CyClaw",
            "mode": "read",
            "writes_enabled": False,
            "gh_min_version": "2.40.0",
            "registry_path": registry_path,
            "deepagent_github": {"enabled": deep_enabled},
        },
    }
    path = tmp_path / f"config_deep_{uuid.uuid4().hex}.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path)


def test_deepagent_plan_bad_config_and_disabled(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"logging": {"audit_file": str(tmp_path / "a.jsonl")}}), encoding="utf-8")
    assert cli.main(["--config", str(bad), "deepagent-plan", "--instruction", "x", "--repo"]) == cli.EXIT_ENV

    code = cli.main(
        [
            "--config",
            _write_deepagent_config(tmp_path, enabled=True, deep_enabled=False),
            "deepagent-plan",
            "--instruction",
            "x",
            "--repo",
        ]
    )
    assert code == 0
    assert "disabled" in capsys.readouterr().out.lower()


def test_deepagent_plan_fetch_and_build_errors(tmp_path):
    cfg = _write_deepagent_config(tmp_path, enabled=True, deep_enabled=True)
    argv = ["--config", cfg, "deepagent-plan", "--instruction", "do thing", "--issue", "9"]

    with patch("agentic.context.fetch_issue_context", side_effect=GhVersionError("old gh")):
        assert cli.main(argv) == cli.EXIT_ENV

    with patch("agentic.context.fetch_issue_context", side_effect=AgenticError("fetch boom")):
        assert cli.main(argv) == cli.EXIT_FAIL

    plan = MagicMock()
    build = MagicMock(created=False, status="disabled", reason="x", subagent_names=[], interrupt_on=set())
    with (
        patch("agentic.context.fetch_issue_context", return_value={"governance_findings": []}),
        patch("agentic.deepagent_github.runners.draft_plan", side_effect=AgenticWriteRefused("no write")),
    ):
        assert cli.main(argv) == cli.EXIT_REFUSED

    with (
        patch("agentic.context.fetch_issue_context", return_value={"governance_findings": []}),
        patch("agentic.deepagent_github.runners.draft_plan", return_value=plan),
        patch("agentic.deepagent_github.builder.build_deepagent_github", side_effect=AgenticError("build fail")),
    ):
        assert cli.main(argv) == cli.EXIT_FAIL

    with (
        patch("agentic.context.fetch_issue_context", return_value={"governance_findings": []}),
        patch("agentic.deepagent_github.runners.draft_plan", return_value=plan),
        patch("agentic.deepagent_github.builder.build_deepagent_github", return_value=build),
        patch("agentic.cli.asdict", return_value={"steps": []}),
    ):
        assert cli.main(argv) == 0


def test_local_reasoning_effort_gates():
    cfg = MagicMock()
    cfg.deepagent_github.provider = "grok"
    assert cli._local_reasoning_effort(cfg, {"models": {"local_llm": {"reasoning_effort": "low"}}}) is None

    cfg.deepagent_github.provider = "ollama"
    assert cli._local_reasoning_effort(cfg, None) is None
    assert cli._local_reasoning_effort(cfg, {"models": "bad"}) is None
    with patch("utils.config_validation.resolve_reasoning_effort", return_value="high") as resolve:
        assert cli._local_reasoning_effort(cfg, {"models": {"local_llm": {"reasoning_effort": "high"}}}) == "high"
        resolve.assert_called_once()


def test_deepagent_plan_blocks_on_injection_findings(tmp_path, capsys):
    cfg = _write_deepagent_config(tmp_path, enabled=True, deep_enabled=True)
    findings = [{"rule": "injection", "severity": "high"}]
    with patch(
        "agentic.context.fetch_repo_context",
        return_value={"governance_findings": findings},
    ), patch(
        "agentic.cli._blocking_context_findings",
        return_value=findings,
    ):
        code = cli.main(
            ["--config", cfg, "deepagent-plan", "--instruction", "x", "--repo"],
        )
    assert code == cli.EXIT_FAIL
    assert "injection finding" in capsys.readouterr().err.lower()


def test_status_gh_version_error_still_ok(tmp_path, capsys):
    with (
        patch("agentic.gh_client.check_gh_version", side_effect=GhVersionError("too old")),
        patch("agentic.registry.SkillRegistry") as reg_cls,
    ):
        reg = MagicMock()
        reg.version.return_value = 1
        reg.list_skills.return_value = []
        reg_cls.return_value = reg
        code = cli.main(["--config", _write_config(tmp_path, enabled=True), "status"])
    assert code == 0
    assert "too old" in capsys.readouterr().err


def _write_plan_config(
    tmp_path: Path,
    *,
    enabled: bool = True,
    deep_enabled: bool = True,
    model: str = "local-test-model",
    allow_cloud: bool = False,
    grok_enabled: bool = False,
) -> str:
    registry_path = f"data/agentic/_pytest_cli_{uuid.uuid4().hex}.json"
    cfg = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
        "policy": {
            "prompt_filter": {"banned_patterns": ["ignore previous instructions"]},
            "privacy": {},
        },
        "agentic": {
            "enabled": enabled,
            "repo": "CGFixIT/CyClaw",
            "mode": "write",
            "writes_enabled": True,
            "gh_min_version": "2.40.0",
            "registry_path": registry_path,
            "deepagent_github": {
                "enabled": deep_enabled,
                "model": model,
                "allow_cloud_providers": allow_cloud,
                "providers": {
                    "grok": {"enabled": grok_enabled, "model": "grok-test"},
                    "claude": {"enabled": False, "model": ""},
                },
            },
        },
    }
    path = tmp_path / f"config_plan_{uuid.uuid4().hex}.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path)


def test_real_repo_run_plan_gate_refusals(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"logging": {"audit_file": str(tmp_path / "a.jsonl")}}), encoding="utf-8")
    assert cli.main(["--config", str(bad), "real-repo-run-plan", "--repo", "--instruction", "x"]) == cli.EXIT_ENV

    code = cli.main([
        "--config", _write_plan_config(tmp_path, enabled=False),
        "real-repo-run-plan", "--repo", "--instruction", "x",
    ])
    assert code == 0
    assert "disabled" in capsys.readouterr().out.lower()

    code = cli.main([
        "--config", _write_plan_config(tmp_path, deep_enabled=False),
        "real-repo-run-plan", "--repo", "--instruction", "x",
    ])
    assert code == 0
    assert "disabled" in capsys.readouterr().out.lower()

    code = cli.main([
        "--config", _write_plan_config(tmp_path, model=""),
        "real-repo-run-plan", "--repo", "--instruction", "x",
    ])
    assert code == cli.EXIT_ENV
    assert "model must be configured" in capsys.readouterr().err


def test_real_repo_run_plan_fetch_paths_and_errors(tmp_path, capsys):
    cfg = _write_plan_config(tmp_path)
    bundle = {"governance_findings": []}

    with patch("agentic.context.fetch_pr_context", return_value=bundle), patch(
        "agentic.real_repo_loop.generate_plan", return_value="PLAN",
    ), patch("agentic.harness_optimizer.model_adapter.LocalProposerClient") as client_cls:
        client_cls.return_value.close = MagicMock()
        assert cli.main([
            "--config", cfg, "real-repo-run-plan", "--pr", "7", "--instruction", "do",
        ]) == 0

    with patch("agentic.context.fetch_issue_context", return_value=bundle), patch(
        "agentic.real_repo_loop.generate_plan", return_value="PLAN",
    ), patch("agentic.harness_optimizer.model_adapter.LocalProposerClient") as client_cls:
        client_cls.return_value.close = MagicMock()
        assert cli.main([
            "--config", cfg, "real-repo-run-plan", "--issue", "3", "--instruction", "do",
        ]) == 0

    with patch("agentic.context.fetch_repo_context", side_effect=GhNotInstalledError("no gh")):
        assert cli.main([
            "--config", cfg, "real-repo-run-plan", "--repo", "--instruction", "do",
        ]) == cli.EXIT_ENV

    with patch("agentic.context.fetch_repo_context", side_effect=AgenticError("fetch boom")):
        assert cli.main([
            "--config", cfg, "real-repo-run-plan", "--repo", "--instruction", "do",
        ]) == cli.EXIT_FAIL

    from agentic.context import INJECTION_FINDING_CODE

    findings = [{"code": INJECTION_FINDING_CODE, "field": "body"}]
    with patch("agentic.context.fetch_repo_context", return_value={"governance_findings": findings}):
        assert cli.main([
            "--config", cfg, "real-repo-run-plan", "--repo", "--instruction", "do",
        ]) == cli.EXIT_FAIL
        assert "injection finding" in capsys.readouterr().err.lower()

    with patch("agentic.context.fetch_repo_context", return_value=bundle), patch(
        "agentic.real_repo_loop.generate_plan", side_effect=AgenticError("plan boom"),
    ), patch("agentic.harness_optimizer.model_adapter.LocalProposerClient") as client_cls:
        client_cls.return_value.close = MagicMock()
        assert cli.main([
            "--config", cfg, "real-repo-run-plan", "--repo", "--instruction", "do",
        ]) == cli.EXIT_FAIL


def test_real_repo_run_plan_cloud_provider_path(tmp_path, monkeypatch, capsys):
    cfg = _write_plan_config(tmp_path, allow_cloud=True, grok_enabled=True)
    monkeypatch.setenv("GROK_API_KEY", "test-not-a-real-key")
    bundle = {"governance_findings": []}
    with patch("agentic.context.fetch_repo_context", return_value=bundle), patch(
        "agentic.real_repo_loop.generate_plan", return_value="CLOUD PLAN",
    ), patch("agentic.deepagent_github.chat_client.ChatModelProposerClient") as client_cls:
        client_cls.return_value.close = MagicMock()
        code = cli.main([
            "--config", cfg, "real-repo-run-plan", "--repo", "--instruction", "do",
            "--provider", "grok", "--confirm-online",
        ])
    assert code == 0
    assert "CLOUD PLAN" in capsys.readouterr().out
    client_cls.assert_called_once()


def test_propose_apply_skill_error_paths_and_cmd_test(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"logging": {"audit_file": str(tmp_path / "a.jsonl")}}), encoding="utf-8")
    assert cli.main([
        "--config", str(bad), "propose-skill", "--name", "x", "--desc", "d", "--body", "safe",
    ]) == cli.EXIT_ENV
    assert cli.main([
        "--config", str(bad), "apply-skill", "--name", "x", "--desc", "d", "--body", "safe",
        "--reason", "r", "--confirm",
    ]) == cli.EXIT_ENV

    cfg = _write_config(tmp_path, enabled=True)
    with patch("agentic.registry.SkillRegistry", side_effect=SkillRegistryError("propose boom")):
        assert cli.main([
            "--config", cfg, "propose-skill", "--name", "x", "--desc", "d", "--body", "safe body",
        ]) == cli.EXIT_FAIL

    from utils.errors import PromptInjectionError

    with patch("agentic.registry.SkillRegistry") as reg_cls:
        reg = MagicMock()
        reg.apply_skill.side_effect = PromptInjectionError("blocked")
        reg_cls.return_value = reg
        assert cli.main([
            "--config", cfg, "apply-skill", "--name", "x", "--desc", "d", "--body", "safe body",
            "--reason", "r", "--confirm",
        ]) == cli.EXIT_REFUSED

    with patch("agentic.registry.SkillRegistry") as reg_cls:
        reg = MagicMock()
        reg.apply_skill.side_effect = SkillRegistryError("apply boom")
        reg_cls.return_value = reg
        assert cli.main([
            "--config", cfg, "apply-skill", "--name", "x", "--desc", "d", "--body", "safe body",
            "--reason", "r", "--confirm",
        ]) == cli.EXIT_FAIL

    with patch("agentic.registry.SkillRegistry") as reg_cls:
        reg = MagicMock()
        reg.apply_skill.return_value = {"applied": True, "name": "x"}
        reg_cls.return_value = reg
        assert cli.main([
            "--config", cfg, "apply-skill", "--name", "x", "--desc", "d", "--body", "safe body",
            "--reason", "r", "--confirm",
        ]) == 0
        assert "applied" in capsys.readouterr().out

    with patch("agentic.selftest.run_self_test", return_value=(5, 5, ["ok"])):
        assert cli.main(["--config", cfg, "test"]) == 0
    with patch("agentic.selftest.run_self_test", return_value=(4, 5, ["fail"])):
        assert cli.main(["--config", cfg, "test"]) == cli.EXIT_FAIL
