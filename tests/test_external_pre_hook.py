"""Unit tests for utils/external_pre_hook.py fail-closed branches.

No wall-clock sleeps: subprocess.run is monkeypatched.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from utils.external_pre_hook import (
    DEFAULT_TIMEOUT_SEC,
    MAX_TIMEOUT_SEC,
    MIN_TIMEOUT_SEC,
    _hook_cfg,
    _include_query_hash,
    _normalize_timeout,
    run_pre_action_hook,
)
from utils.numbat_emitter import close_numbat_handles

_TEST_QUERY_HASH = "a" * 64


@pytest.fixture(autouse=True)
def _release_numbat_handles():
    """Release cached file handles so tmp_path teardown succeeds on Windows."""
    yield
    close_numbat_handles()


def _hook_config(
    tmp_path: Path,
    *,
    enabled: bool = True,
    emit_verdict: bool = True,
    command: tuple[str, ...] = ("false",),
) -> dict:
    out = tmp_path / "numbat-events.ndjsonl"
    return {
        "numbat": {"enabled": True, "output_path": str(out)},
        "policy": {
            "fallback": {
                "pre_action_hook": {
                    "enabled": enabled,
                    "command": list(command),
                    "emit_verdict": emit_verdict,
                }
            }
        },
    }


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


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


def test_emit_verdict_false_no_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _hook_config(tmp_path, emit_verdict=False)

    def _exit_2(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=2, stdout=b"", stderr=b"deny")

    monkeypatch.setattr(subprocess, "run", _exit_2)
    result = run_pre_action_hook("grok", "grok-4.5", "abc", cfg)
    assert result["verdict"] == "deny"
    assert _lines(Path(cfg["numbat"]["output_path"])) == []


def test_emit_verdict_true_exit_2_emits_permission_denied(tmp_path: Path):
    cfg = _hook_config(
        tmp_path,
        command=(
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('blocked'); sys.exit(2)",
        ),
    )
    result = run_pre_action_hook("grok", "grok-4.5", _TEST_QUERY_HASH, cfg)
    assert result["verdict"] == "deny"
    assert "blocked" in result["reason"]

    records = _lines(Path(cfg["numbat"]["output_path"]))
    assert len(records) == 1
    rec = records[0]
    assert rec["event_type"] == "permission.denied"
    assert rec["decision"] == "denied"
    assert rec["model"] == "grok-4.5"
    assert rec["model_provider"] == "xai"
    assert rec["tool_name"] == "external_llm_call"
    assert rec["approval_reason"] == "hook_denied"
    assert rec["entrypoint"] == "cyclaw"
    assert "cyclaw" in rec["tags"]
    # Schema 0.3.0 additionalProperties:false — the hash rides inside
    # content_preview, never as a top-level field.
    assert "query_hash" not in rec
    assert json.loads(rec["content_preview"]) == {"query_hash": _TEST_QUERY_HASH}
    assert "blocked" not in json.dumps(rec)


def test_emit_verdict_true_timeout_emits_network_indicator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _hook_config(tmp_path, command=("sleep", "60"))

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 5))

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    result = run_pre_action_hook("claude", "claude-sonnet-4", _TEST_QUERY_HASH, cfg)
    assert result["verdict"] == "deny"

    records = _lines(Path(cfg["numbat"]["output_path"]))
    assert len(records) == 1
    rec = records[0]
    assert rec["event_type"] == "network.indicator"
    assert rec["confidence"] == "low"
    assert rec["model_provider"] == "anthropic"
    assert "query_hash" not in rec
    assert json.loads(rec["content_preview"]) == {"query_hash": _TEST_QUERY_HASH}


def test_emit_verdict_true_other_exit_emits_network_indicator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _hook_config(tmp_path)

    def _exit_7(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=7, stdout=b"boom", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _exit_7)
    result = run_pre_action_hook("grok", "grok-4.5", _TEST_QUERY_HASH, cfg)
    assert result["verdict"] == "deny"

    records = _lines(Path(cfg["numbat"]["output_path"]))
    assert len(records) == 1
    rec = records[0]
    assert rec["event_type"] == "network.indicator"
    assert rec["confidence"] == "low"
    assert "query_hash" not in rec
    assert json.loads(rec["content_preview"]) == {"query_hash": _TEST_QUERY_HASH}


def test_emit_verdict_invalid_query_hash_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _hook_config(tmp_path)

    def _exit_7(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=7, stdout=b"boom", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _exit_7)
    result = run_pre_action_hook("grok", "grok-4.5", "abc", cfg)
    assert result["verdict"] == "deny"

    records = _lines(Path(cfg["numbat"]["output_path"]))
    assert len(records) == 1
    rec = records[0]
    # Non-64-hex query_hash is dropped: no top-level field, no content_preview.
    assert "query_hash" not in rec
    assert "query_hash" not in rec.get("content_preview", "")


def test_emit_verdict_query_hash_omitted_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """logging.audit_fields.include_query_hash: false must suppress content_preview.

    Regression for a Codex review finding on PR #1183: content_preview was
    gated only on hash format, not on the operator's opt-out -- silently
    reintroducing an unsalted, dictionary-guessable identifier into the
    Numbat stream even when include_query_hash is explicitly false.
    """
    cfg = _hook_config(tmp_path)
    cfg["logging"] = {"audit_fields": {"include_query_hash": False}}

    def _exit_2(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=2, stdout=b"", stderr=b"deny")

    monkeypatch.setattr(subprocess, "run", _exit_2)
    result = run_pre_action_hook("grok", "grok-4.5", _TEST_QUERY_HASH, cfg)
    assert result["verdict"] == "deny"

    records = _lines(Path(cfg["numbat"]["output_path"]))
    assert len(records) == 1
    rec = records[0]
    # A valid 64-hex hash is still dropped -- the opt-out wins even though
    # the format check alone would have allowed it through.
    assert "query_hash" not in rec
    assert "content_preview" not in rec


def test_payload_query_hash_present_regardless_of_audit_hash_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The hook's stdin payload always carries query_hash -- a documented,
    unconditional contract (config.yaml:255-260, graph.py's pre_action_hook_node
    docstring, docs/plans/NUMBAT_AND_ALWAYS_ON_ROADMAP.md Step 2). Regression
    for a Codex review finding on PR #1187: an earlier revision of this test
    asserted the opposite (payload omits query_hash under
    logging.audit_fields.include_query_hash: false), which both broke that
    documented contract and could make a fail-closed hook deny every call.

    include_query_hash is not a "hide the hash everywhere" switch -- per
    utils/logger.py::audit_log, false means the RAW query text is left in the
    primary audit.jsonl record (see
    test_disabling_hashing_persists_raw_text__documented_leak), so a hook
    receiving a one-way hash of that same query on stdin discloses nothing
    the operator's own choice hasn't already exposed on the primary log.
    """
    captured: list[bytes] = []

    def _capture(*args, **kwargs):
        captured.append(kwargs["input"])
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _capture)

    cfg = _hook_config(tmp_path)
    assert run_pre_action_hook("grok", "grok-4.5", _TEST_QUERY_HASH, cfg) == {"verdict": "allow"}
    assert json.loads(captured[-1])["query_hash"] == _TEST_QUERY_HASH

    cfg["logging"] = {"audit_fields": {"include_query_hash": False}}
    assert run_pre_action_hook("grok", "grok-4.5", _TEST_QUERY_HASH, cfg) == {"verdict": "allow"}
    optout_payload = json.loads(captured[-1])
    assert optout_payload["query_hash"] == _TEST_QUERY_HASH
    assert optout_payload["provider"] == "grok"


