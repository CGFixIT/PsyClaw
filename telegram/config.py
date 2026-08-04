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

import yaml  # type: ignore[import-untyped]  # project intentionally carries no PyYAML stubs

from utils.errors import TelegramConfigError
from utils.logger import _get_config

DEFAULT_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"  # noqa: S105 -- env VAR NAME, not a secret value
DEFAULT_MODE = "notify"  # "notify" (outbound only) | "chat" (2-way)
DEFAULT_API_BASE = "https://api.telegram.org"
DEFAULT_POLL_TIMEOUT_SEC = 30
DEFAULT_MAX_MESSAGE_CHARS = 4000  # align with policy.prompt_filter.max_input_chars
MAX_MESSAGE_CHARS = 4096  # Telegram Bot API text ceiling; default stays conservative
DEFAULT_QUERY_BASE_URL = "http://127.0.0.1:8787"  # DevSkim: ignore DS162092 - loopback by design
DEFAULT_API_KEY_ENV = "CYCLAW_API_KEY"
DEFAULT_QUERY_TIMEOUT_SEC = 660  # match api.graph_timeout_sec
DEFAULT_RATE_MAX_OPS = 20
DEFAULT_RATE_WINDOW_SECONDS = 60
DEFAULT_ALLOW_HYBRID_CONFIRM = False
DEFAULT_HYBRID_CONFIRM_TTL_SEC = 120
MAX_HYBRID_CONFIRM_TTL_SEC = 300
DEFAULT_MEDIA_MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_TELEGRAM_CLOUD_DOWNLOAD_BYTES = 20 * 1024 * 1024

