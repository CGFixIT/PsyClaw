"""Passive local-host and neighbor-cache inventory for explicitly scoped LANs."""

from __future__ import annotations

import os
import platform
import re
import socket
import subprocess
from ipaddress import ip_address
from pathlib import Path

from agentic.netconnect.config import NetConnectConfig
from agentic.netconnect.scope import ScopePolicy
from utils.errors import (
    NetCommandNotInstalledError,
    NetConnectError,
    NetConnectRuntimeError,
)
from utils.logger import audit_log, hash_query

_MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5}$")
_WINDOWS_INTERFACE_RE = re.compile(r"^\s*Interface:\s+(\d+(?:\.\d+){3})\b", re.IGNORECASE)
_WINDOWS_ENTRY_RE = re.compile(
    r"^\s*(\d+(?:\.\d+){3})\s+([0-9a-fA-F:-]{17}|[0-9a-fA-F]{12})\s+(\S+)"
)
_DARWIN_ENTRY_RE = re.compile(
    r"^\S+\s+\(([^)]+)\)\s+at\s+(\S+)\s+on\s+(\S+)",
    re.IGNORECASE,
)
_LINUX_STATES = frozenset(
    {"INCOMPLETE", "REACHABLE", "STALE", "DELAY", "PROBE", "FAILED", "NOARP", "PERMANENT"}
)


def _normalize_mac(raw: str) -> str | None:
    value = raw.strip().lower()
    if value in {"(incomplete)", "incomplete", "<incomplete>"}:
        return None
    if re.fullmatch(r"[0-9a-f]{12}", value):
        return ":".join(value[index:index + 2] for index in range(0, 12, 2))
    if not _MAC_RE.fullmatch(value):
        return None
    return value.replace("-", ":")


def _record(ip: str, mac: str | None, interface: str, state: str) -> dict | None:
    try:
        parsed = ip_address(ip)
    except ValueError:
        return None
    if parsed.version != 4:
        return None
    return {
        "ip": str(parsed),
        "mac": _normalize_mac(mac) if mac else None,
        "interface": interface,
        "state": state.lower(),
    }


def parse_neighbor_cache(text: str, system_name: str) -> list[dict]:
    """Normalize Windows ``arp -a``, Linux ``ip neigh``, or Darwin ``arp -an``."""
    records: list[dict] = []
    system_key = system_name.lower()
    if system_key.startswith("win"):
        interface = ""
        for line in text.splitlines():
            header = _WINDOWS_INTERFACE_RE.match(line)
            if header:
                interface = header.group(1)
                continue
            match = _WINDOWS_ENTRY_RE.match(line)
            if match:
                item = _record(match.group(1), match.group(2), interface, match.group(3))
                if item:
                    records.append(item)
    elif system_key == "linux":
        for line in text.splitlines():
            parts = line.split()
            if not parts:
                continue
            interface = parts[parts.index("dev") + 1] if "dev" in parts and parts.index("dev") + 1 < len(parts) else ""
            mac = (
                parts[parts.index("lladdr") + 1]
                if "lladdr" in parts and parts.index("lladdr") + 1 < len(parts)
                else None
            )
            state = next((part for part in reversed(parts) if part.upper() in _LINUX_STATES), "unknown")
            item = _record(parts[0], mac, interface, state)
            if item:
                records.append(item)
    elif system_key == "darwin":
        for line in text.splitlines():
            match = _DARWIN_ENTRY_RE.match(line)
            if match:
                item = _record(match.group(1), match.group(2), match.group(3), "cached")
                if item:
                    records.append(item)
    else:
        raise NetConnectRuntimeError(
            f"netconnect is unsupported on platform {system_name!r}",
            details={"platform": system_name},
        )

    unique: dict[tuple[str, str, str | None], dict] = {}
    for item in records:
        key = (item["ip"], item["interface"], item["mac"])
        unique.setdefault(key, item)
    return list(unique.values())


def _neighbor_command(system_name: str) -> list[str]:
    """Return a fixed absolute passive-cache command for the host platform."""
    system_key = system_name.lower()
    if system_key.startswith("win"):
        root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        candidates = (root / "System32" / "arp.exe",)
        args = ("-a",)
    elif system_key == "linux":
        candidates = tuple(Path(value) for value in ("/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip", "/bin/ip"))
        args = ("neigh", "show")
    elif system_key == "darwin":
        candidates = (Path("/usr/sbin/arp"),)
        args = ("-an",)
    else:
        raise NetConnectRuntimeError(
            f"netconnect is unsupported on platform {system_name!r}",
            details={"platform": system_name},
        )
    for candidate in candidates:
        if candidate.is_absolute() and candidate.is_file():
            return [str(candidate), *args]
    raise NetCommandNotInstalledError(
        f"passive neighbor-cache command not found for {system_name}",
        details={"platform": system_name},
    )


