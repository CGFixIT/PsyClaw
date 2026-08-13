"""CyClaw filesystem connector -- out-of-band, opt-in, human-governed.

Local filesystem / SMB-share access for the air-gapped ICP. Read ops are scoped to
allow-listed roots; writes are confined to a SEPARATE ``writable_roots`` list, gated
(``writes_enabled`` + human reason + confirm), and disabled by default. The connector
is content-agnostic -- it never calls the LLM -- and runs strictly via
``python -m agentic.fsconnect.cli``. It is NEVER imported by gate.py, graph.py, or
mcp_hybrid_server.py; that isolation preserves CyClaw's five security invariants.

Public API:
    from agentic.fsconnect import FsConnectConfig, load_fsconnect_config
"""

from agentic.fsconnect.config import FsConnectConfig, load_fsconnect_config
from utils.errors import (
    FsConnectConfigError,
    FsConnectError,
    FsConnectRuntimeError,
    FsMacOSPermissionError,
    FsPathError,
    FsWriteRefused,
)

__all__ = [
    "FsConnectConfig",
    "load_fsconnect_config",
    "FsConnectError",
    "FsConnectConfigError",
    "FsPathError",
    "FsMacOSPermissionError",
    "FsWriteRefused",
    "FsConnectRuntimeError",
]

__version__ = "0.1.0"