def test_emit_failure_is_fail_soft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _hook_config(tmp_path)

    def _exit_2(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=2, stdout=b"", stderr=b"deny")

    monkeypatch.setattr(subprocess, "run", _exit_2)

    def _boom(**kwargs):
        raise RuntimeError("emit failed")

    monkeypatch.setattr("utils.numbat_emitter.emit_numbat_event", _boom)
    result = run_pre_action_hook("grok", "grok-4.5", "abc", cfg)
    assert result["verdict"] == "deny"
    assert _lines(Path(cfg["numbat"]["output_path"])) == []


def test_hook_cfg_non_dict_fallback_or_block():
    assert _hook_cfg({"policy": {"fallback": "nope"}}) == {}
    assert _hook_cfg({"policy": {"fallback": {"pre_action_hook": "nope"}}}) == {}


def test_include_query_hash_non_dict_branches():
    assert _include_query_hash(None) is True
    assert _include_query_hash({"logging": "nope"}) is True
    assert _include_query_hash({"logging": {"audit_fields": "nope"}}) is True


def test_enabled_with_empty_command_allows():
    cfg = {"policy": {"fallback": {"pre_action_hook": {"enabled": True, "command": []}}}}
    assert run_pre_action_hook("grok", "grok-4.5", "abc", cfg) == {"verdict": "allow"}


def test_unsupported_fail_mode_still_enforces(monkeypatch: pytest.MonkeyPatch):
    cfg = {
        "policy": {
            "fallback": {
                "pre_action_hook": {
                    "enabled": True,
                    "command": ["hook"],
                    "fail_mode": "monitor",
                }
            }
        }
    }

    def _exit_2(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=2, stdout=b"", stderr=b"deny")

    monkeypatch.setattr(subprocess, "run", _exit_2)
    result = run_pre_action_hook("grok", "grok-4.5", "abc", cfg)
    assert result["verdict"] == "deny"


def test_oserror_running_hook_denies_and_emits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg = _hook_config(tmp_path)

    def _boom(*_a, **_k):
        raise OSError("exec format error")

    monkeypatch.setattr(subprocess, "run", _boom)
    result = run_pre_action_hook("grok", "grok-4.5", _TEST_QUERY_HASH, cfg)
    assert result["verdict"] == "deny"
    assert "hook execution failed" in result["reason"]
    records = _lines(Path(cfg["numbat"]["output_path"]))
    assert len(records) == 1
    assert records[0]["event_type"] == "network.indicator"
    assert "hook_error" in records[0].get("tags", [])


def test_emitter_import_failure_is_fail_soft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import builtins
    import sys

    cfg = _hook_config(tmp_path)

    def _exit_2(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=2, stdout=b"", stderr=b"deny")

    monkeypatch.setattr(subprocess, "run", _exit_2)
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
    result = run_pre_action_hook("grok", "grok-4.5", "abc", cfg)
    assert result["verdict"] == "deny"
