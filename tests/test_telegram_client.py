"""Unit tests for telegram.client (httpx mocked; no network)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from telegram.client import get_updates, post_query, send_message
from telegram.config import load_telegram_config
from utils.errors import TelegramRefused, TelegramRuntimeError
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _cfg(tmp_path: Path, **overrides: object):
    block = {
        "enabled": True,
        "mode": "chat",
        "allowed_chat_ids": ["42"],
        "query": {"base_url": "http://127.0.0.1:8787"},
    }
    block.update(overrides)
    raw = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}, "telegram": block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_telegram_config(str(path))


def test_send_message_allowlist_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    with pytest.raises(TelegramRefused) as exc:
        send_message(cfg, chat_id=999, text="hi")
    assert exc.value.details and exc.value.details.get("gate") == "allowlist"


def test_send_message_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 7}}

    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = mock_resp
        data = send_message(cfg, chat_id=42, text="hello")
    assert data["ok"] is True
    assert data["result"]["message_id"] == 7


def test_send_message_api_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {"ok": False, "description": "bad request"}

    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = mock_resp
        with pytest.raises(TelegramRuntimeError):
            send_message(cfg, chat_id=42, text="hello")


def test_post_query_empty_refuses(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with pytest.raises(TelegramRefused):
        post_query(cfg, query="   ")


def test_post_query_success(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"answer": "from local", "model_used": "qwen"}

    with patch("telegram.client.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = mock_resp
        data = post_query(cfg, query="what is CyClaw?")
        # Must never auto-confirm hybrid.
        kwargs = client.post.call_args
        payload = kwargs.kwargs.get("json") or kwargs[1].get("json")
        assert payload["user_confirmed_online"] is False
    assert data["answer"] == "from local"


def test_get_updates_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "result": [{"update_id": 1, "message": {"chat": {"id": 42}, "text": "hi"}}],
    }
    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
        updates = get_updates(cfg, offset=None)
    assert len(updates) == 1
    assert updates[0]["update_id"] == 1
