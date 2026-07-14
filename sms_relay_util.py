"""Small stateless helpers shared across the SMS relay modules."""
from __future__ import annotations

import hashlib
import re
import time

_LOG_UNSAFE_RE = re.compile(r"[\r\n\x00-\x1f\x7f]")
_PHONE_HASH_LEN = 16


def now_ts() -> int:
    return int(time.time())


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def phone_hash(phone: str) -> str:
    return sha256_text(phone)[:_PHONE_HASH_LEN]


def log_safe(raw_value: str) -> str:
    """Strip control characters from a value before it reaches a log sink.

    MessageSid arrives as raw, attacker-reachable webhook form data (Twilio's
    signature check authenticates the request, not the shape of every field),
    and is logged verbatim at several call sites. Stripping CR/LF and other
    control characters here, once, at the point it enters the system, closes
    CWE-117 log injection for every downstream logger call instead of
    requiring each call site to remember to sanitize it.
    """
    return _LOG_UNSAFE_RE.sub("", raw_value)
