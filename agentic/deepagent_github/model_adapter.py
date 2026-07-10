"""Model settings for the optional Deep Agents GitHub harness."""

from __future__ import annotations

from dataclasses import dataclass

from agentic.config import DeepAgentGitHubConfig


@dataclass(frozen=True)
class DeepAgentModelSettings:
    """OpenAI-compatible local model settings passed to a future builder."""

    provider: str
    base_url: str
    model: str

    @classmethod
    def from_config(cls, cfg: DeepAgentGitHubConfig) -> DeepAgentModelSettings:
        return cls(provider=cfg.provider, base_url=cfg.base_url, model=cfg.model)
