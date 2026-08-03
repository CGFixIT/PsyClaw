"""CyClaw Telegram channel package.

Out-of-band, opt-in Telegram Bot API adapter for notify-only (T1) and
two-way chat (T2). Runs strictly as a separate process
(``python -m telegram.cli``), never imported by gate.py, graph.py, or
mcp_hybrid_server.py — which preserves CyClaw's six security invariants
by construction.

Public API:
    from telegram import TelegramConfig, load_telegram_config, TelegramError

Usage from the CLI:
    python -m telegram.cli status
    python -m telegram.cli test
    python -m telegram.cli send --chat-id <id> --text "..."
    python -m telegram.cli poll   # requires telegram.mode: chat

See docs/channels/TELEGRAM_DESIGN.md for architecture, phases, and
threat-model obligations.
"""

from utils.telemetry_kill import apply_telemetry_kill

apply_telemetry_kill()

from telegram.config import TelegramConfig, load_telegram_config
from utils.errors import (
    TelegramConfigError,
    TelegramError,
    TelegramRefused,
    TelegramRuntimeError,
)

__all__ = [
    "TelegramConfig",
    "load_telegram_config",
    "TelegramError",
    "TelegramConfigError",
    "TelegramRefused",
    "TelegramRuntimeError",
]

__version__ = "0.1.0"
