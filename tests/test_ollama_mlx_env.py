"""Contract tests for macos/ollama-mlx.env.

The file is sourced by a POSIX shell. No secrets, no model-tag drift from
config.yaml's shipped MLX default, every documented knob present.
"""

from __future__ import annotations

from pathlib import Path

_ENV = Path(__file__).resolve().parent.parent / "macos" / "ollama-mlx.env"
_SETUP = Path(__file__).resolve().parent.parent / "macos" / "setup-from-clone.sh"


def _env_text() -> str:
    return _ENV.read_text(encoding="utf-8")


def test_env_file_exists_and_has_no_secrets() -> None:
    text = _env_text()
    assert text.startswith("# CyClaw")
    for needle in ("API_KEY", "TOKEN", "PASSWORD", "SECRET"):
        assert needle not in text


def test_shipped_knobs_match_docs() -> None:
    text = _env_text()
    assert "OLLAMA_CONTEXT_LENGTH=16384" in text
    assert "OLLAMA_KEEP_ALIVE=30m" in text
    assert "OLLAMA_MAX_LOADED_MODELS=1" in text
    assert "OLLAMA_NUM_PARALLEL=1" in text
    assert "OLLAMA_FLASH_ATTENTION=1" in text
    assert "OLLAMA_KV_CACHE_TYPE=q8_0" in text


def test_does_not_override_the_shipped_mlx_tag() -> None:
    """Weight quant is the Ollama tag in config.yaml, not an env var."""
    text = _env_text()
    assert "qwen3.8:27b-mlx" not in [
        line.split("=", 1)[-1].strip()
        for line in text.splitlines()
        if line and not line.startswith("#") and "=" in line
    ]


def test_setup_from_clone_sources_the_env_file() -> None:
    script = _SETUP.read_text(encoding="utf-8")
    assert "ollama-mlx.env" in script
    assert "_ollama_apply_mlx_env" in script
