"""Configuration loader for the disabled-by-default passive LAN connector."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from agentic.netconnect.scope import normalize_allowed_cidrs
from utils.errors import NetConnectConfigError
from utils.logger import _get_config

DEFAULT_ALLOWED_NET_OPS = ("self", "arp")
VALID_NET_OPS = frozenset(DEFAULT_ALLOWED_NET_OPS)
DEFAULT_COMMAND_TIMEOUT_SEC = 5.0
MAX_COMMAND_TIMEOUT_SEC = 30.0
DEFAULT_MAX_NEIGHBORS = 512
MAX_NEIGHBORS = 4096


@dataclass
class NetConnectConfig:
    """Parsed and validated ``netconnect:`` block from config.yaml."""

    enabled: bool = False
    allowed_cidrs: list[str] = field(default_factory=list)
    allowed_net_ops: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_NET_OPS))
    command_timeout_sec: float = DEFAULT_COMMAND_TIMEOUT_SEC
    max_neighbors: int = DEFAULT_MAX_NEIGHBORS

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise NetConnectConfigError("netconnect.enabled must be a boolean true/false")
        self.allowed_cidrs = normalize_allowed_cidrs(self.allowed_cidrs)
        if self.enabled and not self.allowed_cidrs:
            raise NetConnectConfigError(
                "netconnect.allowed_cidrs must contain at least one explicit CIDR when enabled"
            )
        if not isinstance(self.allowed_net_ops, list) or not all(
            isinstance(op, str) for op in self.allowed_net_ops
        ):
            raise NetConnectConfigError("netconnect.allowed_net_ops must be a list of strings")
        bad = [op for op in self.allowed_net_ops if op not in VALID_NET_OPS]
        if bad:
            raise NetConnectConfigError(
                f"netconnect.allowed_net_ops contains unknown ops: {bad!r}",
                details={"unknown": bad, "valid": sorted(VALID_NET_OPS)},
            )
        if (
            not isinstance(self.command_timeout_sec, (int, float))
            or isinstance(self.command_timeout_sec, bool)
            or not 0 < float(self.command_timeout_sec) <= MAX_COMMAND_TIMEOUT_SEC
        ):
            raise NetConnectConfigError(
                f"netconnect.command_timeout_sec must be in (0, {MAX_COMMAND_TIMEOUT_SEC:g}]"
            )
        self.command_timeout_sec = float(self.command_timeout_sec)
        if (
            not isinstance(self.max_neighbors, int)
            or isinstance(self.max_neighbors, bool)
            or not 0 < self.max_neighbors <= MAX_NEIGHBORS
        ):
            raise NetConnectConfigError(
                f"netconnect.max_neighbors must be an integer in [1, {MAX_NEIGHBORS}]"
            )

    def to_dict(self) -> dict:
        return asdict(self)


def load_netconnect_config(config_path: str = "config.yaml") -> NetConnectConfig:
    """Read and validate the ``netconnect:`` configuration block."""
    cfg = _get_config(config_path) or {}
    block = cfg.get("netconnect")
    if block is None:
        raise NetConnectConfigError(
            "netconnect: block missing from config.yaml",
            details={"hint": "Add the default-off netconnect block from config.yaml."},
        )
    if not isinstance(block, dict):
        raise NetConnectConfigError(
            f"netconnect: block must be a mapping, got {type(block).__name__}"
        )
    known = set(NetConnectConfig.__dataclass_fields__)
    unknown = set(block) - known
    kwargs = {key: value for key, value in block.items() if key in known}
    try:
        net_cfg = NetConnectConfig(**kwargs)
    except TypeError as exc:
        raise NetConnectConfigError(
            f"netconnect: block invalid: {exc}",
            details={"unknown_keys": sorted(unknown)},
        ) from exc
    net_cfg._unknown_keys = sorted(unknown)  # type: ignore[attr-defined]
    return net_cfg


__all__ = ["NetConnectConfig", "load_netconnect_config"]
