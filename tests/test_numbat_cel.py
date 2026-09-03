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


def test_non_dict_cfg_is_treated_as_disabled():
    assert evaluate_cel_monitor(query_hash="abc", cfg=None) == []
    assert evaluate_cel_monitor(query_hash="abc", cfg="not-a-dict") == []  # type: ignore[arg-type]


def test_enabled_with_empty_or_non_list_rules_returns_empty(tmp_path: Path):
    cfg = _cel_cfg(tmp_path, enabled=True, rules=[])
    assert evaluate_cel_monitor(query_hash="abc", cfg=cfg) == []
    cfg["numbat"]["cel"]["rules"] = "not-a-list"
    assert evaluate_cel_monitor(query_hash="abc", cfg=cfg) == []


def test_celpy_import_failure_is_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import builtins
    import sys

    for key in [k for k in sys.modules if k == "celpy" or k.startswith("celpy.")]:
        monkeypatch.delitem(sys.modules, key, raising=False)
    real_import = builtins.__import__

    def _import(name, g=None, loc=None, fromlist=(), level=0):
        if name == "celpy" or name.startswith("celpy."):
            raise ImportError("No module named 'celpy'")
        return real_import(name, g, loc, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _import)
    cfg = _cel_cfg(tmp_path, enabled=True, rules=['query_hash == "abc"'])
    assert evaluate_cel_monitor(query_hash="abc", cfg=cfg) == []


def test_activation_build_failure_falls_back_to_native_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    pytest.importorskip("celpy")
    import celpy

    def _boom(_value):
        raise RuntimeError("json_to_cel failed")

    monkeypatch.setattr(celpy, "json_to_cel", _boom)
    cfg = _cel_cfg(tmp_path, enabled=True, rules=['query_hash == "abc"'])
    assert evaluate_cel_monitor(query_hash="abc", cfg=cfg) == [0]


def test_rule_evaluation_failure_is_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("celpy")
    import utils.numbat_cel as numbat_cel

    class _BoomProgram:
        def evaluate(self, _activation):
            raise RuntimeError("eval failed")

    monkeypatch.setattr(numbat_cel, "_compile_rules", lambda _rules: [(0, _BoomProgram())])
    cfg = _cel_cfg(tmp_path, enabled=True, rules=['query_hash == "abc"'])
    assert evaluate_cel_monitor(query_hash="abc", cfg=cfg) == []


def test_slow_rule_logs_budget_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog):
    pytest.importorskip("celpy")
    import logging

    import utils.numbat_cel as numbat_cel

    class _SlowProgram:
        def evaluate(self, _activation):
            return True

    times = iter([100.0, 100.05])  # 50ms elapsed vs 20ms budget
    monkeypatch.setattr(numbat_cel.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(numbat_cel, "_compile_rules", lambda _rules: [(0, _SlowProgram())])
    cfg = _cel_cfg(tmp_path, enabled=True, rules=['true'])
    with caplog.at_level(logging.WARNING, logger="cyclaw.numbat_cel"):
        matches = evaluate_cel_monitor(query_hash="abc", cfg=cfg)
    assert matches == [0]
    assert any("exceeded" in r.message for r in caplog.records)


def test_monitor_emitter_import_failure_is_fail_soft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    pytest.importorskip("celpy")
    import builtins
    import sys

    cfg = _cel_cfg(tmp_path, enabled=True, rules=['query_hash == "abc"'])
    for key in [k for k in list(sys.modules) if k.startswith("utils.numbat_emitter")]:
        monkeypatch.delitem(sys.modules, key, raising=False)
    real_import = builtins.__import__

    def _import(name, g=None, loc=None, fromlist=(), level=0):
        if name == "utils.numbat_emitter" or (
            name == "utils" and fromlist and "numbat_emitter" in fromlist
        ):
            raise ImportError("numbat_emitter unavailable")
        return real_import(name, g, loc, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _import)
    monitor_request(query_hash="abc", cfg=cfg)  # must not raise
    assert _lines(Path(cfg["numbat"]["output_path"])) == []


def test_monitor_emit_failure_is_fail_soft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("celpy")
    cfg = _cel_cfg(tmp_path, enabled=True, rules=['query_hash == "abc"'])

    def _boom(*_a, **_k):
        raise RuntimeError("emit failed")

    monkeypatch.setattr("utils.numbat_emitter.emit_numbat_event", _boom)
    monitor_request(query_hash="abc", answer_model="local", cfg=cfg)  # must not raise
    assert _lines(Path(cfg["numbat"]["output_path"])) == []
