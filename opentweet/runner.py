"""One-shot: topic → loopback /query → validate → OpenTweet draft or schedule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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

Topic:
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
    # Normalize the *final* local instant through UTC so a wall-clock slot that
    # falls in a DST gap or is ambiguous snaps to a real instant. Doing this
    # before adding days_ahead leaves a target reached from an earlier day
    # unnormalized (Codex P2 on #1092).
    target = target.astimezone(UTC).astimezone(current.tzinfo)
    if target <= current:
        target = (target + timedelta(days=7)).astimezone(UTC).astimezone(current.tzinfo)
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
        except (OSError, UnicodeError) as exc:
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
