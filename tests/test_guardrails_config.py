"""Tests for guardrails.config -- loader, validation, and opt-in defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from guardrails.config import _REPO_ROOT, GuardrailsConfig, load_guardrails_config
from guardrails.errors import GuardrailsConfigError
from utils.logger import reset_config_cache


def _write_config(tmp_path, guardrails_block) -> str:
    cfg = {"guardrails": guardrails_block} if guardrails_block is not None else {}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    reset_config_cache()
    return str(path)


def test_defaults_are_opt_in():
    gc = GuardrailsConfig()
    assert gc.enabled is False
    assert gc.engine == "openai"
    # Anchored to the repo root, not left cwd-relative -- see
    # test_metrics_path_is_anchored_to_repo_root for why that matters.
    assert gc.metrics_path == str(_REPO_ROOT / "logs" / "guardrails.jsonl")
    assert "soul" in gc.soul_topics


def test_metrics_path_is_anchored_to_repo_root(tmp_path, monkeypatch):
    """A relative metrics_path must not follow the process cwd.

    Regression: nemo_config_dir was anchored to the repo root so the CLI works
    from any directory, but metrics_path was not. Started from a service
    manager or a Windows double-click, every guardrail decision appended to
    <cwd>/logs/guardrails.jsonl instead of the repo's -- and because
    GuardrailMetrics swallows OSError to keep telemetry from becoming policy,
    an unwritable cwd made the stream vanish silently while the rail kept
    enforcing.
    """
    monkeypatch.chdir(tmp_path)
    gc = GuardrailsConfig()
    assert Path(gc.metrics_path).is_absolute()
    assert Path(gc.metrics_path) == _REPO_ROOT / "logs" / "guardrails.jsonl"
    assert tmp_path not in Path(gc.metrics_path).parents


def test_absolute_metrics_path_is_left_alone(tmp_path):
    """An operator who configures an absolute path gets exactly that path."""
    target = tmp_path / "elsewhere" / "g.jsonl"
    assert GuardrailsConfig(metrics_path=str(target)).metrics_path == str(target)


def test_empty_metrics_path_is_rejected():
    """Silently writing to Path("") is worse than refusing to start; disabling
    persistence is GuardrailMetrics(persist=False), not an empty path."""
    with pytest.raises(GuardrailsConfigError):
        GuardrailsConfig(metrics_path="")


def test_absent_block_returns_disabled(tmp_path):
    path = _write_config(tmp_path, None)
    gc = load_guardrails_config(path)
    assert gc.enabled is False
    assert gc._unknown_keys == []
    reset_config_cache()


def test_enabled_block_loads(tmp_path):
    path = _write_config(tmp_path, {"enabled": True, "model": "custom-7b"})
    gc = load_guardrails_config(path)
    assert gc.enabled is True
    assert gc.model == "custom-7b"
    reset_config_cache()


def test_unknown_keys_collected_not_fatal(tmp_path):
    path = _write_config(tmp_path, {"enabled": True, "typo_key": 1})
    gc = load_guardrails_config(path)
    assert gc._unknown_keys == ["typo_key"]
    reset_config_cache()


def test_invalid_engine_raises(tmp_path):
    path = _write_config(tmp_path, {"engine": "anthropic"})
    with pytest.raises(GuardrailsConfigError):
        load_guardrails_config(path)
    reset_config_cache()


def test_invalid_threshold_raises(tmp_path):
    path = _write_config(tmp_path, {"hallucination_threshold": 1.5})
    with pytest.raises(GuardrailsConfigError):
        load_guardrails_config(path)
    reset_config_cache()


def test_invalid_base_url_raises(tmp_path):
    path = _write_config(tmp_path, {"base_url": "ftp://nope"})
    with pytest.raises(GuardrailsConfigError):
        load_guardrails_config(path)
    reset_config_cache()


def test_non_mapping_block_raises(tmp_path):
    path = _write_config(tmp_path, ["not", "a", "dict"])
    with pytest.raises(GuardrailsConfigError):
        load_guardrails_config(path)
    reset_config_cache()


def test_nemo_config_dir_resolved_to_repo_files():
    # The default dir resolves to the real, present config.yml + rails.co.
    gc = GuardrailsConfig()
    assert gc.nemo_config_present is True


def test_repo_config_yaml_block_is_valid():
    # The guardrails: block shipped in the repo config.yaml must load cleanly.
    reset_config_cache()
    gc = load_guardrails_config("config.yaml")
    assert gc.enabled is False  # ships disabled by default
    assert gc.nemo_config_present is True
    reset_config_cache()
