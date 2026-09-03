"""Tests for the Numbat dual-write emitter (#959 / #961).

The emitter is a projection: audit.jsonl stays authoritative, and every
failure path must degrade rather than raise.
"""

from __future__ import annotations

import ast
import json
import os
import threading
import time
from pathlib import Path
from unittest import mock

import pytest
import yaml

from utils import numbat_emitter
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


# --- size-based rollover ------------------------------------------------------


def _rollover_cfg(tmp_path: Path, max_bytes: int) -> tuple[str, Path]:
    out = tmp_path / "numbat-events.ndjsonl"
    cfg = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl")},
        "numbat": {
            "enabled": True,
            "output_path": str(out),
            "max_bytes": max_bytes,
            "source_agent": "unknown",
            "source_type": "hook",
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path), out


def _ndjson_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_rollover_renames_at_max_bytes_and_events_keep_flowing(tmp_path: Path) -> None:
    """Past max_bytes the stream renames to .1 and starts fresh -- with no
    event lost across the boundary (each record lands whole in exactly one
    generation, because the check runs before the write under the lock)."""
    config_path, out = _rollover_cfg(tmp_path, max_bytes=2000)
    rolled = out.with_name(out.name + ".1")

    emitted = 0
    while not rolled.exists() and emitted < 50:
        emit_numbat_event("command.exec", command="echo hi", config_path=config_path)
        emitted += 1
    assert rolled.exists(), "rollover never happened within 50 events"

    emit_numbat_event("command.exec", command="echo after", config_path=config_path)
    emitted += 1

    fresh, archived = _ndjson_lines(out), _ndjson_lines(rolled)
    assert fresh, "stream stopped flowing after rollover"
    assert len(fresh) + len(archived) == emitted, "an event was lost across the rollover"
    assert out.stat().st_size < rolled.stat().st_size


def test_rollover_keeps_a_single_generation(tmp_path: Path) -> None:
    config_path, out = _rollover_cfg(tmp_path, max_bytes=1200)
    rolled = out.with_name(out.name + ".1")
    for _ in range(30):  # enough for several rollovers at 1200 bytes
        emit_numbat_event("command.exec", command="echo hi", config_path=config_path)
    assert rolled.exists()
    generations = [p for p in out.parent.iterdir() if p.name.startswith(out.name) and p != out]
    assert generations == [rolled], "only the single .1 generation may exist"


def test_zero_max_bytes_disables_rollover(tmp_path: Path) -> None:
    config_path, out = _rollover_cfg(tmp_path, max_bytes=0)
    for _ in range(20):
        emit_numbat_event("command.exec", command="echo hi", config_path=config_path)
    assert out.stat().st_size > 1200  # grew well past any small threshold
    assert not out.with_name(out.name + ".1").exists()


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX rename-over-open-file semantics; Windows locks an open file, so the "
           "cross-process rollover this guards against cannot occur there",
)
def test_rollover_by_another_process_does_not_orphan_the_cached_handle(tmp_path: Path) -> None:
    """Regression: a rename underneath a cached handle must force a reopen.

    The handle cache is keyed on the path string, but a rename moves that name
    off the inode the handle holds. The action plane rolls this same stream over
    from ops_runner child processes while a long-lived gate.py holds its handle
    open. Without an inode check the server keeps appending into the .1
    generation for the rest of its uptime -- and the next rollover deletes that
    whole backlog, silently ending the mainline plane's projection.
    """
    config_path, out = _rollover_cfg(tmp_path, max_bytes=0)
    rolled = out.with_name(out.name + ".1")

    emit_numbat_event("command.exec", command="echo before", config_path=config_path)
    assert out.exists()

    # Stand in for another process's rollover: rename the file out from under us.
    os.replace(out, rolled)
    emit_numbat_event("command.exec", command="echo after", config_path=config_path)

    assert out.exists(), "live stream must be reopened, not abandoned"
    fresh = out.read_text(encoding="utf-8")
    assert "echo after" in fresh, "post-rename events must land in the live file"
    assert "echo before" in rolled.read_text(encoding="utf-8")


