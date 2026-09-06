"""Interruptible httpx reads so harness chat cancel works on Darwin.

``socket.shutdown(SHUT_RDWR)`` unblocks a hung POST on Linux. On macOS a
timed ``recv`` often stays inside ``poll()`` until the original timeout,
so ``/api/chat/cancel`` returned 200 while the generation worker kept
``GenerationGate``. Wrapping ``connect_tcp`` slices each read so
``abort_in_flight`` is observed within ``_SLICE_SEC`` on every OS.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from functools import partial

import httpx

# Darwin's timed recv/poll often ignores SHUT_RDWR from another thread.
# Slice reads so abort_in_flight is seen within this window on every OS.
_SLICE_SEC = 0.05
_EXPIRED = 0


def _deadline_of(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    return time.monotonic() + timeout


def _slice_of(deadline: float | None) -> float:
    if deadline is None:
        return _SLICE_SEC
    left = deadline - time.monotonic()
    if left <= 0:
        return _EXPIRED
    return min(_SLICE_SEC, left)


def _retry_timeout(exc: BaseException, deadline: float | None) -> bool:
    if type(exc).__name__ != "ReadTimeout":
        return False
    if deadline is None:
        return True
    return time.monotonic() < deadline


def _method(target: object, name: str) -> Callable[..., object]:
    found = getattr(target, name)
    if not callable(found):
        raise TypeError(name)
    return found


class _SlicedStream:
    """httpcore stream whose reads wake every ``_SLICE_SEC`` to honor abort."""

    def __init__(self, inner: object, cancel: threading.Event) -> None:
        extra = getattr(inner, "get_extra_info", None)
        sock = extra("socket") if callable(extra) else getattr(inner, "_sock", None)
        self._inner = inner
        self._cancel = cancel
        self._sock = sock

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        deadline = _deadline_of(timeout)
        reader = _method(self._inner, "read")
        while True:
            if self._cancel.is_set():
                self.close()
            try:
                return reader(max_bytes, timeout=_slice_of(deadline))
            except Exception as exc:
                if not _retry_timeout(exc, deadline):
                    raise

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        if self._cancel.is_set():
            self.close()
        _method(self._inner, "write")(buffer, timeout=timeout)

    def close(self) -> None:
        closer = getattr(self._inner, "close", None)
        if callable(closer):
            closer()

    def start_tls(
        self,
        ssl_context: object,
        server_hostname: object = None,
        timeout: object = None,
    ) -> _SlicedStream:
        starter = _method(self._inner, "start_tls")
        return _SlicedStream(starter(ssl_context, server_hostname, timeout), self._cancel)

    def get_extra_info(self, extra_key: str) -> object:
        extra = getattr(self._inner, "get_extra_info", None)
        return extra(extra_key) if callable(extra) else None


def _wrap_connect(
    connect: Callable[..., object],
    cancel: threading.Event,
    *args: object,
    **kwargs: object,
) -> _SlicedStream:
    return _SlicedStream(connect(*args, **kwargs), cancel)


def arm_cancel_reads(client: httpx.Client, cancel: threading.Event) -> None:
    """Wrap the default httpcore backend so in-flight reads honor ``cancel``.

    MockTransport (tests) has no pool; this is then a no-op.
    """
    pool = getattr(getattr(client, "_transport", None), "_pool", None)
    backend = getattr(pool, "_network_backend", None)
    connect = getattr(backend, "connect_tcp", None)
    if callable(connect):
        backend.connect_tcp = partial(_wrap_connect, connect, cancel)
