"""Unit tests for scripts/measure_local_llm_throughput.py.

No live Ollama. Loads the operator script by path so production packages
(gate/graph/llm) never import it (I6).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "measure_local_llm_throughput.py"
)


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location(
        "measure_local_llm_throughput", _SCRIPT
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_is_not_a_package_import() -> None:
    """Guard against someone moving this into llm/ and coupling it to /query."""
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "from graph" not in text
    assert "import gate" not in text
    assert "from llm.client" not in text


def test_tok_per_sec_nanoseconds(probe) -> None:
    # 256 tokens in 8.0s = 32 tok/s. Ollama reports ns.
    assert probe.tok_per_sec(256, 8_000_000_000) == 32.0


def test_tok_per_sec_rejects_zeros(probe) -> None:
    assert probe.tok_per_sec(0, 1_000_000_000) is None
    assert probe.tok_per_sec(10, 0) is None
    assert probe.tok_per_sec("nope", 1) is None


def test_rates_from_generate_fixture(probe) -> None:
    payload = {
        "model": "qwen3.8:27b-mlx",
        "prompt_eval_count": 400,
        "prompt_eval_duration": 1_000_000_000,  # 400 tok/s prefill
        "eval_count": 256,
        "eval_duration": 8_000_000_000,  # 32 tok/s decode
        "load_duration": 50_000_000,  # 50 ms
        "total_duration": 9_100_000_000,
    }
    row = probe.rates_from_generate(payload)
    assert row["prefill_tok_s"] == 400.0
    assert row["decode_tok_s"] == 32.0
    assert row["load_ms"] == 50.0
    assert row["model"] == "qwen3.8:27b-mlx"


def test_main_bad_args(probe) -> None:
    assert probe.main(["--num-predict", "0"]) == 1


def test_default_model_matches_shipped_tag(probe) -> None:
    assert probe.DEFAULT_MODEL == "qwen3.8:27b-mlx"
