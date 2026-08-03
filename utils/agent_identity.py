"""Agent git identity — single source for agentic committer + branch namespace.

Why this module exists
----------------------
CyClaw's agentic write path (repo_workspace commits, writer PR heads, harness
console branch validation) used to hardcode ``Claude`` /
``noreply@anthropic.com`` / ``claude/``. That misattributes work when the
driver is a local Qwen model, Grok, Kimi, Codex, or any non-Claude-Code agent.

Defaults here are intentionally **driver-agnostic** for *committer* identity.
Branch *validation* accepts every vendor prefix listed in
``.github/PULL_REQUEST_TEMPLATE.md`` (plus a generic ``agent/``), so each tool
can keep its own convention without reconfiguring the process.

Environment overrides
---------------------
* ``CYCLAW_AGENT_COMMIT_NAME`` / ``CYCLAW_AGENT_COMMIT_EMAIL`` — committer
* ``CYCLAW_AGENT_BRANCH_PREFIX`` — *preferred* prefix (also always allowed);
  does **not** revoke the template allowlist. Use this when a tool needs a
  default namespace, not to exclusive-lock validation to one vendor.

Load-bearing constraints
------------------------
* Every allowed prefix must be alphanumeric-led so ``git checkout -b <name>``
  and ``gh pr create --head`` cannot reparse the name as a flag (no leading
  ``-``).
* Lives under ``utils/`` so both ``agentic/`` and ``harness/`` can import it
  without violating I6 (harness must not import agentic).
* Values resolve at import time. Tests that override env must
  ``importlib.reload`` this module first, then any consumer that re-bound the
  symbols at its own import.
"""

from __future__ import annotations

import os
import re

# Driver-agnostic committer defaults — not Claude/Anthropic, not any single vendor.
DEFAULT_COMMIT_NAME = "CyClaw Agent"
DEFAULT_COMMIT_EMAIL = "cyclaw-agent@users.noreply.github.com"
# Preferred prefix when a caller needs a default without a vendor context.
DEFAULT_BRANCH_PREFIX = "agent"

# From .github/PULL_REQUEST_TEMPLATE.md (plus lowercase CyClaw + generic agent).
# Validation accepts ANY of these; agents pick the one that matches the driver.
TEMPLATE_BRANCH_PREFIXES: frozenset[str] = frozenset(
    {
        "claude",  # Claude Code → claude/{feature}
        "codex",  # Codex → codex/{feature}
        "grok",  # Grok Build → grok/{feature}
        "kimi",  # Kimi / Kimi Code → kimi/{feature}
        "CyClaw",  # CyClaw direct/MCP → CyClaw/{feature}-{date}
        "cyclaw",  # lowercase form of the same convention
        "agent",  # driver-agnostic / unknown driver
    }
)

_PREFIX_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
_TOPIC = r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}"


def _validate_prefix(raw: str, *, env_name: str) -> str:
    if not _PREFIX_SHAPE.fullmatch(raw):
        raise ValueError(
            f"{env_name} must be 1-32 chars from [A-Za-z0-9._-] "
            f"and start alphanumeric, got {raw!r}"
        )
    return raw


def _load_preferred_prefix() -> str:
    raw = os.environ.get("CYCLAW_AGENT_BRANCH_PREFIX", DEFAULT_BRANCH_PREFIX)
    return _validate_prefix(raw, env_name="CYCLAW_AGENT_BRANCH_PREFIX")


def _compile_branch_name_re(prefixes: frozenset[str]) -> re.Pattern[str]:
    # Longer prefixes first so alternation is unambiguous if one is a prefix of another.
    ordered = sorted(prefixes, key=lambda p: (-len(p), p))
    alt = "|".join(re.escape(p) for p in ordered)
    return re.compile(rf"^({alt})/{_TOPIC}$")


COMMIT_NAME: str = os.environ.get("CYCLAW_AGENT_COMMIT_NAME", DEFAULT_COMMIT_NAME)
COMMIT_EMAIL: str = os.environ.get("CYCLAW_AGENT_COMMIT_EMAIL", DEFAULT_COMMIT_EMAIL)
# Preferred default for *new* branches when the caller has no vendor context.
BRANCH_PREFIX: str = _load_preferred_prefix()
# Always accept the template set; preferred env value is unioned in so a custom
# future vendor prefix still works without code edits.
ALLOWED_BRANCH_PREFIXES: frozenset[str] = frozenset(TEMPLATE_BRANCH_PREFIXES | {BRANCH_PREFIX})
BRANCH_NAME_RE: re.Pattern[str] = _compile_branch_name_re(ALLOWED_BRANCH_PREFIXES)


def allowed_prefixes_help() -> str:
    """Human-readable list for error messages (e.g. ``claude/, codex/, grok/``)."""
    return ", ".join(f"{p}/" for p in sorted(ALLOWED_BRANCH_PREFIXES, key=str.lower))
