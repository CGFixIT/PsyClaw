"""CyClaw OpenTweet X channel package.

Out-of-band, opt-in posting adapter. Runs strictly as a separate process
(``python -m opentweet.cli``), never imported by gate.py, graph.py, or
mcp_hybrid_server.py — which preserves CyClaw's six security invariants
by construction.

Public API:
    from opentweet import OpenTweetConfig, load_opentweet_config, OpenTweetError

Usage from the CLI:
    python -m opentweet.cli status
    python -m opentweet.cli test
    python -m opentweet.cli post --dry-run
    python -m opentweet.cli schedule-plist   # Darwin, generate-don't-load
    python -m opentweet.cli schedule-task    # Windows, generate-don't-register

See docs/channels/OPENTWEET_DESIGN.md for architecture and threat-model notes.
"""

from utils.telemetry_kill import apply_telemetry_kill

apply_telemetry_kill()

from opentweet.config import OpenTweetConfig, load_opentweet_config
from utils.errors import (
    OpenTweetConfigError,
    OpenTweetError,
    OpenTweetRefused,
    OpenTweetRuntimeError,
)

__all__ = [
    "OpenTweetConfig",
    "load_opentweet_config",
    "OpenTweetError",
    "OpenTweetConfigError",
    "OpenTweetRefused",
    "OpenTweetRuntimeError",
]

__version__ = "0.1.0"
