"""Exact IPv4 scope policy for the passive network connector."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network

from utils.errors import NetConnectConfigError

ALLOWED_PARENT_CIDRS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
)
_ALLOWED_PARENTS = tuple(IPv4Network(value) for value in ALLOWED_PARENT_CIDRS)


def normalize_allowed_cidrs(values: object) -> list[str]:
    """Validate and canonicalize explicit RFC1918/loopback IPv4 networks."""
    if not isinstance(values, list):
        raise NetConnectConfigError("netconnect.allowed_cidrs must be a list of CIDR strings")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str) or not raw.strip():
            raise NetConnectConfigError(
                "netconnect.allowed_cidrs entries must be non-empty strings",
                details={"received": repr(raw)},
            )
        try:
            network = ip_network(raw.strip(), strict=False)
        except ValueError as exc:
            raise NetConnectConfigError(
                f"invalid netconnect CIDR: {raw!r}",
                details={"cidr": raw},
            ) from exc
        if not isinstance(network, IPv4Network):
            raise NetConnectConfigError(
                "netconnect v0.1 accepts IPv4 CIDRs only",
                details={"cidr": raw},
            )
        if not any(network.subnet_of(parent) for parent in _ALLOWED_PARENTS):
            raise NetConnectConfigError(
                "netconnect CIDRs must stay inside RFC1918 or IPv4 loopback space",
                details={"cidr": raw, "allowed_parents": list(ALLOWED_PARENT_CIDRS)},
            )
        canonical = network.with_prefixlen
        if canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
    return normalized


@dataclass(frozen=True)
class ScopePolicy:
    """Pre-parsed network scope used to filter every returned address."""

    networks: tuple[IPv4Network, ...]

    @classmethod
    def from_cidrs(cls, cidrs: object) -> ScopePolicy:
        normalized = normalize_allowed_cidrs(cidrs)
        return cls(tuple(IPv4Network(value) for value in normalized))

    def contains(self, value: str) -> bool:
        try:
            address = ip_address(value)
        except ValueError:
            return False
        return isinstance(address, IPv4Address) and any(
            address in network for network in self.networks
        )


__all__ = ["ALLOWED_PARENT_CIDRS", "ScopePolicy", "normalize_allowed_cidrs"]
