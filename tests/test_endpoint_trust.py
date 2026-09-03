"""Destination allowlist for generation clients."""

from __future__ import annotations

import pytest

from utils.endpoint_trust import (
    EndpointTrustError,
    assert_loopback,
    assert_online_destination,
    hostname_of,
)


def test_hostname_of_strips_url() -> None:
    assert hostname_of("https://api.x.ai/v1") == "api.x.ai"
    assert hostname_of("http://127.0.0.1:11434/v1") == "127.0.0.1"
    assert hostname_of("http://[::1]:11434/v1") == "::1"


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
    with pytest.raises(EndpointTrustError, match="unknown online provider"):
        assert_online_destination(provider="not-a-provider", base_url="https://api.x.ai/v1", confirmed=True)
