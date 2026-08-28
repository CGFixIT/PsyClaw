"""Offline pre-flight checks for the passive network connector."""

from __future__ import annotations

from agentic.netconnect.client import parse_neighbor_cache
from agentic.netconnect.config import load_netconnect_config
from agentic.netconnect.scope import ScopePolicy, normalize_allowed_cidrs
from utils.errors import NetConnectConfigError
from utils.selftest import fail, finalize, ok, skip


def run_self_test(config_path: str = "config.yaml") -> tuple[int, int, list[str]]:
    results: list[tuple[bool, str]] = []
    try:
        net_cfg = load_netconnect_config(config_path)
        results.append(ok("01. netconnect config loads and validates"))
    except NetConnectConfigError as exc:
        results.append(fail("01. netconnect config loads and validates", exc.message))
        for number in range(2, 5):
            results.append(skip(f"{number:02d}. (skipped -- no config)", "config invalid"))
        return finalize(results)

    try:
        normalize_allowed_cidrs(["8.8.8.0/24"])
        results.append(fail("02. public CIDR refusal", "public scope was accepted"))
    except NetConnectConfigError:
        results.append(ok("02. public CIDR is refused"))

    if net_cfg.allowed_cidrs:
        policy = ScopePolicy.from_cidrs(net_cfg.allowed_cidrs)
        sample = str(policy.networks[0].network_address)
        results.append(ok("03. explicit scope policy") if policy.contains(sample)
                       else fail("03. explicit scope policy", "configured network not accepted"))
    else:
        results.append(skip("03. explicit scope policy", "connector disabled; no CIDRs configured"))

    samples = {
        "Windows": "Interface: 192.168.1.2 --- 0x6\n  192.168.1.1 aa-bb-cc-dd-ee-ff dynamic",
        "Linux": "192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE",
        "Darwin": "? (192.168.1.1) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]",
    }
    parsed = [parse_neighbor_cache(text, system) for system, text in samples.items()]
    if all(items and items[0]["ip"] == "192.168.1.1" for items in parsed):
        results.append(ok("04. passive cache parsers normalize all supported platforms"))
    else:
        results.append(fail("04. passive cache parsers", "fixture normalization failed"))
    return finalize(results)


if __name__ == "__main__":
    passed, total, output = run_self_test()
    for line in output:
        print(line)
    print(f"\n{passed}/{total} passed")
    raise SystemExit(0 if passed == total else 1)
