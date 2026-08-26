"""Runner fail-closed cases and dry-run (no OpenTweet write)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
import yaml

from opentweet.config import load_opentweet_config
from opentweet.runner import next_schedule_datetime, post_once
from utils.errors import OpenTweetRefused
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_config_cache()
    yield
    reset_config_cache()


def _cfg(tmp_path: Path, **extra: object) -> str:
    block: dict = {
        "enabled": True,
        "topic_file": str(tmp_path / "topic.txt"),
        "query": {"base_url": "http://127.0.0.1:8787"},
    }
    block.update(extra)
    raw = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}, "opentweet": block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    (tmp_path / "topic.txt").write_text("soul governance", encoding="utf-8")
    return str(path)


def _answer(**overrides: object) -> dict:
    data = {
        "answer": "Ship the invariants as topology, not prompts.",
        "hit_count": 3,
        "model_used": "local",
        "needs_confirm": False,
        "error": None,
    }
    data.update(overrides)
    return data


def test_topic_with_braces_does_not_crash(tmp_path: Path) -> None:
    cfg = load_opentweet_config(_cfg(tmp_path))
    with (
        patch("opentweet.client.post_query", return_value=_answer()) as post,
        patch("opentweet.client.get_me"),
        patch("opentweet.client.create_post"),
    ):
        post_once(cfg, topic="what does {graph} topology mean?", dry_run=True)
    sent = post.call_args.args[1]
    assert "{graph}" in sent
    assert "Write exactly one X status" in sent


def test_non_utf8_topic_file_refuses(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    (tmp_path / "topic.txt").write_bytes(b"\xff\xfeS\x00")
    cfg = load_opentweet_config(cfg_path)
    with pytest.raises(OpenTweetRefused) as exc:
        post_once(cfg, dry_run=True)
    assert exc.value.details["gate"] == "topic_file"


def test_empty_topic_file_refuses(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    (tmp_path / "topic.txt").write_text("  \n", encoding="utf-8")
    cfg = load_opentweet_config(cfg_path)
    with pytest.raises(OpenTweetRefused) as exc:
        post_once(cfg, dry_run=True)
    assert exc.value.details["gate"] == "topic_empty"


def test_needs_confirm_refuses(tmp_path: Path) -> None:
    cfg = load_opentweet_config(_cfg(tmp_path))
    with patch("opentweet.client.post_query", return_value=_answer(needs_confirm=True)):
        with pytest.raises(OpenTweetRefused) as exc:
            post_once(cfg, dry_run=True)
    assert exc.value.details["gate"] == "needs_confirm"


def test_zero_hits_refuses(tmp_path: Path) -> None:
    cfg = load_opentweet_config(_cfg(tmp_path))
    with patch("opentweet.client.post_query", return_value=_answer(hit_count=0)):
        with pytest.raises(OpenTweetRefused) as exc:
            post_once(cfg, dry_run=True)
    assert exc.value.details["gate"] == "hit_count"


def test_online_model_refuses(tmp_path: Path) -> None:
    cfg = load_opentweet_config(_cfg(tmp_path))
    with patch("opentweet.client.post_query", return_value=_answer(model_used="grok")):
        with pytest.raises(OpenTweetRefused) as exc:
            post_once(cfg, dry_run=True)
    assert exc.value.details["gate"] == "online_model"


def test_oversize_refuses_no_truncate(tmp_path: Path) -> None:
    cfg = load_opentweet_config(_cfg(tmp_path))
    with patch("opentweet.client.post_query", return_value=_answer(answer="x" * 281)):
        with pytest.raises(OpenTweetRefused) as exc:
            post_once(cfg, dry_run=True)
    assert exc.value.details["gate"] == "max_post_chars"


def test_rail_prefix_refuses(tmp_path: Path) -> None:
    cfg = load_opentweet_config(_cfg(tmp_path))
    with patch("opentweet.client.post_query", return_value=_answer(answer="[blocked]")):
        with pytest.raises(OpenTweetRefused) as exc:
            post_once(cfg, dry_run=True)
    assert exc.value.details["gate"] == "rail_prefix"


def test_dry_run_does_not_call_opentweet(tmp_path: Path) -> None:
    cfg = load_opentweet_config(_cfg(tmp_path))
    with (
        patch("opentweet.client.post_query", return_value=_answer()),
        patch("opentweet.client.get_me") as me,
        patch("opentweet.client.create_post") as create,
    ):
        result = post_once(cfg, dry_run=True)
    me.assert_not_called()
    create.assert_not_called()
    assert result["dry_run"] is True
    assert result["mode"] == "draft"
    assert "text" not in result
    assert len(result["text_hash"]) == 64


def test_schedule_sends_future_iso_not_publish_now(tmp_path: Path) -> None:
    cfg = load_opentweet_config(_cfg(tmp_path, schedule_enabled=True, weekday=1, schedule_slot="09:00"))
    now = datetime(2026, 8, 24, 6, 0, tzinfo=UTC)
    with (
        patch("opentweet.client.post_query", return_value=_answer()),
        patch(
            "opentweet.client.get_me",
            return_value={
                "authenticated": True,
                "subscription": {"has_access": True},
                "limits": {"can_post": True},
            },
        ),
        patch("opentweet.client.create_post", return_value={"success": True, "posts": [{"id": "p2"}]}) as create,
    ):
        result = post_once(cfg, schedule=True, now=now)
    assert result["mode"] == "scheduled"
    kwargs = create.call_args.kwargs
    assert kwargs.get("scheduled_date")
    assert "publish_now" not in kwargs
    assert kwargs["scheduled_date"].startswith("2026-08-24T09:00:00")


def test_limits_null_refuses_schedule(tmp_path: Path) -> None:
    cfg = load_opentweet_config(_cfg(tmp_path, schedule_enabled=True))
    with (
        patch("opentweet.client.post_query", return_value=_answer()),
        patch(
            "opentweet.client.get_me",
            return_value={"authenticated": True, "subscription": {"has_access": True}, "limits": None},
        ),
        patch("opentweet.client.create_post") as create,
    ):
        with pytest.raises(OpenTweetRefused) as exc:
            post_once(cfg, schedule=True)
    assert exc.value.details["gate"] == "subscription"
    create.assert_not_called()


def test_success_does_not_include_raw_text(tmp_path: Path) -> None:
    cfg = load_opentweet_config(_cfg(tmp_path))
    with (
        patch("opentweet.client.post_query", return_value=_answer()),
        patch(
            "opentweet.client.get_me",
            return_value={"authenticated": True, "subscription": {"has_access": True}, "limits": {"can_post": True}},
        ),
        patch("opentweet.client.create_post", return_value={"success": True, "posts": [{"id": "p1"}]}) as create,
    ):
        result = post_once(cfg, dry_run=False)
    create.assert_called_once()
    assert create.call_args.kwargs.get("scheduled_date") is None
    assert result["opentweet_id"] == "p1"
    assert "Ship the invariants" not in str(result)


def test_next_schedule_is_future() -> None:
    now = datetime(2026, 8, 24, 6, 0, tzinfo=UTC)  # Monday
    when = next_schedule_datetime(1, "09:00", now=now)
    assert when > now
    assert when.hour == 9
    assert when.minute == 0
    # Same Monday 09:00 is still in the future at 06:00.
    assert when.date() == now.date()
    later = next_schedule_datetime(1, "09:00", now=now + timedelta(hours=4))
    assert later.date() > now.date()


def test_next_schedule_handles_dst_gap() -> None:
    """A slot that falls in the spring-forward gap must snap to a real instant."""
    tz = ZoneInfo("America/New_York")
    # 2026-03-08 01:30 EST; clocks spring forward at 02:00, so 02:30 does not exist.
    now = datetime(2026, 3, 8, 1, 30, tzinfo=tz)
    when = next_schedule_datetime(7, "02:30", now=now)  # same Sunday
    assert when > now
    # The nonexistent 02:30 EST must normalize to 03:30 EDT (or later).
    assert when.minute == 30
    assert when.utcoffset() == timedelta(hours=-4)


def test_next_schedule_handles_dst_gap_from_prior_day() -> None:
    """A slot chosen from the Saturday before spring-forward must still snap."""
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 3, 7, 12, 0, tzinfo=tz)  # Saturday before the gap Sunday
    when = next_schedule_datetime(7, "02:30", now=now)
    assert when > now
    assert when.date() == datetime(2026, 3, 8).date()
    assert when.minute == 30
    assert when.utcoffset() == timedelta(hours=-4)


def test_schedule_with_naive_now_normalizes(tmp_path: Path) -> None:
    """A library caller passing a naive ``now`` must not get a TypeError."""
    cfg = load_opentweet_config(_cfg(tmp_path, schedule_enabled=True, weekday=1, schedule_slot="09:00"))
    now = datetime(2026, 8, 24, 6, 0)  # naive
    with (
        patch("opentweet.client.post_query", return_value=_answer()),
        patch(
            "opentweet.client.get_me",
            return_value={
                "authenticated": True,
                "subscription": {"has_access": True},
                "limits": {"can_post": True},
            },
        ),
        patch(
            "opentweet.client.create_post",
            return_value={"success": True, "posts": [{"id": "p3"}]},
        ) as create,
    ):
        result = post_once(cfg, schedule=True, now=now)
    assert result["mode"] == "scheduled"
    kwargs = create.call_args.kwargs
    assert kwargs.get("scheduled_date")
    assert kwargs["scheduled_date"].startswith("2026-08-24T09:00:00")
