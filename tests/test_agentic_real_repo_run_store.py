"""Tests for agentic.real_repo_run_store -- pending real-repo run persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic.real_repo_run_store import (
    PENDING_DECISION,
    RealRepoRunRecord,
    load_run,
    new_run_id,
    require_pending_decision,
    save_run,
)
from utils.errors import AgenticError


def _record(**overrides) -> RealRepoRunRecord:
    kwargs = {
        "run_id": new_run_id(),
        "repo": "owner/repo",
        "dest": "/tmp/clone",
        "status": "running",
    }
    kwargs.update(overrides)
    return RealRepoRunRecord(**kwargs)


def test_new_run_id_matches_its_own_validation_pattern():
    from agentic.real_repo_run_store import RUN_ID_RE

    assert RUN_ID_RE.match(new_run_id())


def test_save_then_load_round_trips(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    record = _record(status=PENDING_DECISION, branch_name="claude/x", commit_message="msg", changed_files=["a.txt"])
    save_run(runs_dir, record)

    loaded = load_run(runs_dir, record.run_id)
    assert loaded.run_id == record.run_id
    assert loaded.status == PENDING_DECISION
    assert loaded.branch_name == "claude/x"
    assert loaded.changed_files == ["a.txt"]
    assert loaded.created_at
    assert loaded.updated_at


def test_save_stamps_created_at_once_and_updates_updated_at(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    record = _record()
    save_run(runs_dir, record)
    first_created = load_run(runs_dir, record.run_id).created_at

    record.status = "exhausted"
    save_run(runs_dir, record)
    second = load_run(runs_dir, record.run_id)
    assert second.created_at == first_created
    assert second.status == "exhausted"


def test_save_is_atomic_no_tmp_file_left_behind(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    save_run(runs_dir, _record())
    assert list(runs_dir.glob(".*.tmp")) == []
    assert list(runs_dir.glob("*.json"))


def test_load_raises_for_a_missing_run(tmp_path: Path):
    with pytest.raises(AgenticError, match="not found"):
        load_run(tmp_path / "runs", new_run_id())


def test_load_raises_for_a_corrupt_record(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    run_id = new_run_id()
    (runs_dir / f"{run_id}.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(AgenticError, match="unreadable or corrupt"):
        load_run(runs_dir, run_id)


@pytest.mark.parametrize(
    "payload",
    [
        "[]",  # valid JSON, but not a mapping -- **data raises TypeError
        '"a string"',
        '{"run_id": "x"}',  # valid JSON object, but missing required fields
        '{"run_id": "x", "repo": "r", "dest": "d", "status": "s", "unexpected_field": 1}',
    ],
)
def test_load_raises_agentic_error_not_typeerror_for_a_structurally_wrong_record(tmp_path: Path, payload):
    """The record crosses a real process boundary (written by one subprocess,
    read by a later one), so a schema-drifted or hand-edited-but-valid-JSON
    file must land here as AgenticError, not escape as a bare TypeError.

    RealRepoRunRecord(**data) used to run OUTSIDE the try/except, so every one
    of these shapes escaped both callers' `except AgenticError` handlers,
    reached main() uncaught, and exited 1 -- a code outside the documented
    0/2/3/4 API.
    """
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    run_id = new_run_id()
    (runs_dir / f"{run_id}.json").write_text(payload, encoding="utf-8")
    with pytest.raises(AgenticError, match="unreadable or corrupt"):
        load_run(runs_dir, run_id)


@pytest.mark.parametrize("bad_id", ["../escape", "not-hex-at-all!!", "", "a" * 31, "A" * 32])
def test_run_id_validation_rejects_unsafe_or_malformed_ids(tmp_path: Path, bad_id: str):
    with pytest.raises(AgenticError, match="invalid run_id"):
        load_run(tmp_path / "runs", bad_id)


def test_run_id_validation_cannot_escape_runs_dir(tmp_path: Path):
    # Even if the regex somehow let something through, confirm the intent:
    # a run_id is used as a bare filename stem, never joined as a path.
    runs_dir = tmp_path / "runs"
    with pytest.raises(AgenticError):
        load_run(runs_dir, "../../etc/passwd")
    assert not (tmp_path / "etc").exists()


def test_require_pending_decision_passes_when_pending(tmp_path: Path):
    require_pending_decision(_record(status=PENDING_DECISION))


@pytest.mark.parametrize("status", ["approved", "rejected", "exhausted", "failed"])
def test_require_pending_decision_rejects_terminal_states(status: str):
    with pytest.raises(AgenticError, match="already decided"):
        require_pending_decision(_record(status=status))


def test_require_pending_decision_rejects_still_running():
    with pytest.raises(AgenticError, match="not awaiting a decision"):
        require_pending_decision(_record(status="running"))


def test_record_to_dict_is_json_serializable(tmp_path: Path):
    record = _record(status=PENDING_DECISION)
    json.dumps(record.to_dict())  # must not raise