def _scope_interface(raw: str, scope: ScopePolicy) -> str:
    """Keep named NICs; redact out-of-scope IPv4 interface addresses."""
    if not raw:
        return ""
    try:
        parsed = ip_address(raw)
    except ValueError:
        return raw
    if parsed.version != 4 or not scope.contains(str(parsed)):
        return ""
    return str(parsed)


class NetClient:
    """Execute the two passive operations allowed by netconnect v0.1."""

    def __init__(self, cfg: dict, net_cfg: NetConnectConfig, config_path: str = "config.yaml"):
        self.cfg = cfg
        self.net_cfg = net_cfg
        self.config_path = config_path
        self.scope = ScopePolicy.from_cidrs(net_cfg.allowed_cidrs)

    def _guard_op(self, op: str) -> None:
        if op not in self.net_cfg.allowed_net_ops:
            raise NetConnectError(
                f"netconnect operation is not allowed: {op}",
                code="NETCONNECT_OP_NOT_ALLOWED",
                details={"op": op},
            )

    def _audit(self, op: str, **counts: object) -> None:
        audit_log(
            {
                "event": "netconnect_scan",
                "op": op,
                "scope_hashes": [hash_query(cidr) for cidr in self.net_cfg.allowed_cidrs],
                **counts,
            },
            self.config_path,
            cfg=self.cfg,
        )

    def self_inventory(self) -> dict:
        """Return best-effort local identity without claiming route completeness."""
        self._guard_op("self")
        hostname = socket.gethostname()
        errors: list[str] = []
        try:
            interfaces = [
                {"index": index, "name": name}
                for index, name in sorted(socket.if_nameindex(), key=lambda item: item[0])
            ]
        except OSError as exc:
            interfaces = []
            errors.append(f"interface enumeration failed: {exc}")

        self._audit(
            "self",
            interface_count=len(interfaces),
            address_count=0,
            error_count=len(errors),
        )
        return {
            "op": "self",
            "hostname": hostname,
            "interfaces": interfaces,
            "addresses": [],
            "complete": False,
            "active_probe": False,
            "limitations": (
                "stdlib hostname/interface names only; addresses and routes are not enumerated; "
                "not a reachability scan"
            ),
            "errors": errors,
        }

    def arp_neighbors(self) -> dict:
        """Read and scope-filter the operating system's existing neighbor cache."""
        self._guard_op("arp")
        system_name = platform.system()
        argv = _neighbor_command(system_name)
        try:
            result = subprocess.run(  # noqa: S603 -- fixed absolute argv, never caller input
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.net_cfg.command_timeout_sec,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise NetConnectRuntimeError(
                "passive neighbor-cache command timed out",
                details={"timeout_sec": self.net_cfg.command_timeout_sec},
            ) from exc
        except OSError as exc:
            raise NetCommandNotInstalledError(
                f"could not execute passive neighbor-cache command: {exc}",
                details={"platform": system_name},
            ) from exc
        if result.returncode != 0:
            raise NetConnectRuntimeError(
                "passive neighbor-cache command failed",
                details={"returncode": result.returncode, "platform": system_name},
            )

        observed = parse_neighbor_cache(result.stdout, system_name)
        in_scope = []
        for item in observed:
            if not self.scope.contains(item["ip"]):
                continue
            filtered = dict(item)
            filtered["interface"] = _scope_interface(item["interface"], self.scope)
            in_scope.append(filtered)
        in_scope.sort(
            key=lambda item: (
                int(ip_address(item["ip"])),
                item["interface"],
                item["mac"] or "",
            )
        )
        total_in_scope = len(in_scope)
        neighbors = in_scope[:self.net_cfg.max_neighbors]
        truncated = total_in_scope > len(neighbors)
        self._audit(
            "arp",
            platform=system_name,
            observed_count=len(observed),
            in_scope_count=total_in_scope,
            result_count=len(neighbors),
            truncated=truncated,
        )
        return {
            "op": "arp",
            "platform": system_name,
            "source": "existing_neighbor_cache",
            "active_probe": False,
            "observed_count": len(observed),
            "in_scope_count": total_in_scope,
            "truncated": truncated,
            "neighbors": neighbors,
        }


__all__ = ["NetClient", "parse_neighbor_cache"]
