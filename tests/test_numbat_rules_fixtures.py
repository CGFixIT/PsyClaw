"""Lock the committed Numbat fixtures to the CI ``rules test --fixture`` contract.

The GitHub job (``.github/workflows/numbat-rules.yml``) runs the same argv
against these files. When ``numbat`` is on PATH (or ``NUMBAT`` points at the
binary), this module drives that CLI. The fixture-content assertions always
run so a missing CLI cannot hide a fixture that would miss the two required
rule ids.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_FIXTURE_DIR = _REPO / "tests" / "fixtures" / "numbat"
KNOWN_BAD = _FIXTURE_DIR / "known-bad-events.ndjson"
CLEAN = _FIXTURE_DIR / "clean-events.ndjson"
EXPECTED_HITS = ("exfil.curl_post_file", "secrets.read_private_key")


def _numbat_bin() -> str | None:
    return os.environ.get("NUMBAT") or shutil.which("numbat")


def _load_events(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


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


def _generate_live_jail(out: Path) -> None:
    script = _FIXTURE_DIR / "generate_jail_events.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--out", str(out)],
        cwd=str(_REPO),
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def test_live_jail_events_have_no_rule_signatures(tmp_path: Path) -> None:
    out = tmp_path / "jail-events.ndjson"
    _generate_live_jail(out)
    blob = out.read_text(encoding="utf-8")
    assert out.stat().st_size > 0
    assert "@/.ssh/id_rsa" not in blob
    assert "/.ssh/id_rsa" not in blob
    assert "https://evil.example" not in blob
    events = _load_events(out)
    assert any(e.get("event_type") == "command.exec" for e in events)
    assert any(e.get("event_type") == "file.read" for e in events)


@pytest.mark.skipif(_numbat_bin() is None, reason="numbat CLI not installed")
def test_numbat_cli_live_jail_zero_hits(tmp_path: Path) -> None:
    exe = _numbat_bin()
    assert exe is not None
    out = tmp_path / "jail-events.ndjson"
    _generate_live_jail(out)
    argv = [exe, "rules", "test", "--fixture", str(out), "--expect-none"]
    proc = subprocess.run(argv, check=False, capture_output=True, text=True)
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, combined
    assert "exfil.curl_post_file" not in proc.stdout
    assert "secrets.read_private_key" not in proc.stdout


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
