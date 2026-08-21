"""OpenTweetConfig dataclass and validating loader for the ``opentweet:`` block.

Absence of the block disables the channel without touching gate/graph/MCP.
This package is NEVER imported by those modules (I6).
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

import yaml  # type: ignore[import-untyped]

from utils.errors import OpenTweetConfigError
from utils.logger import _get_config

DEFAULT_API_KEY_ENV = "OPENTWEET_API_KEY"
DEFAULT_API_BASE = "https://opentweet.io"
DEFAULT_QUERY_BASE_URL = "http://127.0.0.1:8787"  # DevSkim: ignore DS162092 - loopback by design
DEFAULT_QUERY_API_KEY_ENV = "CYCLAW_API_KEY"
DEFAULT_QUERY_TIMEOUT_SEC = 780
DEFAULT_MAX_TOPIC_CHARS = 500
DEFAULT_MAX_POST_CHARS = 280
MAX_POST_CHARS = 280
DEFAULT_SCHEDULE_SLOT = "09:00"
DEFAULT_WEEKDAY = 1  # Monday; 0/7 = Sunday (match win_schtasks)
DEFAULT_FIRE_HOUR = 6
DEFAULT_FIRE_MINUTE = 0

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SLOT_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_SHELL_METACHARS = set(";|&$`<>(){}!*?\"'\\ \n\r\t")
# Paths may contain \ / : ~ space; they become argv elements, not a shell string.
_PATH_UNSAFE = set(";|&$`<>(){}!*?\"'\n\r\t")


def _validate_bool(value: object, field_name: str) -> None:
    if not isinstance(value, bool):
        raise OpenTweetConfigError(
            f"{field_name} must be a YAML boolean, got {type(value).__name__}",
            details={"received": repr(value)},
        )


def _validate_int(value: object, field_name: str, *, min_v: int, max_v: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OpenTweetConfigError(
            f"{field_name} must be an integer, got {type(value).__name__}",
            details={"received": repr(value)},
        )
    if not min_v <= value <= max_v:
        raise OpenTweetConfigError(
            f"{field_name} must be in [{min_v}, {max_v}], got: {value}",
            details={"received": value},
        )
    return value


def _validate_positive_int(value: object, field_name: str) -> int:
    return _validate_int(value, field_name, min_v=1, max_v=10_000_000)


def _validate_env_name(name: str, field_name: str) -> str:
    if not isinstance(name, str) or not _ENV_NAME_RE.match(name):
        raise OpenTweetConfigError(
            f"{field_name} must be an UPPER_SNAKE env var name",
            details={"received_type": type(name).__name__},
        )
    return name


def _validate_loopback_url(url: str, field_name: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise OpenTweetConfigError(
            f"{field_name} is required",
            details={"hint": "Use a loopback URL such as http://127.0.0.1:8787"},
        )
    if any(c in url for c in _SHELL_METACHARS):
        raise OpenTweetConfigError(
            f"{field_name} contains disallowed characters",
            details={"received": url},
        )
    try:
        parsed = urlparse(url)
        _ = parsed.port
    except ValueError as exc:
        raise OpenTweetConfigError(f"{field_name} is not a valid URL") from exc
    if parsed.scheme not in ("http", "https"):
        raise OpenTweetConfigError(
            f"{field_name} must be http or https, got scheme={parsed.scheme!r}",
            details={"received": url},
        )
    if parsed.username is not None or parsed.password is not None:
        raise OpenTweetConfigError(f"{field_name} must not contain URL credentials")
    if parsed.query or parsed.fragment:
        raise OpenTweetConfigError(f"{field_name} must not contain a query or fragment")
    host = (parsed.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise OpenTweetConfigError(
            f"{field_name} must target loopback (127.0.0.1/localhost), got host={host!r}",
            details={
                "received": url,
                "hint": "OpenTweet talks to CyClaw only over loopback.",
            },
        )
    return url.rstrip("/")


def _validate_https_base(url: str, field_name: str) -> str:
    if not isinstance(url, str):
        raise OpenTweetConfigError(
            f"{field_name} must be an https URL",
            details={"received_type": type(url).__name__},
        )
    if any(c in url for c in _SHELL_METACHARS):
        raise OpenTweetConfigError(
            f"{field_name} contains disallowed characters",
            details={"received": url},
        )
    try:
        parsed = urlparse(url)
        _ = parsed.port
    except ValueError as exc:
        raise OpenTweetConfigError(f"{field_name} is not a valid URL") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise OpenTweetConfigError(f"{field_name} must be an https URL with a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise OpenTweetConfigError(f"{field_name} must not contain URL credentials")
    if parsed.query or parsed.fragment:
        raise OpenTweetConfigError(f"{field_name} must not contain a query or fragment")
    return url.rstrip("/")


def parse_schedule_slot(slot: str, field_name: str = "opentweet.schedule_slot") -> tuple[int, int]:
    if not isinstance(slot, str) or not _SLOT_RE.match(slot.strip()):
        raise OpenTweetConfigError(
            f"{field_name} must be HH:MM (24h)",
            details={"received_type": type(slot).__name__},
        )
    hour_s, minute_s = slot.strip().split(":", 1)
    return int(hour_s), int(minute_s)


@dataclass
class OpenTweetQueryConfig:
    """How this channel reaches existing CyClaw ``POST /query``."""

    base_url: str = DEFAULT_QUERY_BASE_URL
    api_key_env: str = DEFAULT_QUERY_API_KEY_ENV
    timeout_sec: int = DEFAULT_QUERY_TIMEOUT_SEC

    def __post_init__(self) -> None:
        self.base_url = _validate_loopback_url(self.base_url, "opentweet.query.base_url")
        self.api_key_env = _validate_env_name(self.api_key_env, "opentweet.query.api_key_env")
        self.timeout_sec = _validate_positive_int(self.timeout_sec, "opentweet.query.timeout_sec")


@dataclass
class OpenTweetConfig:
    """Parsed and validated ``opentweet:`` block from config.yaml."""

    enabled: bool = False
    api_base: str = DEFAULT_API_BASE
    api_key_env: str = DEFAULT_API_KEY_ENV
    topic_file: str = ""
    max_topic_chars: int = DEFAULT_MAX_TOPIC_CHARS
    max_post_chars: int = DEFAULT_MAX_POST_CHARS
    schedule_enabled: bool = False
    schedule_slot: str = DEFAULT_SCHEDULE_SLOT
    weekday: int = DEFAULT_WEEKDAY
    fire_hour: int = DEFAULT_FIRE_HOUR
    fire_minute: int = DEFAULT_FIRE_MINUTE
    query: OpenTweetQueryConfig = field(default_factory=OpenTweetQueryConfig)
    _config_path: str = "config.yaml"

    def __post_init__(self) -> None:
        _validate_bool(self.enabled, "opentweet.enabled")
        _validate_bool(self.schedule_enabled, "opentweet.schedule_enabled")
        self.api_base = _validate_https_base(self.api_base, "opentweet.api_base")
        self.api_key_env = _validate_env_name(self.api_key_env, "opentweet.api_key_env")
        self.max_topic_chars = _validate_positive_int(self.max_topic_chars, "opentweet.max_topic_chars")
        self.max_post_chars = _validate_int(
            self.max_post_chars, "opentweet.max_post_chars", min_v=1, max_v=MAX_POST_CHARS
        )
        self.weekday = _validate_int(self.weekday, "opentweet.weekday", min_v=0, max_v=7)
        self.fire_hour = _validate_int(self.fire_hour, "opentweet.fire_hour", min_v=0, max_v=23)
        self.fire_minute = _validate_int(self.fire_minute, "opentweet.fire_minute", min_v=0, max_v=59)
        parse_schedule_slot(self.schedule_slot)
        self.schedule_slot = self.schedule_slot.strip()

        if not isinstance(self.topic_file, str):
            raise OpenTweetConfigError(
                "opentweet.topic_file must be a string",
                details={"received_type": type(self.topic_file).__name__},
            )
        if "\x00" in self.topic_file or any(c in self.topic_file for c in _PATH_UNSAFE):
            raise OpenTweetConfigError(
                "opentweet.topic_file contains disallowed characters",
            )
        self.topic_file = self.topic_file.strip()
        if self.enabled and not self.topic_file:
            raise OpenTweetConfigError(
                "opentweet.topic_file is required when opentweet.enabled is true",
                details={"hint": "Use an absolute path such as ~/.CyClaw/opentweet-topic.txt"},
            )
        if not isinstance(self.query, OpenTweetQueryConfig):
            raise OpenTweetConfigError("opentweet.query must be a mapping")

    def resolve_api_key(self) -> str:
        token = os.environ.get(self.api_key_env, "").strip()
        if not token:
            raise OpenTweetConfigError(
                f"OpenTweet API key env var {self.api_key_env} is unset or empty",
                details={"env": self.api_key_env},
            )
        return token

    def resolve_query_api_key(self) -> str | None:
        return os.environ.get(self.query.api_key_env, "").strip() or None

    def to_public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("_config_path", None)
        d["api_key_set"] = bool(os.environ.get(self.api_key_env, "").strip())
        d["query_api_key_set"] = bool(os.environ.get(self.query.api_key_env, "").strip())
        return d


def load_opentweet_config(config_path: str = "config.yaml") -> OpenTweetConfig:
    """Load and validate the ``opentweet:`` block. Missing block → disabled."""
    try:
        raw = _get_config(config_path)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise OpenTweetConfigError(
            "Unable to load OpenTweet configuration",
            details={"error_type": type(exc).__name__},
        ) from None
    if not isinstance(raw, dict):
        raise OpenTweetConfigError(
            "config root must be a mapping",
            details={"received_type": type(raw).__name__},
        )
    if "opentweet" not in raw:
        cfg = OpenTweetConfig()
        cfg._config_path = config_path
        return cfg
    block = raw["opentweet"]
    if not isinstance(block, dict):
        raise OpenTweetConfigError(
            "opentweet: block must be a mapping",
            details={"received_type": type(block).__name__},
        )

    known = {
        "enabled",
        "api_base",
        "api_key_env",
        "topic_file",
        "max_topic_chars",
        "max_post_chars",
        "schedule_enabled",
        "schedule_slot",
        "weekday",
        "fire_hour",
        "fire_minute",
        "query",
    }
    unknown = set(block) - known
    if unknown:
        unknown_display = sorted(str(key) for key in unknown)
        raise OpenTweetConfigError(
            f"opentweet: unknown key(s): {unknown_display}",
            details={"unknown": unknown_display},
        )

    q_raw = block.get("query", {})
    if not isinstance(q_raw, dict):
        raise OpenTweetConfigError("opentweet.query must be a mapping")
    unknown_q = set(q_raw) - {"base_url", "api_key_env", "timeout_sec"}
    if unknown_q:
        unknown_display = sorted(str(key) for key in unknown_q)
        raise OpenTweetConfigError(
            f"opentweet.query unknown key(s): {unknown_display}",
            details={"unknown": unknown_display},
        )
    query = OpenTweetQueryConfig(
        base_url=q_raw.get("base_url", DEFAULT_QUERY_BASE_URL),
        api_key_env=q_raw.get("api_key_env", DEFAULT_QUERY_API_KEY_ENV),
        timeout_sec=q_raw.get("timeout_sec", DEFAULT_QUERY_TIMEOUT_SEC),
    )
    cfg = OpenTweetConfig(
        enabled=block.get("enabled", False),
        api_base=block.get("api_base", DEFAULT_API_BASE),
        api_key_env=block.get("api_key_env", DEFAULT_API_KEY_ENV),
        topic_file=block.get("topic_file", ""),
        max_topic_chars=block.get("max_topic_chars", DEFAULT_MAX_TOPIC_CHARS),
        max_post_chars=block.get("max_post_chars", DEFAULT_MAX_POST_CHARS),
        schedule_enabled=block.get("schedule_enabled", False),
        schedule_slot=block.get("schedule_slot", DEFAULT_SCHEDULE_SLOT),
        weekday=block.get("weekday", DEFAULT_WEEKDAY),
        fire_hour=block.get("fire_hour", DEFAULT_FIRE_HOUR),
        fire_minute=block.get("fire_minute", DEFAULT_FIRE_MINUTE),
        query=query,
    )
    cfg._config_path = config_path
    return cfg