def test_rollover_does_not_clobber_the_archive_when_another_writer_wins(tmp_path: Path) -> None:
    """A second writer must not replace the .1 generation with the fresh file.

    _WRITE_LOCK is process-local, but ops_runner children write this same path.
    Before the rollover lock, a writer that measured the file as oversized and
    then lost the race would still run os.replace -- moving the WINNER's newly
    created (tiny) live file over the archive and destroying the generation.
    Reproduced as 500 archived lines replaced by a 14-byte file.
    """
    live = tmp_path / "numbat-events.ndjsonl"
    live.write_text("ARCHIVE-LINE\n" * 500, encoding="utf-8")
    archive = live.with_name(live.name + ".1")
    max_bytes = 100

    original_acquire = numbat_emitter._acquire_rollover_lock
    gate = threading.Event()

    def stalling_acquire(lock_path):
        # The loser stat'd the old oversized file, then stalls before the lock --
        # exactly the window that destroyed the archive.
        if threading.current_thread().name == "loser":
            gate.wait(5)
        return original_acquire(lock_path)

    def winner() -> None:
        numbat_emitter._rollover_if_needed(live, max_bytes)
        live.write_text("tiny-new-line\n", encoding="utf-8")
        gate.set()

    def loser() -> None:
        numbat_emitter._rollover_if_needed(live, max_bytes)

    with mock.patch.object(numbat_emitter, "_acquire_rollover_lock", stalling_acquire):
        t_lose = threading.Thread(target=loser, name="loser")
        t_lose.start()
        time.sleep(0.05)  # let the loser get past its size check first
        t_win = threading.Thread(target=winner, name="winner")
        t_win.start()
        t_win.join(timeout=10)
        t_lose.join(timeout=10)

    assert archive.read_text(encoding="utf-8").count("ARCHIVE-LINE") == 500
    assert live.read_text(encoding="utf-8") == "tiny-new-line\n"
    assert not live.with_name(live.name + ".rollover.lock").exists()


def test_rollover_skips_while_another_writer_holds_the_lock(tmp_path: Path) -> None:
    """A held lock makes this writer stand down rather than rotate concurrently."""
    live = tmp_path / "numbat-events.ndjsonl"
    live.write_text("x" * 500, encoding="utf-8")
    lock_path = live.with_name(live.name + ".rollover.lock")
    lock_path.write_text("", encoding="utf-8")  # a live holder

    numbat_emitter._rollover_if_needed(live, 100)

    assert live.exists(), "the live file must not be rotated while the lock is held"
    assert not live.with_name(live.name + ".1").exists()


def test_rollover_reclaims_an_abandoned_lock(tmp_path: Path) -> None:
    """A lock left behind by a crashed writer must not disable rollover forever."""
    live = tmp_path / "numbat-events.ndjsonl"
    live.write_text("x" * 500, encoding="utf-8")
    lock_path = live.with_name(live.name + ".rollover.lock")
    lock_path.write_text("", encoding="utf-8")
    stale = time.time() - (numbat_emitter._ROLLOVER_LOCK_STALE_SEC + 30)
    os.utime(lock_path, (stale, stale))

    numbat_emitter._rollover_if_needed(live, 100)

    assert live.with_name(live.name + ".1").exists(), "abandoned lock should be reclaimed"
    assert not lock_path.exists()


def test_max_bytes_garbage_falls_back_to_default() -> None:
    assert numbat_emitter._max_bytes({"numbat": {"max_bytes": "nope"}}) == numbat_emitter.DEFAULT_MAX_BYTES
    assert numbat_emitter._max_bytes({"numbat": {"max_bytes": None}}) == numbat_emitter.DEFAULT_MAX_BYTES
    assert numbat_emitter._max_bytes({"numbat": {"max_bytes": -5}}) == 0


def test_build_endpoint_arch_and_device_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(numbat_emitter.platform, "machine", lambda: "aarch64")
    monkeypatch.setenv("NUMBAT_DEVICE_ID", "dev-123")
    ep = build_endpoint()
    assert ep["arch"] == "arm64"
    assert ep["device_id"] == "dev-123"

    monkeypatch.setattr(numbat_emitter.platform, "machine", lambda: "riscv64")
    ep2 = build_endpoint()
    assert ep2["arch"] == "riscv64"


def test_build_endpoint_getuser_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> str:
        raise OSError("no user")

    monkeypatch.setattr(numbat_emitter.getpass, "getuser", boom)
    ep = build_endpoint()
    assert ep["username"] == "unknown"


