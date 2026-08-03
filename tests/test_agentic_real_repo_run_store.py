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
    require_approved_for_push,
    require_pending_decision,
    require_pushed_for_publish,
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
    record = _record(status=PENDING_DECISION, branch_name="agent/x", commit_message="msg", changed_files=["a.txt"])
    save_run(runs_dir, record)

    loaded = load_run(runs_dir, record.run_id)
    assert loaded.run_id == record.run_id
    assert loaded.status == PENDING_DECISION
    assert loaded.branch_name == "agent/x"
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


def test_save_wraps_an_oserror_as_agentic_error(tmp_path: Path):
    """Mirrors load_run's own precedent: a write-time OSError (disk full,

    permission denied, a locked temp file) must not escape as a bare OSError --
    every caller in agentic/cli.py only catches AgenticError, so an unwrapped
    OSError would skip past all of them, reach main() uncaught, and exit 1, a
    code outside the documented 0/2/3/4 API.
    """
    runs_dir = tmp_path / "runs"
    # A file where the runs directory needs to be makes mkdir(parents=True) --
    # and therefore the whole write -- fail with a real OSError, no mocking.
    runs_dir.parent.mkdir(parents=True, exist_ok=True)
    runs_dir.write_text("not a directory", encoding="utf-8")
    with pytest.raises(AgenticError, match="failed to persist run record"):
        save_run(runs_dir, _record())


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


# --- the two escalation guards ----------------------------------------------


@pytest.mark.parametrize("status", ["running", PENDING_DECISION, "rejected", "exhausted", "failed"])
def test_push_requires_an_approved_run(status):
    """A run that was never approved has no commit to push."""
    with pytest.raises(AgenticError, match="not approved"):
        require_approved_for_push(_record(status=status))


def test_push_refuses_a_second_push():
    """A re-push is a git no-op that would report success while doing nothing."""
    with pytest.raises(AgenticError, match="already pushed"):
        require_approved_for_push(_record(status="approved", pushed=True))


def test_push_accepts_an_approved_unpushed_run():
    require_approved_for_push(_record(status="approved"))  # must not raise


@pytest.mark.parametrize("status", ["running", PENDING_DECISION, "rejected", "exhausted", "failed"])
def test_publish_requires_an_approved_run(status):
    with pytest.raises(AgenticError, match="not approved"):
        require_pushed_for_publish(_record(status=status, pushed=True))


def test_publish_requires_the_branch_to_be_on_origin_first():
    """`gh pr create --head` names a branch GitHub must already be able to see."""
    with pytest.raises(AgenticError, match="has not been pushed"):
        require_pushed_for_publish(_record(status="approved", pushed=False))


def test_publish_refuses_a_second_pull_request():
    """The duplicate execute_write's no-retry rule exists to prevent.

    Had ZERO coverage: deleting this branch left the whole suite green, and it
    is the only thing standing between an operator's natural retry and a
    second gh pr create on the same branch.
    """
    with pytest.raises(AgenticError, match="already has a pull request"):
        require_pushed_for_publish(_record(status="approved", pushed=True, pr_url="https://x/pull/1"))


def test_publish_accepts_an_approved_pushed_run_with_no_pr():
    require_pushed_for_publish(_record(status="approved", pushed=True))  # must not raise
