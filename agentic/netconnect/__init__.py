"""Passive, scope-gated LAN inventory connector.

The connector is disabled by default and remains strictly out-of-band. It can
describe the local host and read the operating system's existing neighbor
cache; it never sends probes, sweeps a subnet, or imports the request path.
"""

from agentic.netconnect.config import NetConnectConfig, load_netconnect_config
from utils.errors import (
    NetCommandNotInstalledError,
    NetConnectConfigError,
    NetConnectError,
    NetConnectRuntimeError,
)

__all__ = [
    "NetConnectConfig",
    "load_netconnect_config",
    "NetConnectError",
    "NetConnectConfigError",
    "NetConnectRuntimeError",
    "NetCommandNotInstalledError",
]

__version__ = "0.1.0"
