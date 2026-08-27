"""Tests for utils/numbat_cel.py — CEL monitor-only backend.

The default-off path must work without ``cel-python`` installed.  Tests that
need the evaluator skip when it is absent.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from utils.numbat_cel import evaluate_cel_monitor, monitor_request
from utils.numbat_emitter import close_numbat_handles


@pytest.fixture(autouse=True)
def _release_handles():
    """Release cached Numbat file handles so tmp_path teardown succeeds on Windows."""
    yield
    close_numbat_handles()


def _cel_cfg(tmp_path: Path, *, enabled: bool, rules: list[str]) -> dict:
    out = tmp_path / "numbat-events.ndjsonl"
    return {
        "numbat": {
            "enabled": True,
            "output_path": str(out),
            "cel": {"enabled": enabled, "rules": rules, "max_rule_ms": 20},
        }
    }


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_disabled_returns_empty_and_does_not_import_celpy(tmp_path: Path):
    """When cel.enabled is false, cel-python must not enter sys.modules."""
    cfg = _cel_cfg(tmp_path, enabled=False, rules=['query_hash == "abc"'])
    assert evaluate_cel_monitor(query_hash="abc", cfg=cfg) == []

    script = (
        "import sys\n"
        "from utils.numbat_cel import evaluate_cel_monitor\n"
        "cfg = {'numbat': {'cel': {'enabled': False, 'rules': []}}}\n"
        "evaluate_cel_monitor(query_hash='abc', cfg=cfg)\n"
        "sys.exit(0 if 'celpy' not in sys.modules else 1)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_enabled_rule_match_emits_permission_denied(tmp_path: Path):
    celpy = pytest.importorskip("celpy")
    cfg = _cel_cfg(
        tmp_path,
        enabled=True,
        rules=['query_hash == "abc"', 'top_score > 0.1'],
    )
    monitor_request(
        query_hash="abc",
        top_score=0.05,
        answer_model="qwen3.8:27b-mlx",
        guardrail_blocked=False,
        guardrail_rails=[],
        model_provider="ollama",
        source_hashes=["h1"],
        cfg=cfg,
    )
    out = Path(cfg["numbat"]["output_path"])
    records = _lines(out)
    assert len(records) == 1
    rec = records[0]
    assert rec["event_type"] == "permission.denied"
    assert rec["decision"] == "denied"
    assert rec["confidence"] == "low"
    assert rec["tool_name"] == "cel_monitor"
    assert rec["model"] == "qwen3.8:27b-mlx"
    assert rec["model_provider"] == "ollama"
    assert rec["approval_reason"] == "cel_rules_matched:0"
    assert "query" not in json.dumps(rec)
    assert "abc" not in json.dumps(rec)


def test_enabled_no_match_does_not_emit(tmp_path: Path):
    pytest.importorskip("celpy")
    cfg = _cel_cfg(tmp_path, enabled=True, rules=['query_hash == "xyz"'])
    monitor_request(query_hash="abc", cfg=cfg)
    out = Path(cfg["numbat"]["output_path"])
    assert _lines(out) == []


def test_malformed_rule_is_fail_open(tmp_path: Path):
    pytest.importorskip("celpy")
    cfg = _cel_cfg(tmp_path, enabled=True, rules=['this is not valid CEL'])
    # Must not raise, and must not emit because the rule cannot compile.
    monitor_request(query_hash="abc", cfg=cfg)
    out = Path(cfg["numbat"]["output_path"])
    assert _lines(out) == []


def test_bad_rule_type_is_skipped(tmp_path: Path):
    pytest.importorskip("celpy")
    cfg = _cel_cfg(tmp_path, enabled=True, rules=[123, 'query_hash == "abc"'])
    matches = evaluate_cel_monitor(query_hash="abc", cfg=cfg)
    assert matches == [1]
