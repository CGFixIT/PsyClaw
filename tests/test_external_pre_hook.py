"""Unit tests for utils/external_pre_hook.py fail-closed branches.

No wall-clock sleeps: subprocess.run is monkeypatched.
"""

from __future__ import annotations

import subprocess

from utils.external_pre_hook import (
    DEFAULT_TIMEOUT_SEC,
    MAX_TIMEOUT_SEC,
    MIN_TIMEOUT_SEC,
    _normalize_timeout,
    run_pre_action_hook,
)


def test_invalid_command_type_denies():
    cfg = {"policy": {"fallback": {"pre_action_hook": {"enabled": True, "command": "not-a-list"}}}}
    result = run_pre_action_hook("grok", "grok-4.5", "abc", cfg)
    assert result["verdict"] == "deny"
    assert "invalid hook command" in result["reason"]


def test_list_with_non_string_element_denies():
    cfg = {"policy": {"fallback": {"pre_action_hook": {"enabled": True, "command": ["python", 123]}}}}
    result = run_pre_action_hook("grok", "grok-4.5", "abc", cfg)
    assert result["verdict"] == "deny"


def test_timeout_expired_denies(monkeypatch):
    cfg = {"policy": {"fallback": {"pre_action_hook": {"enabled": True, "command": ["sleep", "60"]}}}}

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 5))

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    result = run_pre_action_hook("grok", "grok-4.5", "abc", cfg)
    assert result["verdict"] == "deny"
    assert "timed out" in result["reason"]


def test_normalize_timeout_clamps_and_defaults():
    assert _normalize_timeout(None) == DEFAULT_TIMEOUT_SEC
    assert _normalize_timeout("not-an-int") == DEFAULT_TIMEOUT_SEC
    assert _normalize_timeout(0) == MIN_TIMEOUT_SEC
    assert _normalize_timeout(-5) == MIN_TIMEOUT_SEC
    assert _normalize_timeout(100) == MAX_TIMEOUT_SEC
    assert _normalize_timeout(7) == 7


def test_hook_disabled_returns_allow():
    cfg = {"policy": {"fallback": {"pre_action_hook": {"enabled": False, "command": ["true"]}}}}
    assert run_pre_action_hook("grok", "grok-4.5", "abc", cfg) == {"verdict": "allow"}


def test_missing_config_returns_allow():
    assert run_pre_action_hook("grok", "grok-4.5", "abc", None) == {"verdict": "allow"}
