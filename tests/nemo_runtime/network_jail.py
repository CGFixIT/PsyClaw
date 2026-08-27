"""Fail closed if NeMo init/tests open a non-loopback socket or DNS name."""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class UnexpectedNetworkError(RuntimeError):
    """A test or engine init tried to leave loopback."""


def _check_host(host: object) -> None:
    name = str(host or "")
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    if name not in _LOOPBACK_HOSTS:
        raise UnexpectedNetworkError(f"unexpected DNS/network: {host!r}")


@contextmanager
def loopback_only() -> Iterator[None]:
    """Patch getaddrinfo and socket.connect so only loopback is reachable."""

    orig_getaddrinfo = socket.getaddrinfo
    orig_connect = socket.socket.connect

    def jailed_getaddrinfo(host: object, port: object, *args: object, **kwargs: object) -> list[Any]:
        _check_host(host)
        return orig_getaddrinfo(host, port, *args, **kwargs)  # type: ignore[arg-type]

    def jailed_connect(self: socket.socket, address: object) -> None:
        host: object
        if isinstance(address, tuple) and address:
            host = address[0]
        else:
            host = address
        _check_host(host)
        return orig_connect(self, address)

    socket.getaddrinfo = jailed_getaddrinfo  # type: ignore[assignment]
    socket.socket.connect = jailed_connect  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.getaddrinfo = orig_getaddrinfo
        socket.socket.connect = orig_connect  # type: ignore[method-assign]


# Keep a typed alias so tests can assert the helper is a context manager.
install_loopback_jail: Callable[[], Any] = loopback_only
