"""In-process sliding-window rate limit for the Telegram channel.

Separate from ``utils.ratelimit`` (gateway per-IP limiter) on purpose: this
process is out-of-band and must not share the gateway's sqlite path. T0 uses a
process-local counter; T1 may swap in a dedicated sqlite file under ``data/``.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from utils.errors import TelegramRefused


class SlidingWindowLimiter:
    """Thread-safe sliding window: at most ``max_ops`` events per ``window_seconds``."""

    def __init__(self, max_ops: int, window_seconds: int) -> None:
        if max_ops <= 0 or window_seconds <= 0:
            raise ValueError("max_ops and window_seconds must be > 0")
        self._max_ops = max_ops
        self._window = float(window_seconds)
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        """Raise TelegramRefused if ``key`` is over budget; otherwise record one op."""
        now = time.monotonic()
        with self._lock:
            q = self._events.setdefault(key, deque())
            cutoff = now - self._window
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self._max_ops:
                raise TelegramRefused(
                    f"rate limit exceeded for {key!r}",
                    details={
                        "gate": "rate_limit",
                        "key": key,
                        "max_ops": self._max_ops,
                        "window_seconds": int(self._window),
                    },
                )
            q.append(now)


# Process-wide limiter instances keyed by (max_ops, window) so config reloads
# with the same knobs share state; different knobs get a fresh window.
_LIMITERS: dict[tuple[int, int], SlidingWindowLimiter] = {}
_LIMITERS_LOCK = threading.Lock()


def get_limiter(max_ops: int, window_seconds: int) -> SlidingWindowLimiter:
    key = (max_ops, window_seconds)
    with _LIMITERS_LOCK:
        lim = _LIMITERS.get(key)
        if lim is None:
            lim = SlidingWindowLimiter(max_ops, window_seconds)
            _LIMITERS[key] = lim
        return lim


def reset_limiters_for_tests() -> None:
    """Drop process-wide limiter state (unit tests only)."""
    with _LIMITERS_LOCK:
        _LIMITERS.clear()
