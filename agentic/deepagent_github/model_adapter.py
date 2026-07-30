"""Model settings and chat-model construction for the Deep Agents GitHub harness.

Local providers build an OpenAI-compatible client against a loopback endpoint.
Cloud providers (Grok, Claude) are the agentic-plane analog of the core graph's
triple-gated fallback and sit behind a six-condition chain -- see
``DeepAgentModelSettings.from_config``.

What transfers from ``llm/client.py`` is the DISCIPLINE, not the classes: keys
live in env vars only, availability is pure key presence with no network probe,
a missing key fails closed, and an error carries the required env var's NAME and
never its value. The classes themselves are single-shot ``generate(prompt) -> str``
wrappers with no tool-calling, and ``agentic/`` importing ``llm/`` would be the
wrong direction besides.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agentic.config import CLOUD_KEY_ENVS, DeepAgentGitHubConfig
from utils.errors import AgenticError

_LOCAL_EXTRA = "agentic-deepagents"
_CLOUD_EXTRA = "agentic-deepagents-cloud"


@dataclass(frozen=True)
class DeepAgentModelSettings:
    """Resolved model settings for one build of the harness."""

    provider: str
    base_url: str
    model: str
    is_cloud: bool = False

    @classmethod
    def from_config(
        cls, cfg: DeepAgentGitHubConfig, *, cloud_provider: str | None = None
    ) -> DeepAgentModelSettings:
        """Resolve settings for the local provider, or for a gated cloud provider.

        Passing *cloud_provider* asserts gates 3 and 4 of the chain
        (``allow_cloud_providers`` and that provider's own ``enabled``) via
        ``cfg.cloud_provider``; it returns None when either is false, and this
        refuses rather than silently falling back to local. Gate 5 (the API key)
        is checked at build time in :func:`build_chat_model`; gates 1, 2, and 6
        belong to the caller.
        """
        if cloud_provider is None:
            return cls(provider=cfg.provider, base_url=cfg.base_url, model=cfg.model)
        provider_cfg = cfg.cloud_provider(cloud_provider)
        if provider_cfg is None:
            raise AgenticError(
                f"cloud provider {cloud_provider!r} is not enabled",
                details={
                    "provider": cloud_provider,
                    "allow_cloud_providers": cfg.allow_cloud_providers,
                },
            )
        # base_url stays empty: each cloud SDK owns its own endpoint, and letting
        # config point a cloud client at an arbitrary host would turn a provider
        # toggle into an arbitrary-egress control.
        return cls(provider=cloud_provider, base_url="", model=provider_cfg.model, is_cloud=True)


def cloud_key_available(provider: str) -> bool:
    """Gate 5, as a pure predicate: is the provider's key present in the env?

    Mirrors ``llm/client.py``'s ``is_available`` -- key presence only, never a
    network probe -- so a status command can report readiness without egress.
    """
    env = CLOUD_KEY_ENVS.get(provider)
    return bool(env and (os.environ.get(env) or "").strip())


def _require_cloud_key(provider: str) -> str:
    env = CLOUD_KEY_ENVS[provider]
    # `or ""` then .strip() so a whitespace-only value counts as missing and a
    # padded one never reaches an auth header.
    key = (os.environ.get(env) or "").strip()
    if not key:
        # The env var's NAME, never its value -- an AgenticError's details reach
        # the audit log and, via ops_runner, a browser.
        raise AgenticError(f"{env} not set", details={"required_env": env, "provider": provider})
    return key


def build_chat_model(settings: DeepAgentModelSettings) -> object:
    """Return a tool-calling BaseChatModel for the resolved provider.

    Local providers never require a key. Cloud providers fail closed on a missing
    one and never place it anywhere it can be logged.
    """
    if not settings.is_cloud:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise AgenticError(
                "optional Deep Agents runtime dependencies are not installed",
                details={"extra": _LOCAL_EXTRA},
            ) from exc
        return ChatOpenAI(
            model=settings.model,
            base_url=settings.base_url,
            api_key=os.getenv("DEEPAGENT_API_KEY", "not-needed"),
        )

    key = _require_cloud_key(settings.provider)
    if settings.provider == "grok":
        try:
            # Separate extra from the local set: a local-only install must never
            # pull a cloud SDK it will not use.
            from langchain_xai import ChatXAI
        except ImportError as exc:
            raise AgenticError(
                "optional cloud provider dependencies are not installed",
                details={"extra": _CLOUD_EXTRA, "provider": settings.provider},
            ) from exc
        return ChatXAI(model=settings.model, api_key=key)

    try:
        # langchain-anthropic is a MANDATORY dependency of deepagents itself, not
        # an extra -- installing the local harness already brings it. Stated
        # plainly here because "Claude needs no new pin" is otherwise surprising.
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:
        raise AgenticError(
            "optional Deep Agents runtime dependencies are not installed",
            details={"extra": _LOCAL_EXTRA, "provider": settings.provider},
        ) from exc
    return ChatAnthropic(model=settings.model, api_key=key)


__all__ = ["DeepAgentModelSettings", "build_chat_model", "cloud_key_available"]
