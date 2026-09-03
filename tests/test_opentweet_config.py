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


def test_non_bool_enabled_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"enabled": "true"})
    with pytest.raises(OpenTweetConfigError, match="YAML boolean"):
        load_opentweet_config(path)


def test_non_int_timeout_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": {"timeout_sec": "long"}})
    with pytest.raises(OpenTweetConfigError, match="must be an integer"):
        load_opentweet_config(path)


def test_timeout_out_of_range_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": {"timeout_sec": 0}})
    with pytest.raises(OpenTweetConfigError, match="must be in"):
        load_opentweet_config(path)


def test_query_url_blank_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": {"base_url": ""}})
    with pytest.raises(OpenTweetConfigError, match="is required"):
        load_opentweet_config(path)


def test_query_url_shell_metachar_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": {"base_url": "http://127.0.0.1:8787|x"}})
    with pytest.raises(OpenTweetConfigError, match="disallowed characters"):
        load_opentweet_config(path)


def test_query_url_bad_scheme_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": {"base_url": "ftp://127.0.0.1:8787"}})
    with pytest.raises(OpenTweetConfigError, match="must be http or https"):
        load_opentweet_config(path)


def test_query_url_fragment_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": {"base_url": "http://127.0.0.1:8787/#frag"}})
    with pytest.raises(OpenTweetConfigError, match="query or fragment"):
        load_opentweet_config(path)


def test_url_for_details_redacts_userinfo() -> None:
    from opentweet.config import _url_for_details

    assert "<redacted>" in _url_for_details("https://user:secret@host/path")
    assert "secret" not in _url_for_details("https://user:secret@host/path")
    assert _url_for_details("not-a-url") == "<unparsed>"


def test_shipped_query_timeout_clears_graph_deadline() -> None:
    # 790 = api.graph_timeout_sec (780) + 10s: the channel client must lose the
    # race so the server's diagnosable 504 GRAPH_TIMEOUT arrives instead of a
    # client abort (same pattern as static/terminal.js's queryDeadlineMs).
    shipped_path = Path(__file__).resolve().parent.parent / "config.yaml"
    shipped = yaml.safe_load(shipped_path.read_text(encoding="utf-8"))
    assert shipped["opentweet"]["query"]["timeout_sec"] == 790
    assert shipped["opentweet"]["query"]["timeout_sec"] > shipped["api"]["graph_timeout_sec"]

def test_query_url_invalid_port_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": {"base_url": "http://127.0.0.1:notaport"}})
    with pytest.raises(OpenTweetConfigError, match="not a valid URL"):
        load_opentweet_config(path)


def test_api_base_type_shell_port_and_fragment() -> None:
    cases = [
        (12, "must be an https URL"),
        ("https://opentweet.io;x", "disallowed characters"),
        ("https://opentweet.io:bad", "not a valid URL"),
        ("http://opentweet.io", "must be an https URL"),
        ("https://opentweet.io/#frag", "query or fragment"),
    ]
    for value, match in cases:
        with pytest.raises(OpenTweetConfigError, match=match):
            OpenTweetConfig(api_base=value, enabled=False)  # type: ignore[arg-type]


def test_topic_file_type_and_query_type_and_resolvers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(OpenTweetConfigError, match="topic_file must be a string"):
        OpenTweetConfig(topic_file=9)  # type: ignore[arg-type]
    with pytest.raises(OpenTweetConfigError, match="query must be a mapping"):
        OpenTweetConfig(query="nope")  # type: ignore[arg-type]
    cfg = load_opentweet_config(_write_config(tmp_path, {"enabled": False}))
    monkeypatch.delenv(cfg.api_key_env, raising=False)
    with pytest.raises(OpenTweetConfigError, match="unset or empty"):
        cfg.resolve_api_key()
    monkeypatch.setenv(cfg.api_key_env, "ot-key")
    assert cfg.resolve_api_key() == "ot-key"
    monkeypatch.delenv(cfg.query.api_key_env, raising=False)
    assert cfg.resolve_query_api_key() is None
    monkeypatch.setenv(cfg.query.api_key_env, "q")
    assert cfg.resolve_query_api_key() == "q"
    public = cfg.to_public_dict()
    assert public["api_key_set"] is True
    assert public["query_api_key_set"] is True
    assert "_config_path" not in public


def test_load_opentweet_config_error_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from opentweet import config as otc

    monkeypatch.setattr(otc, "_get_config", lambda _p: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OpenTweetConfigError, match="Unable to load"):
        load_opentweet_config(str(tmp_path / "missing.yaml"))
    monkeypatch.setattr(otc, "_get_config", lambda _p: ["not", "a", "map"])
    with pytest.raises(OpenTweetConfigError, match="config root must be a mapping"):
        load_opentweet_config(str(tmp_path / "x.yaml"))
    monkeypatch.setattr(otc, "_get_config", lambda _p: {"opentweet": ["nope"]})
    with pytest.raises(OpenTweetConfigError, match="block must be a mapping"):
        load_opentweet_config(str(tmp_path / "x.yaml"))

def test_opentweet_query_mapping_and_unknown_keys(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": ["not", "a", "map"]})
    with pytest.raises(OpenTweetConfigError, match="query must be a mapping"):
        load_opentweet_config(path)
    reset_config_cache()
    path = _write_config(tmp_path, {"query": {"base_url": "http://127.0.0.1:8787", "extra": 1}})
    with pytest.raises(OpenTweetConfigError, match="query unknown"):
        load_opentweet_config(path)
