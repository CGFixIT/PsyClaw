"""TelegramConfig dataclass and validating loader for the CyClaw telegram: block.

Reads the ``telegram:`` block from CyClaw's single-source-of-truth ``config.yaml``
via ``utils.logger._get_config`` (shared cached load; tests reset it via
``reset_config_cache``). Purely additive: absence of the block disables the
channel entirely without perturbing the gateway, graph, or MCP server.

Hardened defaults (conservative, matching CyClaw's offline-first posture):

  - enabled:           False     whole layer is opt-in; absent key => disabled
  - mode:              "notify"  outbound-only; "chat" is opt-in 2-way
  - allowed_chat_ids:  []        empty list refuses ALL traffic when enabled
  - bot_token_env:     TELEGRAM_BOT_TOKEN   token never stored in config.yaml
  - query.base_url:    http://127.0.0.1:8787  loopback CyClaw only

This module is part of a package that is NEVER imported by gate.py, graph.py, or
mcp_hybrid_server.py. That isolation preserves CyClaw's six security invariants.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

from utils.errors import TelegramConfigError
from utils.logger import _get_config

DEFAULT_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"  # noqa: S105 -- env VAR NAME, not a secret value
DEFAULT_MODE = "notify"  # "notify" (outbound only) | "chat" (2-way)
DEFAULT_API_BASE = "https://api.telegram.org"
DEFAULT_POLL_TIMEOUT_SEC = 30
DEFAULT_MAX_MESSAGE_CHARS = 4000  # align with policy.prompt_filter.max_input_chars
DEFAULT_QUERY_BASE_URL = "http://127.0.0.1:8787"  # DevSkim: ignore DS162092 - loopback by design
DEFAULT_API_KEY_ENV = "CYCLAW_API_KEY"
DEFAULT_QUERY_TIMEOUT_SEC = 660  # match api.graph_timeout_sec
DEFAULT_RATE_MAX_OPS = 20
DEFAULT_RATE_WINDOW_SECONDS = 60
DEFAULT_ALLOW_HYBRID_CONFIRM = False  # T3 — not wired in skeleton

_VALID_MODES = ("notify", "chat")
# Telegram chat ids are signed 64-bit integers (user positive, groups negative).
_CHAT_ID_RE = re.compile(r"^-?\d{1,20}$")
# Env var names: upper snake.
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SHELL_METACHARS = set(";|&$`<>(){}[]!*?\"'\\\n\r\t ")


def _validate_bool(value: object, field_name: str) -> None:
    if not isinstance(value, bool):
        raise TelegramConfigError(
            f"{field_name} must be a YAML boolean, got {type(value).__name__}",
            details={"received": repr(value)},
        )


def _validate_positive_int(value: object, field_name: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TelegramConfigError(
            f"{field_name} must be an integer, got {type(value).__name__}",
            details={"received": repr(value)},
        )
    if allow_zero:
        if value < 0:
            raise TelegramConfigError(
                f"{field_name} must be >= 0, got: {value}",
                details={"received": value},
            )
    elif value <= 0:
        raise TelegramConfigError(
            f"{field_name} must be > 0, got: {value}",
            details={"received": value},
        )
    return value


def _validate_env_name(name: str, field_name: str) -> str:
    if not isinstance(name, str) or not _ENV_NAME_RE.match(name):
        raise TelegramConfigError(
            f"{field_name} must be an UPPER_SNAKE env var name, got: {name!r}",
            details={"received": name},
        )
    return name


def _validate_loopback_url(url: str, field_name: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise TelegramConfigError(
            f"{field_name} is required",
            details={"hint": "Use a loopback URL such as http://127.0.0.1:8787"},
        )
    if any(c in url for c in _SHELL_METACHARS):
        raise TelegramConfigError(
            f"{field_name} contains disallowed characters",
            details={"received": url},
        )
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise TelegramConfigError(
            f"{field_name} must be http or https, got scheme={parsed.scheme!r}",
            details={"received": url},
        )
    host = (parsed.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise TelegramConfigError(
            f"{field_name} must target loopback (127.0.0.1/localhost), got host={host!r}",
            details={
                "received": url,
                "hint": "Telegram talks to CyClaw only over loopback; do not point this at a LAN or public host.",
            },
        )
    return url.rstrip("/")


@dataclass
class TelegramRateLimitConfig:
    """Per-process soft rate limit for outbound Bot API + inbound handling."""

    max_ops: int = DEFAULT_RATE_MAX_OPS
    window_seconds: int = DEFAULT_RATE_WINDOW_SECONDS

    def __post_init__(self) -> None:
        self.max_ops = _validate_positive_int(self.max_ops, "telegram.rate_limit.max_ops")
        self.window_seconds = _validate_positive_int(
            self.window_seconds, "telegram.rate_limit.window_seconds"
        )


@dataclass
class TelegramQueryConfig:
    """How this channel reaches the existing CyClaw ``POST /query`` surface."""

    base_url: str = DEFAULT_QUERY_BASE_URL
    api_key_env: str = DEFAULT_API_KEY_ENV
    timeout_sec: int = DEFAULT_QUERY_TIMEOUT_SEC

    def __post_init__(self) -> None:
        self.base_url = _validate_loopback_url(self.base_url, "telegram.query.base_url")
        self.api_key_env = _validate_env_name(self.api_key_env, "telegram.query.api_key_env")
        self.timeout_sec = _validate_positive_int(self.timeout_sec, "telegram.query.timeout_sec")


@dataclass
class TelegramConfig:
    """Parsed and validated ``telegram:`` block from config.yaml."""

    enabled: bool = False
    mode: str = DEFAULT_MODE
    bot_token_env: str = DEFAULT_BOT_TOKEN_ENV
    allowed_chat_ids: list[str] = field(default_factory=list)
    api_base: str = DEFAULT_API_BASE
    poll_timeout_sec: int = DEFAULT_POLL_TIMEOUT_SEC
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS
    allow_hybrid_confirm: bool = DEFAULT_ALLOW_HYBRID_CONFIRM
    rate_limit: TelegramRateLimitConfig = field(default_factory=TelegramRateLimitConfig)
    query: TelegramQueryConfig = field(default_factory=TelegramQueryConfig)
    # Bookkeeping — not a config key.
    _config_path: str = "config.yaml"

    def __post_init__(self) -> None:
        _validate_bool(self.enabled, "telegram.enabled")
        _validate_bool(self.allow_hybrid_confirm, "telegram.allow_hybrid_confirm")

        if self.mode not in _VALID_MODES:
            raise TelegramConfigError(
                f"telegram.mode must be one of {_VALID_MODES}, got: {self.mode!r}",
                details={"received": self.mode},
            )

        self.bot_token_env = _validate_env_name(self.bot_token_env, "telegram.bot_token_env")
        self.poll_timeout_sec = _validate_positive_int(
            self.poll_timeout_sec, "telegram.poll_timeout_sec"
        )
        self.max_message_chars = _validate_positive_int(
            self.max_message_chars, "telegram.max_message_chars"
        )

        if not isinstance(self.api_base, str) or not self.api_base.startswith("https://"):
            raise TelegramConfigError(
                "telegram.api_base must be an https URL",
                details={"received": self.api_base},
            )
        if any(c in self.api_base for c in _SHELL_METACHARS):
            raise TelegramConfigError(
                "telegram.api_base contains disallowed characters",
                details={"received": self.api_base},
            )
        self.api_base = self.api_base.rstrip("/")

        if not isinstance(self.allowed_chat_ids, list):
            raise TelegramConfigError(
                "telegram.allowed_chat_ids must be a list of chat id strings/ints",
                details={"received_type": type(self.allowed_chat_ids).__name__},
            )
        normalized: list[str] = []
        for raw in self.allowed_chat_ids:
            if isinstance(raw, bool) or not isinstance(raw, (int, str)):
                raise TelegramConfigError(
                    "telegram.allowed_chat_ids entries must be int or digit-string",
                    details={"received": repr(raw)},
                )
            s = str(raw).strip()
            if not _CHAT_ID_RE.match(s):
                raise TelegramConfigError(
                    f"telegram.allowed_chat_ids entry invalid: {raw!r}",
                    details={"hint": "Telegram chat ids are signed integers."},
                )
            normalized.append(s)
        # De-dupe while preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for cid in normalized:
            if cid not in seen:
                seen.add(cid)
                unique.append(cid)
        self.allowed_chat_ids = unique

        if not isinstance(self.rate_limit, TelegramRateLimitConfig):
            raise TelegramConfigError("telegram.rate_limit must be a mapping")
        if not isinstance(self.query, TelegramQueryConfig):
            raise TelegramConfigError("telegram.query must be a mapping")

        # Fail loud when enabled with an empty allowlist: nothing would ever work,
        # and silent "enabled but dead" is worse than a config error at load time.
        if self.enabled and not self.allowed_chat_ids:
            raise TelegramConfigError(
                "telegram.enabled is true but telegram.allowed_chat_ids is empty",
                details={
                    "hint": "Add at least one chat id, or set enabled: false. Empty allowlist is a hard refuse.",
                },
            )

    def is_chat_allowed(self, chat_id: int | str) -> bool:
        return str(chat_id).strip() in set(self.allowed_chat_ids)

    def resolve_bot_token(self) -> str:
        """Read the bot token from the configured env var. Never logs the value."""
        token = os.environ.get(self.bot_token_env, "").strip()
        if not token:
            raise TelegramConfigError(
                f"Bot token env var {self.bot_token_env} is unset or empty",
                details={"env": self.bot_token_env},
            )
        return token

    def resolve_api_key(self) -> str | None:
        """Optional CyClaw API key for POST /query. Empty is allowed when gate has none."""
        return os.environ.get(self.query.api_key_env, "").strip() or None

    def to_public_dict(self) -> dict[str, Any]:
        """Config surface safe for status/selftest (no secrets)."""
        d = asdict(self)
        d.pop("_config_path", None)
        d["bot_token_set"] = bool(os.environ.get(self.bot_token_env, "").strip())
        d["api_key_set"] = bool(os.environ.get(self.query.api_key_env, "").strip())
        return d


def load_telegram_config(config_path: str = "config.yaml") -> TelegramConfig:
    """Load and validate the ``telegram:`` block.

    Absence of the block returns a disabled default config (enabled=False).
    Presence with invalid keys or types raises TelegramConfigError.
    """
    raw = _get_config(config_path)
    block = raw.get("telegram")
    if block is None:
        cfg = TelegramConfig()
        cfg._config_path = config_path
        return cfg
    if not isinstance(block, dict):
        raise TelegramConfigError(
            "telegram: block must be a mapping",
            details={"received_type": type(block).__name__},
        )

    known = {
        "enabled",
        "mode",
        "bot_token_env",
        "allowed_chat_ids",
        "api_base",
        "poll_timeout_sec",
        "max_message_chars",
        "allow_hybrid_confirm",
        "rate_limit",
        "query",
    }
    unknown = set(block) - known
    if unknown:
        raise TelegramConfigError(
            f"telegram: unknown key(s): {sorted(unknown)}",
            details={"unknown": sorted(unknown)},
        )

    rl_raw = block.get("rate_limit") or {}
    if not isinstance(rl_raw, dict):
        raise TelegramConfigError("telegram.rate_limit must be a mapping")
    rate_limit = TelegramRateLimitConfig(
        max_ops=rl_raw.get("max_ops", DEFAULT_RATE_MAX_OPS),
        window_seconds=rl_raw.get("window_seconds", DEFAULT_RATE_WINDOW_SECONDS),
    )
    unknown_rl = set(rl_raw) - {"max_ops", "window_seconds"}
    if unknown_rl:
        raise TelegramConfigError(
            f"telegram.rate_limit unknown key(s): {sorted(unknown_rl)}",
            details={"unknown": sorted(unknown_rl)},
        )

    q_raw = block.get("query") or {}
    if not isinstance(q_raw, dict):
        raise TelegramConfigError("telegram.query must be a mapping")
    query = TelegramQueryConfig(
        base_url=q_raw.get("base_url", DEFAULT_QUERY_BASE_URL),
        api_key_env=q_raw.get("api_key_env", DEFAULT_API_KEY_ENV),
        timeout_sec=q_raw.get("timeout_sec", DEFAULT_QUERY_TIMEOUT_SEC),
    )
    unknown_q = set(q_raw) - {"base_url", "api_key_env", "timeout_sec"}
    if unknown_q:
        raise TelegramConfigError(
            f"telegram.query unknown key(s): {sorted(unknown_q)}",
            details={"unknown": sorted(unknown_q)},
        )

    cfg = TelegramConfig(
        enabled=block.get("enabled", False),
        mode=block.get("mode", DEFAULT_MODE),
        bot_token_env=block.get("bot_token_env", DEFAULT_BOT_TOKEN_ENV),
        allowed_chat_ids=list(block.get("allowed_chat_ids") or []),
        api_base=block.get("api_base", DEFAULT_API_BASE),
        poll_timeout_sec=block.get("poll_timeout_sec", DEFAULT_POLL_TIMEOUT_SEC),
        max_message_chars=block.get("max_message_chars", DEFAULT_MAX_MESSAGE_CHARS),
        allow_hybrid_confirm=block.get("allow_hybrid_confirm", DEFAULT_ALLOW_HYBRID_CONFIRM),
        rate_limit=rate_limit,
        query=query,
        _config_path=config_path,
    )
    return cfg
