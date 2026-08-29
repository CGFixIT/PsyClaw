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


def test_url_userinfo_rejected_without_echoing_secret(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"api_base": "https://user:supersecret@opentweet.io"})
    with pytest.raises(OpenTweetConfigError) as exc:
        load_opentweet_config(path)
    assert "credentials" in exc.value.message
    blob = f"{exc.value.message}{exc.value.details}"
    assert "supersecret" not in blob
    assert "user:supersecret" not in blob


def test_loopback_userinfo_rejected(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {"query": {"base_url": "http://user:supersecret@127.0.0.1:8787"}},
    )
    with pytest.raises(OpenTweetConfigError) as exc:
        load_opentweet_config(path)
    assert "credentials" in exc.value.message
    assert "supersecret" not in str(exc.value.details)


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


def test_shipped_query_timeout_clears_graph_deadline() -> None:
    # 790 = api.graph_timeout_sec (780) + 10s: the channel client must lose the
    # race so the server's diagnosable 504 GRAPH_TIMEOUT arrives instead of a
    # client abort (same pattern as static/terminal.js's queryDeadlineMs).
    shipped_path = Path(__file__).resolve().parent.parent / "config.yaml"
    shipped = yaml.safe_load(shipped_path.read_text(encoding="utf-8"))
    assert shipped["opentweet"]["query"]["timeout_sec"] == 790
    assert shipped["opentweet"]["query"]["timeout_sec"] > shipped["api"]["graph_timeout_sec"]
