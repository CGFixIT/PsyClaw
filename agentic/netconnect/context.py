"""Thin operation bundler over :class:`agentic.netconnect.client.NetClient`."""

from __future__ import annotations

from agentic.netconnect.client import NetClient
from agentic.netconnect.config import NetConnectConfig
from utils.errors import NetConnectError


def run_op(
    cfg: dict,
    net_cfg: NetConnectConfig,
    op: str,
    *,
    config_path: str = "config.yaml",
) -> dict:
    client = NetClient(cfg, net_cfg, config_path=config_path)
    if op == "self":
        return client.self_inventory()
    if op == "arp":
        return client.arp_neighbors()
    raise NetConnectError(
        f"unknown netconnect op: {op!r}",
        code="NETCONNECT_OP_NOT_ALLOWED",
        details={"op": op},
    )


__all__ = ["run_op"]
