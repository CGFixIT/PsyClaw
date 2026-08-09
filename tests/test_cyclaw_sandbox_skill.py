"""Unit checks for the Codex cyclaw-sandbox-test helper scripts."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

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
    mock = _load_script("mock_ollama.py")

    ids = {item["id"] for item in json.loads(mock.MODELS_RESP)["data"]}

    assert {mock.MODEL_ID, mock.GROK_MODEL_ID, mock.CLAUDE_MODEL_ID} <= ids


def test_mock_claude_message_shape_returns_text_content() -> None:
    mock = _load_script("mock_ollama.py")

    payload = json.loads(mock._make_claude_completion("claude provider smoke"))

    assert payload["model"] == mock.CLAUDE_MODEL_ID
    assert payload["content"][0]["type"] == "text"
    assert "Mock Claude API" in payload["content"][0]["text"]


def test_runner_temporarily_points_providers_at_mock(tmp_path: Path) -> None:
    runner = _load_script("run_sandbox_test.py")
    repo_config = ROOT / "config.yaml"
    original_text = repo_config.read_text(encoding="utf-8")
    (tmp_path / "config.yaml").write_text(original_text, encoding="utf-8")
    results = []

    original = runner._enable_mock_providers(tmp_path, results)
    patched = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    config = yaml.safe_load(patched)

    assert original == original_text
    assert config["app"]["mode"] == "hybrid"
    assert config["models"]["local_llm"]["provider"] == "ollama"
    assert config["models"]["local_llm"]["base_url"] == f"{runner.MOCK_URL}/v1"
    assert config["models"]["local_llm"]["model"] == runner.MODEL_ID
    assert config["models"]["grok"]["enabled"] is True
    assert config["models"]["claude"]["enabled"] is True
    assert config["models"]["grok"]["base_url"] == f"{runner.MOCK_URL}/v1"
    assert config["models"]["claude"]["base_url"] == f"{runner.MOCK_URL}/v1"

    runner._restore_config(tmp_path, original, results)

    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == original_text
    assert [r.status for r in results] == ["PASS"]


def test_runner_stops_when_clone_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _load_script("run_sandbox_test.py")
    args = SimpleNamespace(in_place=False, work_root=str(tmp_path), branch="main", repo_url="https://invalid")
    monkeypatch.setattr(
        runner,
        "_run",
        lambda *args, **kwargs: runner.Result("clone origin/main", "FAIL", "offline: https://invalid"),
    )

    with pytest.raises(RuntimeError, match="offline: <repo-url>") as exc_info:
        runner._clone_or_use_repo(args, [])
    assert "https://invalid" not in str(exc_info.value)


def test_runner_uses_the_macos_torch_profile_without_mutating_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_script("run_sandbox_test.py")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text(
        "--extra-index-url https://download.pytorch.org/whl/cpu\n"
        "torch==2.13.0+cpu\n"
        "fastapi==0.1\n",
        encoding="utf-8",
    )
    (repo / "constraints.txt").write_text("torch==2.13.0+cpu\nfastapi==0.1\n", encoding="utf-8")
    captured: list[tuple[str, list[str], str, str]] = []

    def fake_run(name: str, cmd: list[str], *_args: object, **_kwargs: object) -> object:
        if name == "install requirements macOS":
            requirements = Path(cmd[cmd.index("-r") + 1]).read_text(encoding="utf-8")
            constraints = Path(cmd[cmd.index("-c") + 1]).read_text(encoding="utf-8")
            captured.append((name, cmd, requirements, constraints))
        else:
            captured.append((name, cmd, "", ""))
        return runner.Result(name, "PASS", "")

    monkeypatch.setattr(runner.sys, "platform", "darwin")
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    monkeypatch.setattr(runner, "_run", fake_run)
    args = SimpleNamespace(skip_install=False, skip_index=True, index_timeout=1)

    runner._prepare_repo(repo, args, [], {})

    torch = next(item for item in captured if item[0] == "install torch macOS")
    requirements = next(item for item in captured if item[0] == "install requirements macOS")
    assert "torch==2.13.0" in torch[1]
    assert "torch==2.13.0+cpu" not in torch[1]
    assert "torch==" not in requirements[2]
    assert "download.pytorch.org" not in requirements[2]
    assert "torch==" not in requirements[3]
    assert (repo / "requirements.txt").read_text(encoding="utf-8").startswith("--extra-index-url")


def test_runner_keeps_cpu_torch_profile_off_macos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _load_script("run_sandbox_test.py")
    repo = tmp_path / "repo"
    repo.mkdir()
    captured: list[tuple[str, list[str]]] = []

    def fake_run(name: str, cmd: list[str], *_args: object, **_kwargs: object) -> object:
        captured.append((name, cmd))
        return runner.Result(name, "PASS", "")

    monkeypatch.setattr(runner.sys, "platform", "win32")
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    monkeypatch.setattr(runner, "_run", fake_run)
    args = SimpleNamespace(skip_install=False, skip_index=True, index_timeout=1)

    runner._prepare_repo(repo, args, [], {})

    torch = next(item for item in captured if item[0] == "install torch cpu")
    requirements = next(item for item in captured if item[0] == "install requirements")
    assert "torch==2.13.0+cpu" in torch[1]
    assert "https://download.pytorch.org/whl/cpu" in torch[1]
    assert requirements[1][requirements[1].index("-r") + 1] == "requirements.txt"


def test_runner_includes_gate_and_harness_runtime_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_script("run_sandbox_test.py")
    names: list[str] = []
    environments: dict[str, dict[str, str]] = {}

    def fake_run(name: str, _cmd: object, _cwd: object, env: dict[str, str], _timeout: object) -> object:
        names.append(name)
        environments[name] = env
        return runner.Result(name, "PASS", "")

    monkeypatch.setattr(runner, "_run", fake_run)

    runner._run_targeted_tests(Path("python"), tmp_path, {"CYCLAW_HOME": "operator-home"}, 1)

    assert names[:2] == ["gate runtime contract", "harness runtime contract"]
    assert environments["harness runtime contract"]["CYCLAW_HOME"] != "operator-home"


def test_runner_rejects_unsupported_python_before_cloning(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _load_script("run_sandbox_test.py")

    monkeypatch.setattr(runner.sys, "version_info", (3, 11, 9))

    assert runner.main() == 2
