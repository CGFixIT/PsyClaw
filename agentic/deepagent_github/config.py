"""Config access helpers for the optional Deep Agents GitHub harness."""

from __future__ import annotations

from agentic.config import AgenticConfig, DeepAgentGitHubConfig, load_agentic_config


def load_deepagent_github_config(config_path: str = "config.yaml") -> tuple[AgenticConfig, DeepAgentGitHubConfig]:
    """Load the top-level agentic config and return its deepagent_github block."""

    cfg = load_agentic_config(config_path)
    return cfg, cfg.deepagent_github