_VALID_MODES = ("notify", "chat")
# Telegram chat ids are signed 64-bit integers (user positive, groups negative).
_CHAT_ID_RE = re.compile(r"^-?\d{1,20}$")
_MIN_CHAT_ID = -(2**63)
_MAX_CHAT_ID = 2**63 - 1
# Env var names: upper snake.
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SHELL_METACHARS = set(";|&$`<>(){}!*?\"'\\\n\r\t ")


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
            f"{field_name} must be an UPPER_SNAKE env var name",
            # Never echo a malformed value: an operator may have pasted the
            # live secret here instead of its environment-variable name.
            details={"received_type": type(name).__name__},
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
    try:
        parsed = urlparse(url)
        _ = parsed.port
    except ValueError as exc:
        raise TelegramConfigError(f"{field_name} is not a valid URL") from exc
    if parsed.scheme not in ("http", "https"):
        raise TelegramConfigError(
            f"{field_name} must be http or https, got scheme={parsed.scheme!r}",
            details={"received": url},
        )
    if parsed.username is not None or parsed.password is not None:
        raise TelegramConfigError(f"{field_name} must not contain URL credentials")
    if parsed.query or parsed.fragment:
        raise TelegramConfigError(f"{field_name} must not contain a query or fragment")
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
class TelegramMediaConfig:
    """Default-off controls for the T4 media-to-fsconnect staging bridge."""

    enabled: bool = False
    fsconnect_root: str = ""
    max_download_bytes: int = DEFAULT_MEDIA_MAX_DOWNLOAD_BYTES

    def __post_init__(self) -> None:
        _validate_bool(self.enabled, "telegram.media.enabled")
        self.max_download_bytes = _validate_positive_int(
            self.max_download_bytes,
            "telegram.media.max_download_bytes",
        )
        if self.max_download_bytes > MAX_TELEGRAM_CLOUD_DOWNLOAD_BYTES:
            raise TelegramConfigError(
                "telegram.media.max_download_bytes must be <= 20 MiB",
                details={"received": self.max_download_bytes},
            )
        if not isinstance(self.fsconnect_root, str):
            raise TelegramConfigError(
                "telegram.media.fsconnect_root must be a string",
                details={"received_type": type(self.fsconnect_root).__name__},
            )
        if "\x00" in self.fsconnect_root:
            raise TelegramConfigError("telegram.media.fsconnect_root must not contain a NUL byte")
        self.fsconnect_root = self.fsconnect_root.strip()
        if self.enabled and not self.fsconnect_root:
            raise TelegramConfigError(
                "telegram.media.fsconnect_root is required when telegram.media.enabled is true",
                details={"hint": "Use one explicit path from fsconnect.writable_roots."},
            )
        if self.enabled:
            expanded_root = os.path.expanduser(os.path.expandvars(self.fsconnect_root))
            if not os.path.isabs(expanded_root):
                raise TelegramConfigError(
                    "telegram.media.fsconnect_root must be an absolute path when enabled",
                    details={"hint": "Use a dedicated absolute fsconnect.writable_roots path."},
                )


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
    hybrid_confirm_ttl_sec: int = DEFAULT_HYBRID_CONFIRM_TTL_SEC
    rate_limit: TelegramRateLimitConfig = field(default_factory=TelegramRateLimitConfig)
    query: TelegramQueryConfig = field(default_factory=TelegramQueryConfig)
    media: TelegramMediaConfig = field(default_factory=TelegramMediaConfig)
    # Bookkeeping — not a config key.
    _config_path: str = "config.yaml"

    def __post_init__(self) -> None:
        _validate_bool(self.enabled, "telegram.enabled")
        _validate_bool(self.allow_hybrid_confirm, "telegram.allow_hybrid_confirm")
        self.hybrid_confirm_ttl_sec = _validate_positive_int(
            self.hybrid_confirm_ttl_sec,
            "telegram.hybrid_confirm_ttl_sec",
        )
        if self.hybrid_confirm_ttl_sec > MAX_HYBRID_CONFIRM_TTL_SEC:
            raise TelegramConfigError(
                f"telegram.hybrid_confirm_ttl_sec must be <= {MAX_HYBRID_CONFIRM_TTL_SEC}",
                details={"received": self.hybrid_confirm_ttl_sec},
            )

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
        if self.max_message_chars > MAX_MESSAGE_CHARS:
            raise TelegramConfigError(
                f"telegram.max_message_chars must be <= {MAX_MESSAGE_CHARS}",
                details={"received": self.max_message_chars},
            )

        if not isinstance(self.api_base, str):
            raise TelegramConfigError(
                "telegram.api_base must be an https URL",
                details={"received_type": type(self.api_base).__name__},
            )
        if any(c in self.api_base for c in _SHELL_METACHARS):
            raise TelegramConfigError(
                "telegram.api_base contains disallowed characters",
                details={"received": self.api_base},
            )
        try:
            parsed_api = urlparse(self.api_base)
            _ = parsed_api.port
        except ValueError as exc:
            raise TelegramConfigError("telegram.api_base is not a valid URL") from exc
        if parsed_api.scheme != "https" or not parsed_api.hostname:
            raise TelegramConfigError("telegram.api_base must be an https URL with a hostname")
        if parsed_api.username is not None or parsed_api.password is not None:
            raise TelegramConfigError("telegram.api_base must not contain URL credentials")
        if parsed_api.query or parsed_api.fragment:
            raise TelegramConfigError("telegram.api_base must not contain a query or fragment")
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
            numeric_id = int(s)
            if not _MIN_CHAT_ID <= numeric_id <= _MAX_CHAT_ID:
                raise TelegramConfigError(
                    f"telegram.allowed_chat_ids entry is outside signed 64-bit range: {raw!r}",
                    details={"hint": "Telegram chat ids are signed 64-bit integers."},
                )
            normalized.append(str(numeric_id))
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
        if not isinstance(self.media, TelegramMediaConfig):
            raise TelegramConfigError("telegram.media must be a mapping")

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
    try:
        raw = _get_config(config_path)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise TelegramConfigError(
            "Unable to load Telegram configuration",
            details={"error_type": type(exc).__name__},
        ) from None
    if not isinstance(raw, dict):
        raise TelegramConfigError(
            "config root must be a mapping",
            details={"received_type": type(raw).__name__},
        )
    if "telegram" not in raw:
        cfg = TelegramConfig()
        cfg._config_path = config_path
        return cfg
    block = raw["telegram"]
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
        "hybrid_confirm_ttl_sec",
        "rate_limit",
        "query",
        "media",
    }
    unknown = set(block) - known
    if unknown:
        unknown_display = sorted(str(key) for key in unknown)
        raise TelegramConfigError(
            f"telegram: unknown key(s): {unknown_display}",
            details={"unknown": unknown_display},
        )

    rl_raw = block.get("rate_limit", {})
    if not isinstance(rl_raw, dict):
        raise TelegramConfigError("telegram.rate_limit must be a mapping")
    rate_limit = TelegramRateLimitConfig(
        max_ops=rl_raw.get("max_ops", DEFAULT_RATE_MAX_OPS),
        window_seconds=rl_raw.get("window_seconds", DEFAULT_RATE_WINDOW_SECONDS),
    )
    unknown_rl = set(rl_raw) - {"max_ops", "window_seconds"}
    if unknown_rl:
        unknown_display = sorted(str(key) for key in unknown_rl)
        raise TelegramConfigError(
            f"telegram.rate_limit unknown key(s): {unknown_display}",
            details={"unknown": unknown_display},
        )

    q_raw = block.get("query", {})
    if not isinstance(q_raw, dict):
        raise TelegramConfigError("telegram.query must be a mapping")
    query = TelegramQueryConfig(
        base_url=q_raw.get("base_url", DEFAULT_QUERY_BASE_URL),
        api_key_env=q_raw.get("api_key_env", DEFAULT_API_KEY_ENV),
        timeout_sec=q_raw.get("timeout_sec", DEFAULT_QUERY_TIMEOUT_SEC),
    )
    unknown_q = set(q_raw) - {"base_url", "api_key_env", "timeout_sec"}
    if unknown_q:
        unknown_display = sorted(str(key) for key in unknown_q)
        raise TelegramConfigError(
            f"telegram.query unknown key(s): {unknown_display}",
            details={"unknown": unknown_display},
        )

    media_raw = block.get("media", {})
    if not isinstance(media_raw, dict):
        raise TelegramConfigError("telegram.media must be a mapping")
    media = TelegramMediaConfig(
        enabled=media_raw.get("enabled", False),
        fsconnect_root=media_raw.get("fsconnect_root", ""),
        max_download_bytes=media_raw.get("max_download_bytes", DEFAULT_MEDIA_MAX_DOWNLOAD_BYTES),
    )
    unknown_media = set(media_raw) - {"enabled", "fsconnect_root", "max_download_bytes"}
    if unknown_media:
        unknown_display = sorted(str(key) for key in unknown_media)
        raise TelegramConfigError(
            f"telegram.media unknown key(s): {unknown_display}",
            details={"unknown": unknown_display},
        )

    cfg = TelegramConfig(
        enabled=block.get("enabled", False),
        mode=block.get("mode", DEFAULT_MODE),
        bot_token_env=block.get("bot_token_env", DEFAULT_BOT_TOKEN_ENV),
        # Preserve the configured value for __post_init__ to validate. Coercing
        # here with list(...) turns a scalar such as "42" into ["4", "2"] and
        # can silently weaken the fail-closed allowlist.
        allowed_chat_ids=block.get("allowed_chat_ids", []),
        api_base=block.get("api_base", DEFAULT_API_BASE),
        poll_timeout_sec=block.get("poll_timeout_sec", DEFAULT_POLL_TIMEOUT_SEC),
        max_message_chars=block.get("max_message_chars", DEFAULT_MAX_MESSAGE_CHARS),
        allow_hybrid_confirm=block.get("allow_hybrid_confirm", DEFAULT_ALLOW_HYBRID_CONFIRM),
        hybrid_confirm_ttl_sec=block.get(
            "hybrid_confirm_ttl_sec",
            DEFAULT_HYBRID_CONFIRM_TTL_SEC,
        ),
        rate_limit=rate_limit,
        query=query,
        media=media,
        _config_path=config_path,
    )
    return cfg
