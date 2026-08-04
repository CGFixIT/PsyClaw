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


class RateLimitReservation:
    """Capacity held for a bounded batch and charged only when consumed."""

    def __init__(self, limiter: SlidingWindowLimiter, key: str, cost: int) -> None:
        self._limiter = limiter
        self._key = key
        self._remaining = cost

    def consume(self) -> None:
        """Convert one held slot into an event at the actual operation time."""
        if self._remaining <= 0:
            raise RuntimeError("rate-limit reservation exhausted")
        self._limiter._consume_reserved(self._key)
        self._remaining -= 1

    def close(self) -> None:
        """Release every held slot that was not consumed."""
        if self._remaining > 0:
            self._limiter._release_reserved(self._key, self._remaining)
            self._remaining = 0


class SlidingWindowLimiter:
    """Thread-safe sliding window: at most ``max_ops`` events per ``window_seconds``."""

    def __init__(self, max_ops: int, window_seconds: int) -> None:
        if max_ops <= 0 or window_seconds <= 0:
            raise ValueError("max_ops and window_seconds must be > 0")
        self._max_ops = max_ops
        self._window = float(window_seconds)
        self._events: dict[str, deque[float]] = {}
        self._reserved: dict[str, int] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _validate_cost(cost: int) -> None:
        if isinstance(cost, bool) or not isinstance(cost, int) or cost <= 0:
            raise ValueError("cost must be a positive integer")

    def _prune(self, q: deque[float], now: float) -> None:
        cutoff = now - self._window
        while q and q[0] <= cutoff:
            q.popleft()

    def _ensure_capacity(
        self,
        key: str,
        q: deque[float],
        *,
        cost: int,
        now: float,
    ) -> None:
        """Raise under ``self._lock`` when events plus holds cannot fit ``cost``."""
        if cost > self._max_ops:
            raise TelegramRefused(
                f"operation cost exceeds rate limit capacity for {key!r}",
                details={
                    "gate": "rate_limit_capacity",
                    "key": key,
                    "cost": cost,
                    "max_ops": self._max_ops,
                    "window_seconds": int(self._window),
                },
            )
        reserved = self._reserved.get(key, 0)
        if len(q) + reserved + cost <= self._max_ops:
            return
        needed = len(q) + reserved + cost - self._max_ops
        details: dict[str, int | float | str] = {
            "gate": "rate_limit",
            "key": key,
            "max_ops": self._max_ops,
            "window_seconds": int(self._window),
        }
        if q and needed <= len(q):
            # Held capacity may be released sooner than an event expires. When
            # events alone can provide the needed slots, report that deadline.
            event_index = needed - 1
            details["retry_after"] = max(
                0.0,
                q[event_index] + self._window - now,
            )
        raise TelegramRefused(
            f"rate limit exceeded for {key!r}",
            details=details,
        )

    def check(self, key: str, *, cost: int = 1) -> None:
        """Atomically record ``cost`` operations or refuse without recording any."""
        self._validate_cost(cost)
        with self._lock:
            # Sample inside the lock so contending threads cannot append
            # timestamps out of chronological order.
            now = time.monotonic()
            q = self._events.setdefault(key, deque())
            self._prune(q, now)
            self._ensure_capacity(key, q, cost=cost, now=now)
            q.extend([now] * cost)

    def reserve(self, key: str, *, cost: int) -> RateLimitReservation:
        """Hold capacity for a batch, charging each slot only when consumed."""
        reservation, _ = self.reserve_up_to(key, minimum=cost, maximum=cost)
        return reservation

    def reserve_up_to(
        self,
        key: str,
        *,
        minimum: int,
        maximum: int,
    ) -> tuple[RateLimitReservation, int]:
        """Atomically hold the most available slots in ``[minimum, maximum]``."""
        self._validate_cost(minimum)
        self._validate_cost(maximum)
        if minimum > maximum:
            raise ValueError("minimum reservation cannot exceed maximum")
        with self._lock:
            now = time.monotonic()
            q = self._events.setdefault(key, deque())
            self._prune(q, now)
            self._ensure_capacity(key, q, cost=minimum, now=now)
            held = self._reserved.get(key, 0)
            available = self._max_ops - len(q) - held
            granted = min(maximum, available)
            self._reserved[key] = held + granted
        return RateLimitReservation(self, key, granted), granted

    def _consume_reserved(self, key: str) -> None:
        with self._lock:
            held = self._reserved.get(key, 0)
            if held <= 0:
                raise RuntimeError("rate-limit reservation is not active")
            if held == 1:
                self._reserved.pop(key, None)
            else:
                self._reserved[key] = held - 1
            now = time.monotonic()
            q = self._events.setdefault(key, deque())
            self._prune(q, now)
            q.append(now)

    def _release_reserved(self, key: str, cost: int) -> None:
        with self._lock:
            held = self._reserved.get(key, 0)
            if cost > held:
                raise RuntimeError("cannot release more rate-limit capacity than held")
            remaining = held - cost
            if remaining:
                self._reserved[key] = remaining
            else:
                self._reserved.pop(key, None)


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
