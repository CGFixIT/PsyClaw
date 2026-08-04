"""Static regression guards for the container-isolation deploy contract.

Native seccomp, AppArmor, and eBPF validation still requires a Linux host; this
test prevents the repository from silently rewiring an untraced custom policy.
"""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
WRITABLE_ROOTS = (
    "/app/data",
    "/app/index",
    "/app/logs",
    "/app/checkpoints",
    "/app/.emb_cache",
    "/tmp",
)


def test_compose_forces_builtin_seccomp_and_stage_one_baseline() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["cyclaw"]

    assert service["ports"] == ["127.0.0.1:8787:8787"]
    assert service["user"] == "1000:1000"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true", "seccomp:builtin"]
    assert not list((REPO_ROOT / "deploy" / "seccomp").glob("*.json"))


def test_apparmor_candidate_matches_writable_carveouts() -> None:
    profile = (REPO_ROOT / "deploy" / "apparmor" / "cyclaw-gate").read_text(
        encoding="utf-8"
    )
    overlay = yaml.safe_load(
        (REPO_ROOT / "deploy" / "apparmor" / "docker-compose.apparmor.yml").read_text(
            encoding="utf-8"
        )
    )

    assert overlay["services"]["cyclaw"]["security_opt"] == [
        "no-new-privileges:true",
        "apparmor:cyclaw-gate",
    ]
    for root in WRITABLE_ROOTS:
        assert f"{root}/** rwkl," in profile
    assert "/** r," in profile
    assert not any(line.strip().startswith("/** w") for line in profile.splitlines())
    assert "abstractions/base" not in profile
    assert "network netlink raw," in profile
    assert "deny network inet raw," in profile
    assert "deny network inet6 raw," in profile
    assert "deny network raw," not in profile
    assert "deny network packet," in profile
    assert "deny network alg," in profile
    assert "\n  signal,\n" not in profile
    assert "deny /sys/firmware/** rwklx," in profile
    assert "deny /sys/devices/virtual/powercap/** rwklx," in profile
    assert "deny /sys/kernel/security/** rwklx," in profile


def test_falco_models_index_writes_and_optional_tool_egress() -> None:
    rules = yaml.safe_load(
        (REPO_ROOT / "deploy" / "falco" / "falco_rules.yaml").read_text(encoding="utf-8")
    )
    entries = {
        entry.get("macro") or entry.get("list") or entry.get("rule"): entry
        for entry in rules
    }

    assert '/app/index/' in entries["cyclaw_expected_write_dir"]["condition"]
    assert "git" in entries["cyclaw_expected_exes"]["items"]
    assert "git-remote-http" in entries["cyclaw_expected_exes"]["items"]
    assert "git-remote-https" in entries["cyclaw_optional_network_exes"]["items"]
    assert entries["CyClaw optional tool outbound connection"]["priority"] == "NOTICE"
