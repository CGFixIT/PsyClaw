"""Tests for guardrails.selftest -- the operator pre-flight check runner.

Focus: the reported pass/total is consistent across the success and the
config-failure paths (the failure path previously reported 6 checks, the
success path 7).
"""

from __future__ import annotations

from guardrails import selftest
from guardrails.errors import GuardrailsConfigError


def test_self_test_reports_seven_checks():
    """The normal run always enumerates the full 7-check ladder."""
    passed, total, lines = selftest.run_self_test()
    assert total == 7
    assert len(lines) == 7
    assert 0 <= passed <= total


def test_self_test_total_consistent_on_config_failure(monkeypatch):
    """A config error must not shrink the denominator (was 6 before the fix)."""

    def _boom(config_path: str = "config.yaml"):
        raise GuardrailsConfigError("invalid guardrails block")

    monkeypatch.setattr(selftest, "load_guardrails_config", _boom)
    passed, total, lines = selftest.run_self_test()

    assert total == 7
    assert len(lines) == 7
    # Check 01 is the real failure; 02..07 are skips, which count as passed.
    assert "[FAIL] 01" in lines[0]
    assert any("07." in ln for ln in lines)
    assert passed == 6


def test_self_test_fail_arms_for_heuristic_checks(monkeypatch):
    """Force each heuristic fail branch while keeping config valid."""
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        nemo_config_present=False,
        nemo_config_dir="/missing",
        metrics_path="logs/guardrails.jsonl",
        soul_topics=["soul"],
    )
    monkeypatch.setattr(selftest, "load_guardrails_config", lambda *_a, **_k: cfg)
    monkeypatch.setattr(selftest, "is_soul_topic", lambda *_a, **_k: False)
    monkeypatch.setattr(selftest, "detect_soul_mutation_intent", lambda *_a, **_k: False)
    monkeypatch.setattr(selftest, "grounding_score", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(selftest, "scan_injection", lambda *_a, **_k: [])

    class _BadMetrics:
        def __init__(self, *_a, **_k):
            self.counters = {"blocked_generation": 0}

        def record_blocked(self, **_k):
            return None

    monkeypatch.setattr(selftest, "GuardrailMetrics", _BadMetrics)
    monkeypatch.setattr("guardrails.integration.NEMO_AVAILABLE", True)

    passed, total, lines = selftest.run_self_test()
    assert total == 7
    joined = "\n".join(lines)
    assert "[FAIL] 02" in joined
    assert "[FAIL] 03" in joined
    assert "[FAIL] 04" in joined
    assert "[FAIL] 05" in joined
    assert "[FAIL] 06" in joined
    assert "[OK]" in lines[-1] or "live rails available" in joined


def test_self_test_metrics_exception_is_fail(monkeypatch):
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        nemo_config_present=True,
        nemo_config_dir="guardrails/config",
        metrics_path="logs/guardrails.jsonl",
        soul_topics=["soul", "personality"],
    )
    monkeypatch.setattr(selftest, "load_guardrails_config", lambda *_a, **_k: cfg)

    class _BoomMetrics:
        def __init__(self, *_a, **_k):
            raise RuntimeError("metrics boom")

    monkeypatch.setattr(selftest, "GuardrailMetrics", _BoomMetrics)
    monkeypatch.setattr("guardrails.integration.NEMO_AVAILABLE", False)
    passed, total, lines = selftest.run_self_test()
    assert total == 7
    assert any("[FAIL] 06" in ln and "metrics boom" in ln for ln in lines)
