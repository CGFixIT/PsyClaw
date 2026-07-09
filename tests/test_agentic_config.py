"""Self-contained tests for agentic.config (no conftest fixtures needed)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from agentic.config import AgenticConfig, load_agentic_config
from utils.errors import AgenticConfigError
from utils.logger import reset_config_cache

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = (REPO_ROOT / "data").resolve()


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write_config(tmp_path: Path, agentic_block: dict) -> str:
    cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}, "agentic": agentic_block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path)


def _base_block(**overrides: object) -> dict:
    block = {
        "enabled": True,
        "repo": "CGFixIT/CyClaw",
        "mode": "read",
        "writes_enabled": False,
        "gh_min_version": "2.40.0",
        "registry_path": "data/agentic/skills_registry.json",
    }
    block.update(overrides)
    return block


def test_valid_load(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))
    assert isinstance(cfg, AgenticConfig)
    assert cfg.repo == "CGFixIT/CyClaw"
    assert cfg.mode == "read"
    assert cfg.gh_min_tuple == (2, 40, 0)
    assert os.path.isabs(cfg.registry_path)
    assert cfg.deepagent_github.enabled is False
    assert cfg.harness_optimizer.enabled is False
    assert cfg.enabled is True  # type: ignore[attr-defined]


def test_relative_registry_path_is_repo_anchored_from_other_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_config(tmp_path, _base_block())
    outside = tmp_path / "outside"
    outside.mkdir()

    monkeypatch.chdir(outside)
    cfg = load_agentic_config(path)

    assert cfg.registry_path == str(DATA_ROOT / "agentic" / "skills_registry.json")


def test_defaults_disabled_when_absent_enabled(tmp_path: Path) -> None:
    block = _base_block()
    del block["enabled"]
    cfg = load_agentic_config(_write_config(tmp_path, block))
    # Conservative: agentic is disabled unless explicitly enabled.
    assert cfg.enabled is False  # type: ignore[attr-defined]


def test_rejects_non_bool_enabled(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(enabled="false")))


def test_rejects_non_bool_writes_enabled(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(writes_enabled="false")))


def test_deepagent_config_defaults_disabled_and_path_anchored(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))

    assert cfg.deepagent_github.provider == "lmstudio"
    assert cfg.deepagent_github.base_url == "http://localhost:1234/v1"
    assert cfg.deepagent_github.allow_deepagents_dependency is False
    assert cfg.deepagent_github.allow_shell_execution is False
    assert cfg.deepagent_github.workspace_root == str(DATA_ROOT / "agentic" / "workspaces")


def test_deepagent_config_rejects_shell_metachar_model(tmp_path: Path) -> None:
    block = _base_block(deepagent_github={"model": "good;bad"})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_deepagent_config_rejects_workspace_escape(tmp_path: Path) -> None:
    block = _base_block(deepagent_github={"workspace_root": "data/../outside"})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_harness_optimizer_config_defaults_disabled_and_path_anchored(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))

    assert cfg.harness_optimizer.max_iterations == 3
    assert cfg.harness_optimizer.require_human_confirm_for_accept is True
    assert cfg.harness_optimizer.allow_local_model_judge is False
    assert cfg.harness_optimizer.output_dir == str(DATA_ROOT / "agentic" / "harness_optimizer" / "runs")
    assert cfg.harness_optimizer.memory_dir == str(DATA_ROOT / "agentic" / "harness_optimizer" / "memory")


def test_harness_optimizer_config_rejects_bad_iterations(tmp_path: Path) -> None:
    block = _base_block(harness_optimizer={"max_iterations": 0})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_harness_optimizer_config_rejects_memory_escape(tmp_path: Path) -> None:
    block = _base_block(harness_optimizer={"memory_dir": "/tmp/cyclaw-memory"})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_missing_block_raises(tmp_path: Path) -> None:
    cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(AgenticConfigError):
        load_agentic_config(str(path))


def test_rejects_bad_repo_slug(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError) as exc:
        load_agentic_config(_write_config(tmp_path, _base_block(repo="not-a-slug")))
    assert exc.value.code == "AGENTIC_CONFIG_INVALID"


def test_rejects_repo_with_metacharacters(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(repo="evil/x; rm -rf")))


@pytest.mark.parametrize("repo", ["-x/y", "x/-y", "-owner/-name", "--repo/x"])
def test_rejects_repo_with_leading_dash_flag_injection(tmp_path: Path, repo: str) -> None:
    # A slug whose owner or name starts with '-' would flow positionally into
    # `gh repo view <repo>` and be parsed by gh as an option. '-' is not in
    # _SHELL_METACHARS, so the slug regex (first char anchored to alphanumeric)
    # is what must reject it.
    with pytest.raises(AgenticConfigError) as exc:
        load_agentic_config(_write_config(tmp_path, _base_block(repo=repo)))
    assert exc.value.code == "AGENTIC_CONFIG_INVALID"


@pytest.mark.parametrize("repo", ["CGFixIT/CyClaw", "a/b", "o.rg/my-repo.name", "x_y/z_1"])
def test_accepts_valid_repo_slugs(tmp_path: Path, repo: str) -> None:
    # Legitimate slugs (dots, hyphens, underscores in non-leading positions) still load.
    cfg = load_agentic_config(_write_config(tmp_path, _base_block(repo=repo)))
    assert cfg.repo == repo


def test_rejects_bad_mode(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(mode="delete")))


def test_rejects_bad_gh_min_version(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(gh_min_version="2.40")))


def test_rejects_registry_path_outside_data(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(registry_path="/tmp/x.json")))


def test_rejects_registry_path_escape(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(registry_path="data/../etc/x.json")))


def test_gh_runtime_defaults(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))
    assert cfg.gh_timeout_sec == 30
    assert cfg.gh_retries == 2


def test_gh_runtime_overrides(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block(gh_timeout_sec=60, gh_retries=0)))
    assert cfg.gh_timeout_sec == 60
    assert cfg.gh_retries == 0


def test_rejects_bad_gh_timeout(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(gh_timeout_sec=0)))


def test_rejects_negative_gh_retries(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(gh_retries=-1)))


def test_unknown_keys_collected_not_fatal(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block(typo="oops")))
    assert cfg._unknown_keys == ["typo"]  # type: ignore[attr-defined]


def test_to_dict_excludes_enabled(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))
    d = cfg.to_dict()
    assert "enabled" not in d  # plain attribute, not a dataclass field
    assert d["repo"] == "CGFixIT/CyClaw"
