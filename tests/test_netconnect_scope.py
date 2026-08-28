"""Pure scope and passive neighbor-cache parser tests."""

from __future__ import annotations

import pytest

from agentic.netconnect.client import parse_neighbor_cache
from agentic.netconnect.scope import ScopePolicy, normalize_allowed_cidrs


@pytest.mark.parametrize("cidr", [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "10.20.30.0/24",
])
def test_exact_allowed_parent_ranges(cidr):
    assert normalize_allowed_cidrs([cidr]) == [cidr]


def test_scope_policy_filters_every_address():
    policy = ScopePolicy.from_cidrs(["192.168.4.0/24", "127.0.0.0/8"])
    assert policy.contains("192.168.4.10")
    assert policy.contains("127.0.0.1")
    assert not policy.contains("192.168.5.10")
    assert not policy.contains("8.8.8.8")
    assert not policy.contains("not-an-ip")


def test_windows_arp_parser():
    text = """Interface: 192.168.1.50 --- 0x6
  Internet Address      Physical Address      Type
  192.168.1.1           aa-bb-cc-dd-ee-ff     dynamic
  224.0.0.22            01-00-5e-00-00-16     static
"""
    records = parse_neighbor_cache(text, "Windows")
    assert records[0] == {
        "ip": "192.168.1.1",
        "mac": "aa:bb:cc:dd:ee:ff",
        "interface": "192.168.1.50",
        "state": "dynamic",
    }


def test_linux_neighbor_parser_handles_missing_mac():
    text = """192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE
192.168.1.99 dev eth0 FAILED
fe80::1 dev eth0 lladdr aa:bb:cc:dd:ee:00 STALE
"""
    records = parse_neighbor_cache(text, "Linux")
    assert [item["ip"] for item in records] == ["192.168.1.1", "192.168.1.99"]
    assert records[1]["mac"] is None
    assert records[1]["state"] == "failed"


def test_darwin_arp_parser():
    text = "? (10.0.0.1) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n"
    assert parse_neighbor_cache(text, "Darwin") == [{
        "ip": "10.0.0.1",
        "mac": "aa:bb:cc:dd:ee:ff",
        "interface": "en0",
        "state": "cached",
    }]
