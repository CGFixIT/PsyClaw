"""Unit tests for T3's persistent, one-shot Telegram confirmation state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from telegram.state import claim_hybrid_confirm, grant_hybrid_confirm


def test_hybrid_confirmation_is_persistent_then_single_use(tmp_path: Path) -> None:
    path = tmp_path / "session_42.json"
    expiry = grant_hybrid_confirm(42, provider="claude", ttl_sec=120, now=1000, path=path)

    assert expiry == 1120
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "confirm_until": 1120,
        "provider": "claude",
    }
    assert claim_hybrid_confirm(42, now=1001, path=path) == "claude"
    assert claim_hybrid_confirm(42, now=1002, path=path) is None
    assert not path.exists()


def test_expired_hybrid_confirmation_fails_closed_and_is_removed(tmp_path: Path) -> None:
    path = tmp_path / "session_42.json"
    grant_hybrid_confirm(42, provider="grok", ttl_sec=10, now=1000, path=path)

    assert claim_hybrid_confirm(42, now=1010, path=path) is None
    assert not path.exists()


def test_malformed_hybrid_confirmation_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "session_42.json"
    path.write_text('{"confirm_until":"forever","provider":"grok"}', encoding="utf-8")

    assert claim_hybrid_confirm(42, now=1000, path=path) is None


@pytest.mark.parametrize("chat_id", ["../42", "42/other", True, 2**63])
def test_session_path_rejects_path_shaped_or_invalid_chat_ids(
    tmp_path: Path, chat_id: object
) -> None:
    with pytest.raises(ValueError):
        grant_hybrid_confirm(chat_id, provider="grok", ttl_sec=120, path=tmp_path / "state.json")


@pytest.mark.parametrize("provider", ["", "GROK", "other"])
def test_session_rejects_unknown_provider(tmp_path: Path, provider: str) -> None:
    with pytest.raises(ValueError):
        grant_hybrid_confirm(42, provider=provider, ttl_sec=120, path=tmp_path / "state.json")
