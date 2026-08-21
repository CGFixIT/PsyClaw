"""One-shot: topic → loopback /query → validate → OpenTweet draft or schedule."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from opentweet import client
from opentweet.config import OpenTweetConfig, parse_schedule_slot
from utils.errors import OpenTweetRefused
from utils.logger import audit_log, hash_query

PROMPT_TEMPLATE = """Write exactly one X status of at most 260 characters that answers the topic
using only the retrieved corpus.

Output only the post text. No preamble, no wrapping quotes, no hashtags unless
they appear in a source. If the corpus does not support a specific claim, write
one short sentence saying so rather than inventing.

Topic: {topic}
"""

_ONLINE_MODELS = frozenset({"grok", "claude"})


def next_schedule_datetime(
    weekday: int,
    slot: str,
    now: datetime | None = None,
) -> datetime:
    """Next local occurrence of ``weekday`` + ``HH:MM`` strictly in the future."""
    hour, minute = parse_schedule_slot(slot)
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    python_weekday = 6 if weekday in (0, 7) else weekday - 1
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (python_weekday - current.weekday()) % 7
    target = target + timedelta(days=days_ahead)
    if target <= current:
        target += timedelta(days=7)
    return target


def read_topic(cfg: OpenTweetConfig, *, topic: str | None, topic_file: str | None) -> str:
    if topic is not None and topic.strip():
        text = topic.strip()
    else:
        path_s = (topic_file or cfg.topic_file or "").strip()
        if not path_s:
            raise OpenTweetRefused(
                "topic is empty and opentweet.topic_file is unset",
                details={"gate": "topic_missing"},
            )
        path = Path(path_s).expanduser()
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OpenTweetRefused(
                "topic file could not be read",
                details={"gate": "topic_file", "error_type": type(exc).__name__},
            ) from None
        text = raw.strip()
        if not text:
            raise OpenTweetRefused("topic file is empty", details={"gate": "topic_empty"})
    if len(text) > cfg.max_topic_chars:
        raise OpenTweetRefused(
            f"topic exceeds opentweet.max_topic_chars ({cfg.max_topic_chars})",
            details={"gate": "max_topic_chars", "len": len(text)},
        )
    return text


def _validate_answer(cfg: OpenTweetConfig, data: dict[str, Any]) -> str:
    err = data.get("error")
    if err:
        raise OpenTweetRefused(
            "CyClaw /query returned an error",
            details={"gate": "query_error"},
        )
    if data.get("needs_confirm") is True:
        raise OpenTweetRefused(
            "retrieval needs confirmation; refusing to post",
            details={"gate": "needs_confirm"},
        )
    hit_count = data.get("hit_count")
    if not isinstance(hit_count, int) or hit_count <= 0:
        raise OpenTweetRefused(
            "retrieval returned no hits",
            details={"gate": "hit_count"},
        )
    model_used = str(data.get("model_used") or "")
    if model_used in _ONLINE_MODELS:
        raise OpenTweetRefused(
            "online model answered; refusing to post",
            details={"gate": "online_model"},
        )
    answer = data.get("answer")
    if not isinstance(answer, str):
        raise OpenTweetRefused("CyClaw answer is missing", details={"gate": "empty_answer"})
    text = answer.strip()
    if not text:
        raise OpenTweetRefused("CyClaw answer is empty", details={"gate": "empty_answer"})
    if text.startswith("["):
        raise OpenTweetRefused(
            "answer looks like a rail/system stanza",
            details={"gate": "rail_prefix"},
        )
    if len(text) > cfg.max_post_chars:
        raise OpenTweetRefused(
            f"answer exceeds opentweet.max_post_chars ({cfg.max_post_chars})",
            details={"gate": "max_post_chars", "len": len(text)},
        )
    return text


def _check_me(me: dict[str, Any], *, schedule: bool) -> None:
    if me.get("authenticated") is not True:
        raise OpenTweetRefused(
            "OpenTweet /me is not authenticated",
            details={"gate": "me_auth"},
        )
    if not schedule:
        return
    sub = me.get("subscription")
    limits = me.get("limits")
    has_access = isinstance(sub, dict) and sub.get("has_access") is True
    can_post = isinstance(limits, dict) and limits.get("can_post") is True
    if not has_access or not can_post:
        raise OpenTweetRefused(
            "OpenTweet subscription cannot schedule/publish",
            details={"gate": "subscription"},
        )


def post_once(
    cfg: OpenTweetConfig,
    *,
    topic: str | None = None,
    topic_file: str | None = None,
    schedule: bool = False,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Generate one post body and optionally write it to OpenTweet.

    Returns a public dict (hash, length, mode, optional OpenTweet id). Never
    includes the post text or API key.
    """
    topic_text = read_topic(cfg, topic=topic, topic_file=topic_file)
    query = PROMPT_TEMPLATE.format(topic=topic_text)
    data = client.post_query(cfg, query)
    answer = _validate_answer(cfg, data)

    scheduled_date: str | None = None
    if schedule:
        when = next_schedule_datetime(cfg.weekday, cfg.schedule_slot, now=now)
        if when.tzinfo is None:
            when = when.astimezone()
        scheduled_date = when.isoformat(timespec="seconds")
        if when <= (now or datetime.now().astimezone()):
            raise OpenTweetRefused(
                "scheduled_date is not in the future",
                details={"gate": "schedule_past"},
            )

    mode = "scheduled" if scheduled_date else "draft"
    public: dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "text_hash": hash_query(answer),
        "text_len": len(answer),
        "dry_run": dry_run,
        "opentweet_id": None,
    }
    if dry_run:
        audit_log(
            {
                "event": "opentweet_dry_run",
                "channel": "opentweet",
                "ok": True,
                "mode": mode,
                "query_hash": public["text_hash"],
                "query_len": public["text_len"],
            },
            config_path=cfg._config_path,
        )
        return public

    me = client.get_me(cfg)
    _check_me(me, schedule=schedule)
    result = client.create_post(cfg, answer, scheduled_date=scheduled_date)
    posts = result.get("posts")
    post_id = None
    if isinstance(posts, list) and posts and isinstance(posts[0], dict):
        post_id = posts[0].get("id")
    public["opentweet_id"] = post_id
    audit_log(
        {
            "event": "opentweet_scheduled" if scheduled_date else "opentweet_draft",
            "channel": "opentweet",
            "ok": True,
            "mode": mode,
            "query_hash": public["text_hash"],
            "query_len": public["text_len"],
            "opentweet_id": post_id,
        },
        config_path=cfg._config_path,
    )
    return public
