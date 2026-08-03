"""Static regression guards for the optional Falco detection profile.

Runtime eBPF load requires a Linux host + privileged Docker; these tests only
lock the compose command shape and the outbound macro pairing so regressions
are caught without a kernel. Operator steps: deploy/falco/README.md.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES = "/etc/falco/falco_rules.yaml"
CYCLAW_RULES = "/etc/falco/rules.d/cyclaw_rules.yaml"


def test_compose_loads_default_rules_before_cyclaw_rules() -> None:
    """CyClaw's rules depend on macros declared by Falco's default ruleset.

    Any -r flag disables falco.yaml rules_files, so defaults must be passed
    first, then the CyClaw overlay (order is load-bearing).
    """
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert compose["services"]["falco"]["command"] == [
        "/usr/bin/falco",
        "--modern-bpf",
        "-r",
        DEFAULT_RULES,
        "-r",
        CYCLAW_RULES,
    ]


def test_ollama_egress_allowance_pairs_destination_and_port() -> None:
    """Port 11434 alone must not suppress alerts to arbitrary external hosts."""
    rules = yaml.safe_load(
        (REPO_ROOT / "deploy" / "falco" / "falco_rules.yaml").read_text(encoding="utf-8")
    )
    outbound = next(
        entry for entry in rules if entry.get("macro") == "cyclaw_expected_outbound"
    )
    condition = " ".join(outbound["condition"].split())

    assert 'fd.sip = "127.0.0.1"' in condition
    assert 'fd.sip = "::1"' in condition
    assert "fd.sport = 11434" in condition
    assert re.search(r"\)\s+and\s+fd\.sport\s*=\s*11434", condition)
    assert not re.search(r"\bor\s+fd\.sport\s*=\s*11434", condition)
