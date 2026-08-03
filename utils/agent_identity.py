"""Agent git identity — single source for agentic committer + branch namespace.

Why this module exists
----------------------
CyClaw's agentic write path (repo_workspace commits, writer PR heads, harness
console branch validation) used to hardcode ``Claude`` /
``noreply@anthropic.com`` / ``claude/``. That misattributes work when the
driver is a local Qwen model, Grok, Kimi, Codex, or any non-Claude-Code agent.

Defaults here are intentionally **driver-agnostic**. Operators can still
override per session via environment variables:

* ``CYCLAW_AGENT_COMMIT_NAME``
* ``CYCLAW_AGENT_COMMIT_EMAIL``
* ``CYCLAW_AGENT_BRANCH_PREFIX``

Load-bearing constraints
------------------------
* Branch prefix must be alphanumeric-led so ``git checkout -b <name>`` and
  ``gh pr create --head`` cannot reparse the name as a flag (no leading ``-``).
* Lives under ``utils/`` so both ``agentic/`` and ``harness/`` can import it
  without violating I6 (harness must not import agentic).
* Values resolve at import time. Tests that override env must
  ``importlib.reload`` this module first, then any consumer that re-bound the
  symbols at its own import.
"""

from __future__ import annotations

import os
import re

# Driver-agnostic defaults — not Claude/Anthropic, not any single vendor.
DEFAULT_COMMIT_NAME = "CyClaw Agent"
DEFAULT_COMMIT_EMAIL = "cyclaw-agent@users.noreply.github.com"
DEFAULT_BRANCH_PREFIX = "agent"

_PREFIX_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")


def _load_prefix() -> str:
    raw = os.environ.get("CYCLAW_AGENT_BRANCH_PREFIX", DEFAULT_BRANCH_PREFIX)
    if not _PREFIX_SHAPE.fullmatch(raw):
        raise ValueError(
            "CYCLAW_AGENT_BRANCH_PREFIX must be 1-32 chars from [A-Za-z0-9._-] "
            f"and start alphanumeric, got {raw!r}"
        )
    return raw


COMMIT_NAME: str = os.environ.get("CYCLAW_AGENT_COMMIT_NAME", DEFAULT_COMMIT_NAME)
COMMIT_EMAIL: str = os.environ.get("CYCLAW_AGENT_COMMIT_EMAIL", DEFAULT_COMMIT_EMAIL)
BRANCH_PREFIX: str = _load_prefix()
BRANCH_NAME_RE: re.Pattern[str] = re.compile(
    rf"^{re.escape(BRANCH_PREFIX)}/[A-Za-z0-9][A-Za-z0-9._/-]{{0,79}}$"
)
