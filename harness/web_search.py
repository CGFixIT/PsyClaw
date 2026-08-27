"""Allowlist-only web fetch for the harness console (``/web``).

Default-off. When enabled, the console may GET only operator-allowlisted
http(s) URLs. There is no search engine, no browser, no JS, no crawl.

Offline mode is the point: the local 27b still cannot invent a host. A
fetch is attempted only if (1) ``web_enabled`` is true, (2) the URL's
host+path matches the allowlist, and (3) DNS resolves to a public
(non-loopback, non-private, non-link-local) address *before* the GET.
httpx re-resolves on connect, so a residual rebinding window remains
(see ``assert_public_host``). Empty allowlist is fail-closed.

I6: this module is harness-local. It never imports ``gate``, ``graph``,
``mcp_hybrid_server``, ``agentic``, ``sync``, or ``guardrails``. Tool
name-gating uses ``utils.tool_broker``. It never starts ``/api/agent/*``.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
from html import parser, unescape
from pathlib import Path
from urllib.parse import urlparse

import httpx

from harness.config import HarnessConfig, _atomic_write_json
from utils.errors import AgenticError
from utils.tool_broker import ToolDenied, assert_allowed

log = logging.getLogger("cyclaw.harness.web_search")

_UTF8 = "utf-8"
_SCHEMES = frozenset(("http", "https"))
_MAX_ALLOW = 32
_MAX_BYTES = 262_144
_TIMEOUT_SEC = 8.0
_MAX_QUERY = 200
_MAX_SNIPPET = 160
_MAX_HITS_PER_URL = 3
_MAX_SEARCH_URLS = 8
_MAX_CONTEXT = 4000
_MAX_RAW = 500
_SNIP_BEFORE = 40
_SNIP_AFTER = 120
_HTTP_OK_BELOW = 400
_BLOCKED_HOSTS = frozenset((
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.goog",
))
_SKIP_TAGS = frozenset(("script", "style", "noscript", "template"))
_PATH_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~/=+-"
)
_BREAK_TAGS = frozenset(("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "pre"))
_TEXT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/javascript",
)
