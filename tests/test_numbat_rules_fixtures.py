"""Lock the committed Numbat fixtures to the CI ``rules test --fixture`` contract.

The GitHub job (``.github/workflows/numbat-rules.yml``) runs the same argv
against these files. When ``numbat`` is on PATH (or ``NUMBAT`` points at the
binary), this module drives that CLI. The fixture-content assertions always
run so a missing CLI cannot hide a fixture that would miss the two required
rule ids.

Clean events are built with ``utils.numbat_emitter.build_event`` (same kwargs
the executor / fsconnect / real_repo_loop call sites use). AC #2 is the live
executor-jail test below, not a hand-authored JSON file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from utils.logger import reset_config_cache
from utils.numbat_emitter import SCHEMA_VERSION, build_event, close_numbat_handles

_REPO = Path(__file__).resolve().parent.parent
_FIXTURE_DIR = _REPO / "tests" / "fixtures" / "numbat"
KNOWN_BAD = _FIXTURE_DIR / "known-bad-events.ndjson"
CLEAN = _FIXTURE_DIR / "clean-events.ndjson"
EXPECTED_HITS = ("exfil.curl_post_file", "secrets.read_private_key")
_FROZEN_RUN = "fixture-run-961"
_FROZEN_ENDPOINT = {
    "hostname": "space-sandbox",
    "os": "linux",
    "arch": "amd64",
    "username": "user",
    "uid": "2000",
}
_FROZEN_LOCAL_PATH = "logs/numbat-events.ndjsonl"
_CFG = {"numbat": {"enabled": True, "output_path": _FROZEN_LOCAL_PATH}}


def _numbat_bin() -> str | None:
    return os.environ.get("NUMBAT") or shutil.which("numbat")


def _load_events(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _freeze(record: dict, event_id: str, timestamp: str) -> dict:
    record["run_id"] = _FROZEN_RUN
    record["endpoint"] = dict(_FROZEN_ENDPOINT)
    record["event_id"] = event_id
    record["timestamp"] = timestamp
    record["evidence"]["local_path"] = _FROZEN_LOCAL_PATH
    record["schema_version"] = SCHEMA_VERSION
    return record


def test_known_bad_fixture_carries_rule_signatures() -> None:
    events = _load_events(KNOWN_BAD)
    commands = " ".join(str(e.get("command") or "") for e in events)
    paths = " ".join(str(e.get("file_path") or "") for e in events)
    assert "@/.ssh/id_rsa" in commands
    assert "https://" in commands
    assert paths.rstrip().endswith("/.ssh/id_rsa") or "/.ssh/id_rsa" in paths


def test_clean_fixture_has_no_rule_signatures() -> None:
    events = _load_events(CLEAN)
    blob = json.dumps(events)
    assert "@/.ssh/id_rsa" not in blob
    assert "/.ssh/id_rsa" not in blob
    assert "https://evil.example" not in blob


def test_fixtures_are_emitter_shaped() -> None:
    for path in (CLEAN, KNOWN_BAD):
        for event in _load_events(path):
            assert event["schema_version"] == SCHEMA_VERSION
            assert event["source_agent"] == "unknown"
            assert event["evidence"]["artifact_type"]
            assert event["evidence"]["local_path"] == _FROZEN_LOCAL_PATH
            if event["event_type"] == "command.exec":
                assert "exit_code" not in event
                assert "file_path" not in event
            if event["event_type"] == "command.result":
                assert "exit_code" in event


def test_clean_fixture_matches_build_event() -> None:
    expected = [
        _freeze(
            build_event(
                "command.exec",
                command="python -m pytest -q --tb=short",
                tool_name="executor",
                actor="system",
                tags=["executor", "pytest"],
                artifact_type="executor",
                cfg=_CFG,
            ),
            "clean-exec",
            "2026-08-20T00:00:00.000000+00:00",
        ),
        _freeze(
            build_event(
                "command.result",
                command="python -m pytest -q --tb=short",
                exit_code=0,
                tool_name="executor",
                actor="system",
                tags=["executor", "pytest"],
                artifact_type="executor",
                cfg=_CFG,
            ),
            "clean-result",
            "2026-08-20T00:00:00.000100+00:00",
        ),
        _freeze(
            build_event(
                "file.read",
                file_path="README.md",
                tool_name="fsconnect",
                actor="system",
                tags=["fsconnect", "fs_read"],
                artifact_type="fsconnect",
                cfg=_CFG,
            ),
            "clean-read",
            "2026-08-20T00:00:00.000200+00:00",
        ),
        _freeze(
            build_event(
                "permission.approved",
                tool_name="real_repo_loop",
                decision="allowed",
                approval_required=True,
                approval_decision="allowed",
                actor="user",
                tags=["real_repo_loop", "decide"],
                artifact_type="real_repo_loop",
                cfg=_CFG,
            ),
            "clean-perm",
            "2026-08-20T00:00:00.000300+00:00",
        ),
    ]
    assert _load_events(CLEAN) == expected


def test_executor_jail_live_events_have_zero_rule_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC #2: a real executor run under the scrubbed env must not match rules."""
    from agentic.executor import Check, run_verification
    from tests.executor_sandbox_double import inject_argv_list_sandbox

    inject_argv_list_sandbox(monkeypatch)

    reset_config_cache()
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
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    report = run_verification(
        tmp_path,
        [Check("ok", ("python", "-c", "print(1)"))],
        config_path=str(config_path),
        cfg=cfg,
    )
    assert report.ok
    # write_ndjson caches its append handle per output path; release it before
    # tmp_path teardown removes the directory (Windows cannot delete a file
    # that is still open).
    close_numbat_handles()
    blob = out.read_text(encoding="utf-8")
    assert blob
    assert "@/.ssh/id_rsa" not in blob
    assert "/.ssh/id_rsa" not in blob
    assert "https://evil.example" not in blob
    records = [json.loads(line) for line in blob.splitlines() if line.strip()]
    exec_records = [r for r in records if r["event_type"] == "command.exec"]
    result_records = [r for r in records if r["event_type"] == "command.result"]
    assert exec_records, "expected at least one command.exec record"
    assert result_records, "expected at least one command.result record"
    assert "exit_code" not in exec_records[0]
    assert "exit_code" in result_records[0]
    exe = _numbat_bin()
    if exe is None:
        pytest.skip("numbat CLI not installed")
    proc = subprocess.run(
        [exe, "rules", "test", "--fixture", str(out), "--expect-none"],
        check=False,
        capture_output=True,
        text=True,
    )
    out_text = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out_text
    assert "exfil.curl_post_file" not in proc.stdout
    assert "secrets.read_private_key" not in proc.stdout


@pytest.mark.skipif(_numbat_bin() is None, reason="numbat CLI not installed")
def test_numbat_cli_known_bad_hits_required_rules() -> None:
    exe = _numbat_bin()
    assert exe is not None
    argv = [
        exe,
        "rules",
        "test",
        "--fixture",
        str(KNOWN_BAD),
        "--expect",
        EXPECTED_HITS[0],
        "--expect",
        EXPECTED_HITS[1],
    ]
    proc = subprocess.run(argv, check=False, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out
    for rule in EXPECTED_HITS:
        assert rule in proc.stdout


@pytest.mark.skipif(_numbat_bin() is None, reason="numbat CLI not installed")
def test_numbat_cli_clean_fixture_zero_hits() -> None:
    exe = _numbat_bin()
    assert exe is not None
    argv = [exe, "rules", "test", "--fixture", str(CLEAN), "--expect-none"]
    proc = subprocess.run(argv, check=False, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "exfil.curl_post_file" not in proc.stdout
    assert "secrets.read_private_key" not in proc.stdout
