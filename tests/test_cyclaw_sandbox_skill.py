"""Unit checks for the Codex cyclaw-sandbox-test helper scripts."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".codex" / "skills" / "cyclaw-sandbox-test" / "scripts"


def _load_script(filename: str):
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_mock_model_list_advertises_local_grok_and_claude_ids() -> None:
    mock = _load_script("mock_lmstudio.py")

    ids = {item["id"] for item in json.loads(mock.MODELS_RESP)["data"]}

    assert {mock.MODEL_ID, mock.GROK_MODEL_ID, mock.CLAUDE_MODEL_ID} <= ids


def test_mock_claude_message_shape_returns_text_content() -> None:
    mock = _load_script("mock_lmstudio.py")

    payload = json.loads(mock._make_claude_completion("claude provider smoke"))

    assert payload["model"] == mock.CLAUDE_MODEL_ID
    assert payload["content"][0]["type"] == "text"
    assert "Mock Claude API" in payload["content"][0]["text"]


def test_runner_temporarily_points_external_providers_at_mock(tmp_path: Path) -> None:
    runner = _load_script("run_sandbox_test.py")
    repo_config = ROOT / "config.yaml"
    original_text = repo_config.read_text(encoding="utf-8")
    (tmp_path / "config.yaml").write_text(original_text, encoding="utf-8")
    results = []

    original = runner._enable_mock_external_providers(tmp_path, results)
    patched = (tmp_path / "config.yaml").read_text(encoding="utf-8")

    assert original == original_text
    assert '  mode: "hybrid"' in patched
    assert patched.count('base_url: "http://127.0.0.1:1234/v1"') >= 3

    runner._restore_config(tmp_path, original, results)

    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == original_text
    assert [r.status for r in results] == ["PASS"]
