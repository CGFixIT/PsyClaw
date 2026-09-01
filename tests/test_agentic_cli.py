"""Tests for agentic.cli -- subcommands, disabled no-op, exit codes."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pytest
import yaml

import utils.logger as logger_mod
from agentic import cli
from utils.errors import AgenticConfigError, AgenticError, AgenticWriteRefused

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
