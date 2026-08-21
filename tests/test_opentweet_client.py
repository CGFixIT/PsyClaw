"""OpenTweet HTTP client: never confirms online; draft omits publish_now."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from opentweet import client
from opentweet.config import load_opentweet_config
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_config_cache()
    client.reset_http_client_for_tests()
    yield
    client.reset_http_client_for_tests()
    reset_config_cache()


def _cfg(tmp_path: Path) -> str:
    raw = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl")},
        "opentweet": {
            "enabled": True,
            "topic_file": str(tmp_path / "topic.txt"),
            "query": {"base_url": "http://127.0.0.1:8787"},
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return str(path)


def test_post_query_never_confirms_online(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENTWEET_API_KEY", "ot_test")
    cfg = load_opentweet_config(_cfg(tmp_path))
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
        captured["url"] = url
        captured["json"] = json
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"answer": "ok", "hit_count": 1, "model_used": "local"}
        return resp

    mock_http = MagicMock()
    mock_http.post.side_effect = fake_post
    with patch("opentweet.client._get_loopback_http_client", return_value=mock_http):
        client.post_query(cfg, "hello topic")
    assert captured["json"]["user_confirmed_online"] is False
    assert "online_provider" not in captured["json"]
    assert captured["url"].endswith("/query")


def test_create_post_draft_omits_publish_and_schedule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENTWEET_API_KEY", "ot_test")
    cfg = load_opentweet_config(_cfg(tmp_path))
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
        captured["json"] = json
        captured["headers"] = headers
        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = {"success": True, "posts": [{"id": "abc"}]}
        return resp

    mock_http = MagicMock()
    mock_http.post.side_effect = fake_post
    with patch("opentweet.client._get_opentweet_http_client", return_value=mock_http):
        client.create_post(cfg, "body")
    assert captured["json"] == {"text": "body"}
    assert "publish_now" not in captured["json"]
    assert "scheduled_date" not in captured["json"]
    assert str(captured["headers"]["Authorization"]).startswith("Bearer ")


def test_create_post_schedule_sends_future_iso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENTWEET_API_KEY", "ot_test")
    cfg = load_opentweet_config(_cfg(tmp_path))
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
        captured["json"] = json
        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = {"success": True, "posts": [{"id": "abc"}]}
        return resp

    mock_http = MagicMock()
    mock_http.post.side_effect = fake_post
    iso = "2026-08-24T13:00:00-04:00"
    with patch("opentweet.client._get_opentweet_http_client", return_value=mock_http):
        client.create_post(cfg, "body", scheduled_date=iso)
    assert captured["json"]["scheduled_date"] == iso
    assert "publish_now" not in captured["json"]
