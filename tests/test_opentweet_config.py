"""Self-contained tests for opentweet.config."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from opentweet.config import OpenTweetConfig, load_opentweet_config
from utils.errors import OpenTweetConfigError
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write_config(tmp_path: Path, block: dict | None) -> str:
    cfg: dict = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}}
    if block is not None:
        cfg["opentweet"] = block
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path)


def test_absent_block_is_disabled(tmp_path: Path) -> None:
    path = _write_config(tmp_path, None)
    cfg = load_opentweet_config(path)
    assert isinstance(cfg, OpenTweetConfig)
    assert cfg.enabled is False
    assert cfg.schedule_enabled is False
    assert cfg.topic_file == ""


def test_disabled_may_have_empty_topic_file(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"enabled": False, "topic_file": ""})
    cfg = load_opentweet_config(path)
    assert cfg.enabled is False


def test_enabled_requires_topic_file(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"enabled": True, "topic_file": ""})
    with pytest.raises(OpenTweetConfigError) as exc:
        load_opentweet_config(path)
    assert exc.value.code == "OPENTWEET_CONFIG_INVALID"


def test_valid_enabled_load(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "enabled": True,
            "topic_file": str(tmp_path / "topic.txt"),
            "query": {"base_url": "http://127.0.0.1:8787"},
        },
    )
    cfg = load_opentweet_config(path)
    assert cfg.enabled is True
    assert cfg.query.base_url == "http://127.0.0.1:8787"


def test_loopback_reject(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {"query": {"base_url": "http://example.com:8787"}},
    )
    with pytest.raises(OpenTweetConfigError):
        load_opentweet_config(path)


def test_unknown_key(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"publish_now": True})
    with pytest.raises(OpenTweetConfigError) as exc:
        load_opentweet_config(path)
    assert "unknown" in exc.value.message


def test_env_name_regex(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"api_key_env": "ot-key"})
    with pytest.raises(OpenTweetConfigError):
        load_opentweet_config(path)


def test_topic_file_rejects_shell_metacharacters(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"enabled": True, "topic_file": "foo;bar.txt"})
    with pytest.raises(OpenTweetConfigError):
        load_opentweet_config(path)


def test_bad_slot(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"schedule_slot": "25:00"})
    with pytest.raises(OpenTweetConfigError):
        load_opentweet_config(path)
