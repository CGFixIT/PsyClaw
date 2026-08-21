"""Static regression guards for the container-isolation deploy contract.

Native seccomp, AppArmor, and eBPF validation still requires a Linux host; this
test prevents the repository from silently rewiring an untraced custom policy.
"""

import re
import subprocess  # noqa: S404 - executes repo-owned healthcheck payloads only
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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


class _HealthHandler(BaseHTTPRequestHandler):
    status_code = 200

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self.send_response(self.status_code)
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        return


def test_dockerfile_refreshes_ca_certificates_before_nonroot_user() -> None:
    """DLA-4726-1 / issue #1024: digest-pinned slim-bookworm ships a stale
    Mozilla CA bundle. The one-package refresh must run as root (before
    USER cyclaw), must not apt-get upgrade, and must not float the FROM digest.
    """
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    pin = "ca-certificates=20250419~deb12u1"
    assert pin in dockerfile
    assert f"'{pin}'" in dockerfile
    ca_at = dockerfile.index(pin)
    user_at = dockerfile.index("\nUSER cyclaw")
    assert ca_at < user_at, "ca-certificates refresh must run as root before USER cyclaw"
    for line in dockerfile.splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert "apt-get upgrade" not in line
    from_lines = [
        line.strip()
        for line in dockerfile.splitlines()
        if line.startswith("FROM python:3.12-slim-bookworm@sha256:")
    ]
    assert len(from_lines) == 2
    builder, runtime = from_lines
    assert builder.split(" AS ")[0] == runtime


def test_compose_forces_builtin_seccomp_and_stage_one_baseline() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["cyclaw"]

    assert service["ports"] == ["127.0.0.1:8787:8787"]
    assert service["user"] == "1000:1000"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true", "seccomp:builtin"]
    assert not list((REPO_ROOT / "deploy" / "seccomp").glob("*.json"))


def test_container_healthchecks_reject_http_error_statuses() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    compose_probe = compose["services"]["cyclaw"]["healthcheck"]["test"]

    docker_match = re.search(r'^\s*CMD python -c "([^"]+)" \|\| exit 1$', dockerfile, re.MULTILINE)
    assert docker_match, "Dockerfile healthcheck Python payload not found"
    assert compose_probe[:3] == ["CMD", "python", "-c"]
    probes = (docker_match.group(1), compose_probe[3])

    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    probe_url = f"http://127.0.0.1:{server.server_address[1]}/health"
    try:
        for status_code, expected_success in ((200, True), (500, False)):
            _HealthHandler.status_code = status_code
            for probe in probes:
                local_probe = probe.replace("http://127.0.0.1:8787/health", probe_url)
                result = subprocess.run(  # noqa: S603 - fixed interpreter and repo-owned payload
                    [sys.executable, "-c", local_probe],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert (result.returncode == 0) is expected_success, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
