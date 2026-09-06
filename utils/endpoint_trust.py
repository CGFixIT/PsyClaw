"""Destination allowlist for generation clients. Not a substitute for I3."""

from __future__ import annotations

from urllib.parse import urlparse

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_ONLINE_HOSTS = {
    "grok": frozenset({"api.x.ai"}),
    "claude": frozenset({"api.anthropic.com"}),
}
_DEFAULT_URLS = {
    "grok": "https://api.x.ai/v1",
    "claude": "https://api.anthropic.com/v1",
}


class EndpointTrustError(ValueError):
    """Resolved destination is not allowed for this provider."""


def hostname_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        # Keep malformed URLs on the same typed failure path as denied hosts so
        # graph callers can return an audited error instead of a parser traceback.
        raise EndpointTrustError("malformed endpoint URL") from None
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def assert_loopback(base_url: str) -> None:
    host = hostname_of(base_url)
    if host not in _LOOPBACK:
        raise EndpointTrustError(f"local LLM endpoint must be loopback, got {host!r}")


def assert_local_destination(base_url: str, trusted_hosts: object = ()) -> None:
    """Allow loopback or an explicitly trusted operator-owned model host."""
    host = hostname_of(base_url)
    if host in _LOOPBACK:
        return
    # Trust is an operator assertion that this host may receive local context;
    # hostname matching neither pins DNS nor grants online-provider consent.
    # Reject malformed lists rather than accidentally using substring matching.
    if isinstance(trusted_hosts, (list, tuple)) and host and any(
        isinstance(item, str) and host == item.lower() for item in trusted_hosts
    ):
        return
    raise EndpointTrustError("local LLM endpoint is not loopback or an explicitly trusted host")


def assert_online_destination(*, provider: str, base_url: str, confirmed: bool | None) -> None:
    """Allowlist the host. Explicit ``confirmed is False`` refuses (I3).

    ``confirmed is None`` means the node was invoked without a gate stamp
    (unit tests / incomplete state): still enforce the host allowlist.
    """
    if confirmed is False:
        raise EndpointTrustError("online destination requires user_confirmed_online")
    allowed = _ONLINE_HOSTS.get(provider)
    if not allowed:
        raise EndpointTrustError(f"unknown online provider {provider!r}")
    host = hostname_of(base_url or _DEFAULT_URLS.get(provider, ""))
    if host not in allowed:
        raise EndpointTrustError(f"{provider} destination {host!r} is not in the allowlist")
