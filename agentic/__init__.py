"""CyClaw agentic layer -- out-of-band, opt-in, human-governed.

Two capabilities, both strictly out-of-band (run as ``python -m agentic.cli``,
NEVER imported by gate.py, graph.py, or mcp_hybrid_server.py):

  1. Read-only GitHub context via the ``gh`` CLI (argv-list, no shell, audited).
  2. A governed skills registry that reuses the soul propose/apply pattern
     (injection scan + human reason + atomic write + sha256 versioning).

Writes are present only as a DISABLED, STUBBED scaffold (agentic/writer.py): the
gate is the out-of-band analogue of CyClaw's triple-gate, and v0.1 never executes
a write.

Public API:
    from agentic import AgenticConfig, load_agentic_config, SkillRegistry

Usage from the CLI:
    python -m agentic.cli status
    python -m agentic.cli context --pr 123
    python -m agentic.cli propose-skill --name x --desc y --body z --reason r
    python -m agentic.cli test
"""

# This process never imports gate.py, so without this it inherits whatever
# telemetry env the operator's shell/container/observability agent happens to
# carry. Nothing here reaches deepagent_github's langchain-based imports today
# (no CLI subcommand wires it up -- see agentic/deepagent_github/builder.py),
# but applying the kill at package-import time means it is already in place
# the moment a future caller does. Same rationale as mcp_hybrid_server.py.
from utils.telemetry_kill import apply_telemetry_kill

apply_telemetry_kill()

from agentic.config import AgenticConfig, load_agentic_config
from agentic.registry import SkillRegistry
from utils.errors import (
    AgenticConfigError,
    AgenticError,
    AgenticWriteRefused,
    GhNotInstalledError,
    GhVersionError,
    SkillRegistryError,
)

__all__ = [
    "AgenticConfig",
    "load_agentic_config",
    "SkillRegistry",
    "AgenticError",
    "AgenticConfigError",
    "AgenticWriteRefused",
    "GhNotInstalledError",
    "GhVersionError",
    "SkillRegistryError",
]

__version__ = "0.1.0"
