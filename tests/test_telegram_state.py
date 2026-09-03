"""Unit tests for T3's persistent, one-shot Telegram confirmation state."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from telegram.state import claim_hybrid_confirm, grant_hybrid_confirm
from utils.errors import TelegramRuntimeError


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


def test_hybrid_confirmation_unlink_failure_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session_42.json"
    grant_hybrid_confirm(42, provider="grok", ttl_sec=120, now=1000, path=path)

    with (
        patch.object(Path, "unlink", side_effect=OSError),
        pytest.raises(TelegramRuntimeError) as exc,
    ):
        claim_hybrid_confirm(42, now=1001, path=path)

    assert exc.value.details == {"gate": "hybrid_confirm_state", "retryable": True}
    assert path.exists()
    assert claim_hybrid_confirm(42, now=1001, path=path) == "grok"
    assert not path.exists()


def test_malformed_hybrid_confirmation_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "session_42.json"
    path.write_text('{"confirm_until":"forever","provider":"grok"}', encoding="utf-8")

    assert claim_hybrid_confirm(42, now=1000, path=path) is None


@pytest.mark.parametrize("chat_id", ["../42", "42/other", True, 2**63])
def test_session_path_rejects_path_shaped_or_invalid_chat_ids(
    tmp_path: Path, chat_id: object
) -> None:
    # Params span both validators: non-integer-shaped ids raise "chat_id must
    # be a signed integer", 2**63 raises "chat_id is outside signed 64-bit
    # range" -- both carry the chat_id prefix.
    with pytest.raises(ValueError, match="chat_id"):
        grant_hybrid_confirm(chat_id, provider="grok", ttl_sec=120, path=tmp_path / "state.json")


@pytest.mark.parametrize("provider", ["", "GROK", "other"])
def test_session_rejects_unknown_provider(tmp_path: Path, provider: str) -> None:
    with pytest.raises(ValueError, match="provider must be grok or claude"):
        grant_hybrid_confirm(42, provider=provider, ttl_sec=120, path=tmp_path / "state.json")

def test_default_paths_and_canonical_chat_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from telegram import state as st

    monkeypatch.setattr(st, "DEFAULT_STATE_DIR", tmp_path)
    monkeypatch.setattr(st, "DEFAULT_OFFSET_PATH", tmp_path / "offset.json")
    assert st.default_offset_path() == tmp_path / "offset.json"
    assert st.default_hybrid_session_path(42) == tmp_path / "session_42.json"
    with pytest.raises(ValueError, match="outside signed 64-bit"):
        st._canonical_chat_id(-(2**63) - 1)


def test_safe_now_rejects_non_finite() -> None:
    from telegram.state import _safe_now

    with pytest.raises(ValueError, match="finite"):
        _safe_now(True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite"):
        _safe_now(float("nan"))


def test_session_lock_posix_fcntl_path(monkeypatch, tmp_path: Path) -> None:
    from telegram.state import _session_lock

    flock_calls: list[int] = []
    fake_fcntl = type("F", (), {})()
    fake_fcntl.LOCK_EX = 2
    fake_fcntl.LOCK_UN = 8

    def _flock(_fd: int, flags: int) -> None:
        flock_calls.append(flags)

    fake_fcntl.flock = _flock
    monkeypatch.setattr(os, "name", "posix")
    import sys
    monkeypatch.setitem(sys.modules, "fcntl", fake_fcntl)
    path = tmp_path / "session.json"
    with _session_lock(path):
        pass
    assert flock_calls == [2, 8]


def test_save_json_closes_fd_on_write_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from telegram.state import _save_json

    path = tmp_path / "x.json"
    monkeypatch.setattr("telegram.state.os.fdopen", lambda *_a, **_k: (_ for _ in ()).throw(OSError("fail")))
    with pytest.raises(OSError, match="fail"):
        _save_json({"a": 1}, path)


def test_load_hybrid_session_rejects_non_dict(tmp_path: Path) -> None:
    from telegram.state import _load_hybrid_session

    path = tmp_path / "s.json"
    path.write_text("[1,2]", encoding="utf-8")
    assert _load_hybrid_session(path) is None


def test_grant_hybrid_rejects_bad_ttl_and_oserror(tmp_path: Path) -> None:
    from telegram.state import grant_hybrid_confirm

    with pytest.raises(ValueError, match="ttl_sec"):
        grant_hybrid_confirm(1, provider="grok", ttl_sec=0, path=tmp_path / "s.json")
    with (
        patch("telegram.state._session_lock", side_effect=OSError("disk")),
        pytest.raises(TelegramRuntimeError) as exc,
    ):
        grant_hybrid_confirm(1, provider="grok", ttl_sec=10, path=tmp_path / "s.json")
    assert exc.value.details["gate"] == "hybrid_confirm_state"


def test_offset_load_save_defaults_and_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from telegram import state as st

    monkeypatch.setattr(st, "DEFAULT_OFFSET_PATH", tmp_path / "offset.json")
    assert st.load_offset() is None
    (tmp_path / "offset.json").write_text('{"offset": -1}', encoding="utf-8")
    assert st.load_offset() is None
    (tmp_path / "offset.json").write_text("[1]", encoding="utf-8")
    assert st.load_offset() is None
    with pytest.raises(ValueError, match="non-negative"):
        st.save_offset(-1, path=tmp_path / "o.json")
    st.save_offset(7)
    assert st.load_offset() == 7