def test_build_event_normalizes_bad_enums() -> None:
    record = build_event(
        "command.exec",
        command="echo",
        confidence="nope",  # type: ignore[arg-type]
        decision="maybe",  # type: ignore[arg-type]
        approval_decision="nah",  # type: ignore[arg-type]
        actor="robot",  # type: ignore[arg-type]
    )
    assert record["confidence"] == "high"
    assert "decision" not in record
    assert "approval_decision" not in record
    assert "actor" not in record


def test_build_event_rejects_schema_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        numbat_emitter,
        "_KNOWN_FIELDS",
        frozenset(numbat_emitter._KNOWN_FIELDS) - {"command"},
    )
    with pytest.raises(ValueError, match="schema extras rejected"):
        build_event("command.exec", command="echo hi")


def test_handle_still_points_at_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "n.ndjsonl"
    path.write_text("", encoding="utf-8")
    handle = path.open("a", encoding="utf-8")
    try:
        monkeypatch.setattr(
            numbat_emitter.os,
            "fstat",
            lambda _fd: (_ for _ in ()).throw(OSError("gone")),
        )
        assert numbat_emitter._handle_still_points_at(handle, path) is False
    finally:
        handle.close()


def test_numbat_handle_reopens_when_inode_diverges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "n.ndjsonl"
    path.write_text("old\n", encoding="utf-8")
    with numbat_emitter._WRITE_LOCK:
        first = numbat_emitter._numbat_handle(path)
        first.write("cached\n")
        first.flush()
        monkeypatch.setattr(numbat_emitter, "_handle_still_points_at", lambda _h, _p: False)
        second = numbat_emitter._numbat_handle(path)
        assert second is not first
        second.write("fresh\n")
        second.flush()
    close_numbat_handles()
    assert "fresh" in path.read_text(encoding="utf-8")


def test_numbat_handle_close_oserror_on_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "n.ndjsonl"
    path.write_text("", encoding="utf-8")
    with numbat_emitter._WRITE_LOCK:
        handle = numbat_emitter._numbat_handle(path)

        def boom_close() -> None:
            raise OSError("close failed")

        monkeypatch.setattr(handle, "close", boom_close)
        monkeypatch.setattr(numbat_emitter, "_handle_still_points_at", lambda _h, _p: False)
        # Must not raise; reopens a fresh handle.
        numbat_emitter._numbat_handle(path)
    close_numbat_handles()


def test_close_numbat_handles_swallows_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "n.ndjsonl"
    with numbat_emitter._WRITE_LOCK:
        handle = numbat_emitter._numbat_handle(path)

        def boom_close() -> None:
            raise OSError("close failed")

        monkeypatch.setattr(handle, "close", boom_close)
    close_numbat_handles()
    assert numbat_emitter._NUMBAT_HANDLES == {}


def test_rollover_stat_oserror_after_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live = tmp_path / "numbat-events.ndjsonl"
    live.write_text("x" * 500, encoding="utf-8")
    real_stat = Path.stat
    live_stats = {"n": 0}

    def flaky_stat(self, *a, **k):
        if self == live or self.resolve() == live.resolve():
            live_stats["n"] += 1
            # First size check succeeds; re-check under the lock fails.
            if live_stats["n"] >= 2:
                raise OSError("stat failed")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    numbat_emitter._rollover_if_needed(live, 100)
    monkeypatch.undo()
    assert live.exists()
    assert not live.with_name(live.name + ".1").exists()


def test_rollover_handle_close_and_replace_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = tmp_path / "numbat-events.ndjsonl"
    live.write_text("x" * 500, encoding="utf-8")
    with numbat_emitter._WRITE_LOCK:
        handle = numbat_emitter._numbat_handle(live)

        def boom_close() -> None:
            raise OSError("close failed")

        monkeypatch.setattr(handle, "close", boom_close)
    monkeypatch.setattr(
        numbat_emitter.os,
        "replace",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("replace refused")),
    )
    numbat_emitter._rollover_if_needed(live, 100)
    # Replace failed: live file remains (never-raise contract).
    assert live.exists()
    close_numbat_handles()


