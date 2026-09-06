"""Destination allowlist for generation clients."""

from __future__ import annotations

import pytest

from utils.endpoint_trust import (
    EndpointTrustError,
    assert_local_destination,
    assert_loopback,
    assert_online_destination,
    hostname_of,
)


def test_hostname_of_strips_url() -> None:
    assert hostname_of("https://api.x.ai/v1") == "api.x.ai"
    assert hostname_of("http://127.0.0.1:11434/v1") == "127.0.0.1"


def test_assert_loopback_accepts_ollama() -> None:
    assert_loopback("http://127.0.0.1:11434/v1")


def test_assert_loopback_rejects_public() -> None:
    with pytest.raises(EndpointTrustError):
        assert_loopback("https://api.x.ai/v1")


def test_online_requires_confirm() -> None:
    with pytest.raises(EndpointTrustError, match="user_confirmed_online"):
        assert_online_destination(provider="grok", base_url="https://api.x.ai/v1", confirmed=False)
    assert_online_destination(provider="grok", base_url="https://api.x.ai/v1", confirmed=None)


def test_online_allowlist() -> None:
    assert_online_destination(provider="grok", base_url="https://api.x.ai/v1", confirmed=True)
    assert_online_destination(provider="claude", base_url="https://api.anthropic.com/v1", confirmed=True)
    with pytest.raises(EndpointTrustError):
        assert_online_destination(provider="grok", base_url="https://evil.example/v1", confirmed=True)
    with pytest.raises(EndpointTrustError):
        assert_online_destination(provider="claude", base_url="https://api.x.ai/v1", confirmed=True)


@pytest.mark.parametrize("url", ["http://[::1", "http://[not-ip]/"])
def test_malformed_url_is_typed(url):
    with pytest.raises(EndpointTrustError, match="malformed endpoint URL"):
        hostname_of(url)


@pytest.mark.parametrize("hosts", [[], "host.docker.internal", None, ["other.internal"], [None]])
def test_local_destination_rejects_untrusted_or_malformed_allowlist(hosts):
    with pytest.raises(EndpointTrustError):
        assert_local_destination("http://host.docker.internal:11434/v1", hosts)


def test_local_destination_explicit_host_is_exact():
    assert_local_destination("http://host.docker.internal:11434/v1", ["HOST.DOCKER.INTERNAL"])
    with pytest.raises(EndpointTrustError):
        assert_local_destination("http://host.docker.internal.evil:11434", ["host.docker.internal"])
