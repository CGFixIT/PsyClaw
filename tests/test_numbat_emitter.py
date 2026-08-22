"""Tests for the Numbat dual-write emitter (#959 / #961).

The emitter is a projection: audit.jsonl stays authoritative, and every
failure path must degrade rather than raise.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml

from utils.logger import reset_config_cache
from utils.numbat_emitter import (
    SCHEMA_VERSION,
    build_endpoint,
    build_event,
    close_numbat_handles,
    emit_numbat_command,
    emit_numbat_event,
    posix_path,
    redact_argv_for_numbat,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORE = ("gate.py", "gate_ops.py", "gate_auth.py", "gate_memory.py", "graph.py", "mcp_hybrid_server.py")
_REQUIRED = {
    "schema_version",
    "record_type",
    "run_id",
    "endpoint",
    "event_id",
    "source_agent",
    "source_type",
    "event_type",
    "confidence",
    "evidence",
}


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    # write_ndjson now caches its append handle per output path (mirroring
    # utils/logger.py's _AUDIT_HANDLES) instead of open/close per event --
    # release it here so tmp_path teardown can remove the directory (Windows
    # cannot delete a file that is still open).
    close_numbat_handles()
    reset_config_cache()


@pytest.fixture
def numbat_cfg(tmp_path: Path) -> tuple[str, Path]:
    out = tmp_path / "numbat-events.ndjsonl"
    cfg = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl")},
        "numbat": {
            "enabled": True,
            "output_path": str(out),
            "source_agent": "unknown",
            "source_type": "hook",
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path), out


def test_redact_argv_strips_reason_and_sql() -> None:
    joined = redact_argv_for_numbat(
        ["python", "-m", "agentic.cli", "--reason=secret reason", "--sql", "SELECT 1"]
    )
    assert "secret reason" not in joined
    assert "SELECT 1" not in joined
    assert "--reason=<redacted>" in joined
    assert "<redacted>" in joined


def test_posix_path_normalizes_backslashes() -> None:
    assert posix_path(r"C:\Users\x\file") == "C:/Users/x/file"
    assert posix_path("") is None
    assert posix_path(None) is None


def test_build_event_is_schema_legal() -> None:
    record = build_event(
        "command.exec",
        command="python -m pytest -q",
        exit_code=0,
        file_path="/tmp/worktree",
        tags=["executor"],
        artifact_type="executor",
    )
    assert set(_REQUIRED) <= set(record)
    assert record["schema_version"] == SCHEMA_VERSION
    assert SCHEMA_VERSION == "0.3.0"
    assert record["record_type"] == "event"
    assert record["source_agent"] == "unknown"
    assert record["source_type"] == "hook"
    assert record["tags"][0] == "cyclaw"
    assert "executor" in record["tags"]
    assert set(record["endpoint"]) <= {"hostname", "os", "arch", "username", "uid", "device_id"}
    for key in ("hostname", "os", "arch", "username", "uid"):
        assert record["endpoint"][key]
    assert record["evidence"]["artifact_type"] == "executor"
    assert record["evidence"]["local_path"]
    assert "exit_code" not in record
    assert "file_path" not in record


def test_command_result_keeps_exit_code() -> None:
    record = build_event(
        "command.result",
        command="python -m pytest -q",
        exit_code=0,
        duration_ms=12,
        tags=["executor"],
        artifact_type="executor",
    )
    assert record["exit_code"] == 0
    assert record["duration_ms"] == 12
    assert record["command"] == "python -m pytest -q"


def test_source_agent_cyclaw_is_forced_to_unknown() -> None:
    record = build_event("file.read", cfg={"numbat": {"source_agent": "cyclaw"}})
    assert record["source_agent"] == "unknown"


def test_unknown_event_type_raises_in_builder_but_emit_swallows() -> None:
    with pytest.raises(ValueError, match="unsupported event_type"):
        build_event("not.a.type")
    emit_numbat_event("not.a.type")  # must not raise


def test_emit_writes_one_ndjson_line(numbat_cfg: tuple[str, Path]) -> None:
    config_path, out = numbat_cfg
    emit_numbat_command(
        "python -m ruff check .",
        exit_code=0,
        tool_name="executor",
        tags=["executor", "ruff"],
        config_path=config_path,
    )
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    exec_record = json.loads(lines[0])
    result_record = json.loads(lines[1])
    assert exec_record["event_type"] == "command.exec"
    assert exec_record["command"] == "python -m ruff check ."
    assert "exit_code" not in exec_record
    assert result_record["event_type"] == "command.result"
    assert result_record["exit_code"] == 0
    assert "cyclaw" in exec_record["tags"]


def test_disabled_is_a_noop(tmp_path: Path) -> None:
    out = tmp_path / "numbat-events.ndjsonl"
    cfg = {"numbat": {"enabled": False, "output_path": str(out)}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    emit_numbat_event("command.exec", command="echo hi", config_path=str(path))
    assert not out.exists()


def test_executor_emits_command_exec(tmp_path: Path, numbat_cfg: tuple[str, Path]) -> None:
    from agentic.executor import Check, run_verification

    config_path, out = numbat_cfg
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    report = run_verification(
        tmp_path,
        [Check("ok", ("python", "-c", "print(1)"))],
        config_path=config_path,
        cfg=cfg,
    )
    assert report.ok
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    # The mainline audit trail now projects into the same file
    # (artifact_type "cyclaw_audit_jsonl"); this test asserts the executor's
    # action-plane records, so exclude the audit projection.
    records = [r for r in records if r["evidence"]["artifact_type"] != "cyclaw_audit_jsonl"]
    assert len(records) == 2
    assert records[0]["event_type"] == "command.exec"
    assert records[0]["tool_name"] == "executor"
    assert "exit_code" not in records[0]
    assert records[1]["event_type"] == "command.result"
    assert records[1]["exit_code"] == 0


def test_ops_runner_redacts_reason(monkeypatch: pytest.MonkeyPatch, numbat_cfg: tuple[str, Path]) -> None:
    import subprocess

    from utils import ops_runner

    config_path, out = numbat_cfg
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    monkeypatch.setattr(ops_runner, "_get_config", lambda *_a, **_k: cfg)
    monkeypatch.setattr("utils.numbat_emitter._get_config", lambda *_a, **_k: cfg)

    def _fake_run(argv, *, timeout_sec=None):
        del timeout_sec
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(ops_runner, "_run", _fake_run)
    ops_runner.run_agentic_op(
        "apply-skill",
        name="demo",
        desc="desc",
        reason="super secret reason",
        confirm=True,
    )
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    blob = "\n".join(lines)
    assert "super secret reason" not in blob
    record = json.loads(lines[0])
    assert "--reason=<redacted>" in record["command"]
    assert record["tags"][-1] == "apply-skill"
    assert json.loads(lines[1])["event_type"] == "command.result"


def test_core_modules_do_not_import_emitter() -> None:
    for name in _CORE:
        tree = ast.parse((_REPO_ROOT / name).read_text(encoding="utf-8"), filename=name)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any("numbat_emitter" in item for item in imported), name


def test_emitter_does_not_import_core() -> None:
    tree = ast.parse((_REPO_ROOT / "utils" / "numbat_emitter.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split(".", 1)[0])
    assert not ({"gate", "gate_ops", "gate_auth", "gate_memory", "graph", "mcp_hybrid_server"} & set(imported))


def test_endpoint_shape() -> None:
    endpoint = build_endpoint()
    assert set(endpoint) >= {"hostname", "os", "arch", "username", "uid"}
    assert set(endpoint) <= {"hostname", "os", "arch", "username", "uid", "device_id"}


def test_tool_result_strips_illegal_action_fields() -> None:
    record = build_event(
        "tool.result",
        command="echo hi",
        file_path="/tmp/x",
        exit_code=0,
        duration_ms=5,
        tool_name="fsconnect",
    )
    assert "command" not in record
    assert "file_path" not in record
    assert "exit_code" not in record
    assert "duration_ms" not in record
    assert record["tool_name"] == "fsconnect"


def test_file_read_keeps_path_drops_command() -> None:
    record = build_event(
        "file.read",
        file_path="README.md",
        command="cat README.md",
        exit_code=0,
        tool_name="fsconnect",
    )
    assert record["file_path"] == "README.md"
    assert record["tool_name"] == "fsconnect"
    assert "command" not in record
    assert "exit_code" not in record


def test_session_start_strips_action_fields() -> None:
    record = build_event(
        "session.start",
        command="python",
        file_path="/tmp",
        exit_code=0,
        tool_name="executor",
        actor="system",
    )
    assert "command" not in record
    assert "file_path" not in record
    assert "exit_code" not in record
    assert "tool_name" not in record
    assert record["actor"] == "system"


def test_forbidden_map_covers_every_event_type() -> None:
    from utils import numbat_emitter as ne

    assert set(ne._EVENT_TYPE_FORBIDDEN_FIELDS) == ne._EVENT_TYPES
    exec_forbidden = ne._EVENT_TYPE_FORBIDDEN_FIELDS["command.exec"]
    assert {"exit_code", "file_path", "duration_ms"} <= exec_forbidden
    assert "command" not in exec_forbidden
    result_forbidden = ne._EVENT_TYPE_FORBIDDEN_FIELDS["tool.result"]
    assert {"command", "exit_code", "duration_ms", "file_path"} <= result_forbidden