def test_acquire_rollover_lock_oserror_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "x.rollover.lock"

    def open_oserror(*_a, **_k):
        raise OSError("open failed")

    monkeypatch.setattr(numbat_emitter.os, "open", open_oserror)
    assert numbat_emitter._acquire_rollover_lock(lock_path) is None

    # FileExists then stat fails → None
    lock_path.write_text("", encoding="utf-8")

    def open_exists(*_a, **_k):
        raise FileExistsError

    monkeypatch.setattr(numbat_emitter.os, "open", open_exists)
    monkeypatch.setattr(
        Path,
        "stat",
        lambda self, *a, **k: (_ for _ in ()).throw(OSError("stat")),
    )
    assert numbat_emitter._acquire_rollover_lock(lock_path) is None


def test_acquire_rollover_lock_reclaim_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "x.rollover.lock"
    lock_path.write_text("", encoding="utf-8")
    stale = time.time() - (numbat_emitter._ROLLOVER_LOCK_STALE_SEC + 30)
    os.utime(lock_path, (stale, stale))

    def open_exists_then(*_a, **_k):
        raise FileExistsError

    monkeypatch.setattr(numbat_emitter.os, "open", open_exists_then)
    monkeypatch.setattr(
        numbat_emitter.os,
        "unlink",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("lost race")),
    )
    assert numbat_emitter._acquire_rollover_lock(lock_path) is None


def test_release_rollover_lock_swallows_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "x.rollover.lock"
    lock_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        numbat_emitter.os,
        "close",
        lambda _fd: (_ for _ in ()).throw(OSError("close")),
    )
    monkeypatch.setattr(
        numbat_emitter.os,
        "unlink",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("unlink")),
    )
    numbat_emitter._release_rollover_lock(3, lock_path)  # must not raise


def test_audit_content_preview_empty_and_truncation() -> None:
    assert numbat_emitter._audit_content_preview({"timestamp": "t", "event": None}) is None
    # Only timestamp/None → empty preview → None
    assert numbat_emitter._audit_content_preview({"timestamp": "t"}) is None

    bulky = {
        "event": "query",
        "sources": ["x" * 800],
        "errors": ["y" * 800],
        "details": {"z": "w" * 800},
        "extra": "k" * 500,
    }
    text = numbat_emitter._audit_content_preview(bulky)
    assert text is not None
    assert len(text) <= numbat_emitter._AUDIT_PREVIEW_CAP

    # After dropping bulky keys the remainder is still over the cap → hard slice.
    still_huge = {
        "event": "query",
        "sources": ["s"],
        "errors": ["e"],
        "details": {"d": 1},
        "pad": "p" * (numbat_emitter._AUDIT_PREVIEW_CAP + 100),
    }
    sliced = numbat_emitter._audit_content_preview(still_huge)
    assert sliced is not None
    assert len(sliced) == numbat_emitter._AUDIT_PREVIEW_CAP


def test_project_audit_record_writes_and_swallows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "numbat-events.ndjsonl"
    cfg = {
        "numbat": {
            "enabled": True,
            "output_path": str(out),
            "source_agent": "unknown",
            "source_type": "hook",
        },
        "models": {"local_llm": {"provider": "ollama"}},
    }
    numbat_emitter.project_audit_record(
        {
            "event": "rag_query",
            "model_used": "local",
            "llm_model": "llama",
            "top_score": 0.04,
            "guardrail_blocked": True,
        },
        cfg=cfg,
    )
    for event in ("mcp_rag_query", "mcp_rag_error", "retrieval_degraded", "user_gate_pause"):
        numbat_emitter.project_audit_record({"event": event}, cfg=cfg)
    close_numbat_handles()
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "guardrail_blocked" in body

    before = out.read_text(encoding="utf-8")
    numbat_emitter.project_audit_record({"event": "x"}, cfg={"numbat": {"enabled": False}})
    numbat_emitter.project_audit_record({"event": ""}, cfg=cfg)
    numbat_emitter.project_audit_record({"event": 123}, cfg=cfg)

    monkeypatch.setattr(
        numbat_emitter,
        "emit_numbat_event",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    numbat_emitter.project_audit_record({"event": "query_complete"}, cfg=cfg)
    close_numbat_handles()
    assert out.read_text(encoding="utf-8") == before
