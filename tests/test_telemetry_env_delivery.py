"""Process-boundary delivery tests for the canonical telemetry-safe env.

test_telemetry_kill.py proves the IMPORT-TIME half (each entry point applies
the block before heavy imports). This file proves the DELIVERY half from
issue #1135: the canonical environment exists before a child interpreter or
external tool initializes, at every boundary that builds an env by hand --
the agentic verifier, the gh spawn sites, the sync indexer child, the
Dockerfile/compose images, the launchers, and every generated launchd plist /
Windows task / cron line. The sitecustomize probe is the strongest form: a
temporary sitecustomize.py snapshots os.environ at INTERPRETER STARTUP,
before any user code (or any import-time kill) could run, so what it records
is exactly what the delivery mechanism itself provided. No network anywhere.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from utils.telemetry_kill import (
    SCRUBBED_ENV_KEYS,
    TELEMETRY_KILL,
    UPDATE_CHECK_OPT_OUT,
    build_telemetry_safe_env,
    scheduler_env_overlay,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

_CANONICAL = {**TELEMETRY_KILL, **UPDATE_CHECK_OPT_OUT}


# ---------------------------------------------------------------------------
# sitecustomize probe -- values present at interpreter startup
# ---------------------------------------------------------------------------


def _probe_at_interpreter_startup(env: dict[str, str], tmp_path: Path) -> dict[str, str | None]:
    """Launch ``python -c pass`` with *env*; a sitecustomize.py on PYTHONPATH
    dumps the canonical names before any user code executes."""
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir(exist_ok=True)
    out_file = tmp_path / "snapshot.json"
    names = sorted(set(_CANONICAL) | set(SCRUBBED_ENV_KEYS))
    (probe_dir / "sitecustomize.py").write_text(
        "import json, os\n"
        f"names = {names!r}\n"
        f"with open({str(out_file)!r}, 'w') as fh:\n"
        "    json.dump({n: os.environ.get(n) for n in names}, fh)\n",
        encoding="utf-8",
    )
    child_env = dict(env)
    # The probe dir must be the interpreter's sitecustomize source; anything
    # the mechanism itself put in PYTHONPATH is irrelevant to this assertion.
    child_env["PYTHONPATH"] = str(probe_dir)
    completed = subprocess.run(
        [sys.executable, "-c", "pass"],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(out_file.read_text(encoding="utf-8"))


def _assert_canonical_at_startup(snapshot: dict[str, str | None]) -> None:
    wrong = {k: snapshot.get(k) for k, v in _CANONICAL.items() if snapshot.get(k) != v}
    assert not wrong, f"canonical values not present at interpreter startup: {wrong}"
    leaked = [k for k in SCRUBBED_ENV_KEYS if snapshot.get(k) is not None]
    assert not leaked, f"scrubbed names visible at interpreter startup: {leaked}"


def test_sitecustomize_probe_build_telemetry_safe_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TELEMETRY", "true")
    monkeypatch.setenv("OTEL_CONFIG_FILE", "/tmp/evil.yaml")
    _assert_canonical_at_startup(_probe_at_interpreter_startup(build_telemetry_safe_env(), tmp_path))


def test_sitecustomize_probe_executor_child_env(tmp_path, monkeypatch):
    from agentic.executor.runner import _scrubbed_env

    monkeypatch.setenv("GH_TELEMETRY", "log")
    monkeypatch.setenv("GROK_API_KEY", "secret-should-not-cross")
    snapshot = _probe_at_interpreter_startup(_scrubbed_env(), tmp_path)
    _assert_canonical_at_startup(snapshot)


def test_sitecustomize_probe_scheduler_overlay(tmp_path):
    """A generated job's env is the overlay + whatever the scheduler adds;
    the overlay alone must already pin every canonical value at startup."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **scheduler_env_overlay()}
    snapshot = _probe_at_interpreter_startup(env, tmp_path)
    wrong = {k: snapshot.get(k) for k, v in _CANONICAL.items() if snapshot.get(k) != v}
    assert not wrong, wrong


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell eval semantics")
def test_sitecustomize_probe_export_cli_roundtrip(tmp_path):
    """The launchers' mechanism end to end: eval the --export output in a
    shell, then prove the values at a child interpreter's startup."""
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    out_file = tmp_path / "snapshot.json"
    names = sorted(set(_CANONICAL) | set(SCRUBBED_ENV_KEYS))
    (probe_dir / "sitecustomize.py").write_text(
        "import json, os\n"
        f"names = {names!r}\n"
        f"with open({str(out_file)!r}, 'w') as fh:\n"
        "    json.dump({n: os.environ.get(n) for n in names}, fh)\n",
        encoding="utf-8",
    )
    script = (
        f'set -e\ncd "{REPO_ROOT}"\n'
        f'eval "$("{sys.executable}" -m utils.telemetry_kill --export shell)"\n'
        f'export PYTHONPATH="{probe_dir}"\n'
        f'exec "{sys.executable}" -c pass\n'
    )
    completed = subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            # Hostile ambient values the eval'd block must overwrite/remove.
            "GH_TELEMETRY": "true",
            "OTEL_CONFIG_FILE": "/tmp/evil.yaml",
        },
    )
    assert completed.returncode == 0, completed.stderr
    _assert_canonical_at_startup(json.loads(out_file.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# agentic verifier children
# ---------------------------------------------------------------------------


def test_executor_children_retain_controls_and_exclude_secrets(tmp_path, monkeypatch):
    from agentic.executor.runner import Check, run_verification

    monkeypatch.setenv("GH_TELEMETRY", "true")
    monkeypatch.setenv("GROK_API_KEY", "secret-should-not-cross")
    snippet = (
        "import json, os, sys\n"
        "sys.stdout.write(json.dumps({\n"
        "    'gh': os.environ.get('GH_TELEMETRY'),\n"
        "    'otel': os.environ.get('OTEL_SDK_DISABLED'),\n"
        "    'pipvc': os.environ.get('PIP_DISABLE_PIP_VERSION_CHECK'),\n"
        "    'grok': os.environ.get('GROK_API_KEY'),\n"
        "    'cfg': os.environ.get('OTEL_CONFIG_FILE'),\n"
        "}))\n"
    )
    report = run_verification(tmp_path, [Check(name="env-probe", argv=(sys.executable, "-c", snippet))])
    assert report.ok is True
    seen = json.loads(report.results[0].stdout)
    assert seen == {"gh": "false", "otel": "true", "pipvc": "1", "grok": None, "cfg": None}


# ---------------------------------------------------------------------------
# gh spawn sites
# ---------------------------------------------------------------------------


def test_gh_version_probe_receives_forced_safe_env(monkeypatch):
    import agentic.gh_client as gh_client

    monkeypatch.setenv("GH_TELEMETRY", "true")
    monkeypatch.setenv("GH_NO_UPDATE_NOTIFIER", "0")
    captured: dict[str, dict[str, str]] = {}

    def fake_run(argv, **kwargs):
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, stdout="gh version 2.90.0 (2026-01-01)\n", stderr="")

    monkeypatch.setattr(gh_client.shutil, "which", lambda name: "/fake/bin/gh")
    monkeypatch.setattr(gh_client.subprocess, "run", fake_run)
    gh_client.check_gh_version()
    env = captured["env"]
    assert env["GH_TELEMETRY"] == "false", "ambient GH_TELEMETRY=true must never reach a gh child"
    assert env["GH_NO_UPDATE_NOTIFIER"] == "1"
    assert env["GH_NO_EXTENSION_UPDATE_NOTIFIER"] == "1"


def test_gh_read_op_receives_forced_safe_env(monkeypatch):
    import agentic.gh_client as gh_client

    monkeypatch.setenv("GH_TELEMETRY", "log")
    captured: dict[str, dict[str, str]] = {}

    def fake_run(argv, **kwargs):
        captured["env"] = kwargs["env"]
        # run_read's internal check_gh_version probe must parse; the read op
        # itself returns JSON.
        if argv[-1] == "version":
            return subprocess.CompletedProcess(argv, 0, stdout="gh version 2.90.0 (2026-01-01)\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    monkeypatch.setattr(gh_client.shutil, "which", lambda name: "/fake/bin/gh")
    monkeypatch.setattr(gh_client.subprocess, "run", fake_run)
    gh_client.run_read("repo_view", "cgfixit/CyClaw", gh_bin="/fake/bin/gh")
    assert captured["env"]["GH_TELEMETRY"] == "false"
    # GH_TOKEN-style auth still flows: the base is the full inherited env.
    monkeypatch.setenv("GH_TOKEN", "auth-token-passes-through")
    gh_client.run_read("repo_view", "cgfixit/CyClaw", gh_bin="/fake/bin/gh")
    assert captured["env"]["GH_TOKEN"] == "auth-token-passes-through"


def test_writer_execute_references_safe_env_builder():
    """The writer's spawn is gated behind EXECUTION_ENABLED plumbing that
    conftest deliberately disarms; pin the wiring at source level (the
    otel-hardening checker's T12 enforces the same from outside pytest)."""
    source = (REPO_ROOT / "agentic" / "writer.py").read_text(encoding="utf-8")
    assert "env=build_telemetry_safe_env()" in source


# ---------------------------------------------------------------------------
# Docker surfaces (static -- no docker daemon in CI)
# ---------------------------------------------------------------------------


def _dockerfile_env_pairs() -> dict[str, str]:
    pairs: dict[str, str] = {}
    text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8").replace("\\\n", " ")
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("ENV "):
            continue
        import re

        for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|\S*)', line[4:]):
            value = m.group(2)
            pairs[m.group(1)] = value[1:-1] if value.startswith('"') else value
    return pairs


def test_dockerfile_delivers_canonical_env():
    pairs = _dockerfile_env_pairs()
    assert "CYCLAW_TELEMETRY_KILL" not in pairs, "decorative marker must stay removed"
    wrong = {k: pairs.get(k) for k, v in _CANONICAL.items() if pairs.get(k) != v}
    assert not wrong, f"Dockerfile ENV disagrees with the canonical maps: {wrong}"


def test_compose_delivers_canonical_env():
    text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    pairs: dict[str, str] = {}
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("- ") and "=" in stripped:
            name, _, value = stripped[2:].partition("=")
            pairs[name.strip()] = value
    assert "CYCLAW_TELEMETRY_KILL" not in pairs, "decorative marker must stay removed"
    wrong = {k: pairs.get(k) for k, v in _CANONICAL.items() if pairs.get(k) != v}
    assert not wrong, f"compose environment disagrees with the canonical maps: {wrong}"


# ---------------------------------------------------------------------------
# Generated launchd plists / Windows tasks / cron
# ---------------------------------------------------------------------------


def test_service_plist_generator_carries_overlay(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "macos"))
    try:
        import generate_service_plist as gsp
    finally:
        sys.path.pop(0)
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(gsp.platform, "system", lambda: "Darwin")
    code = gsp.main([
        "--service", "harness", "--confirm", "--reason", "delivery test",
    ])
    assert code == 0
    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.harness.plist"
    document = plistlib.loads(plist_path.read_bytes())
    env = document["EnvironmentVariables"]
    for key, value in _CANONICAL.items():
        assert env.get(key) == value, f"{key} missing/wrong in generated plist"
    assert env["CYCLAW_HOME"] == str(home / ".CyClaw"), "service-specific env must survive the overlay"


def test_windows_cmd_launcher_set_lines(tmp_path):
    from utils.win_schtasks import write_cmd_launcher

    path = tmp_path / "task.cmd"
    write_cmd_launcher(path, ["python.exe", "-m", "sync.cli", "sync"], env=scheduler_env_overlay())
    content = path.read_bytes().decode("utf-8")
    assert 'set "GH_TELEMETRY=false"' in content
    assert 'set "POWERSHELL_TELEMETRY_OPTOUT=1"' in content
    assert 'set "POWERSHELL_UPDATECHECK=Off"' in content
    # cmd cannot express empty-but-present: `set "NAME="` DELETES the var --
    # the deliberate, documented form for the two blank CHROMA_OTEL_* names.
    assert 'set "CHROMA_OTEL_COLLECTION_ENDPOINT="' in content
    assert 'set "CHROMA_OTEL_SERVICE_NAME="' in content
    # Inherited scrubbed names are deleted before the env block (Task
    # Scheduler jobs inherit machine/user env; set "NAME=" deletes).
    assert 'set "OTEL_CONFIG_FILE="' in content
    assert 'set "LANGSMITH_API_KEY="' in content
    assert content.index('set "OTEL_CONFIG_FILE="') < content.index('set "GH_TELEMETRY=false"')
    assert content.index('set "GH_TELEMETRY=false"') < content.index("python.exe")


def test_sync_cron_line_env_prefix(monkeypatch):
    import sync.scheduler as scheduler

    monkeypatch.setattr(scheduler.platform, "system", lambda: "Linux")

    class _Cfg:
        _config_path = None
        log_dir = ""
        local_path = str(REPO_ROOT)

    cmd = scheduler._sync_command(_Cfg())
    assert " env " in f" {cmd} "
    # shlex.quote leaves shell-safe tokens unquoted -- assert the pair itself.
    assert "GH_TELEMETRY=false" in cmd
    assert "OTEL_SDK_DISABLED=true" in cmd
    assert "CHROMA_OTEL_COLLECTION_ENDPOINT=" in cmd
    # Inherited scrubbed names must be REMOVED pre-interpreter: positive
    # assignments cannot do that, so the prefix carries env -u unsets.
    assert "-u OTEL_CONFIG_FILE" in cmd
    assert "-u OTEL_EXPERIMENTAL_CONFIG_FILE" in cmd
    assert "-u LANGSMITH_API_KEY" in cmd
    assert cmd.index("-u OTEL_CONFIG_FILE") < cmd.index("GH_TELEMETRY=false")
    assert cmd.index(" env ") < cmd.index("-m sync.cli"), "env prefix must precede the interpreter"


def test_sync_windows_bat_set_lines(tmp_path, monkeypatch):
    import sync.scheduler as scheduler

    class _Cfg:
        _config_path = None
        log_dir = str(tmp_path)
        local_path = str(REPO_ROOT)

    monkeypatch.setattr(scheduler, "_repo_root", lambda cfg: str(REPO_ROOT))
    bat_path = scheduler._write_windows_launcher(_Cfg())
    content = Path(bat_path).read_text(encoding="utf-8")
    assert 'set "GH_TELEMETRY=false"' in content
    assert 'set "OTEL_SDK_DISABLED=true"' in content
    assert 'set "OTEL_CONFIG_FILE="' in content, "inherited scrub names must be deleted"
    assert content.index('set "OTEL_CONFIG_FILE="') < content.index('set "GH_TELEMETRY=false"')
    assert content.index('set "GH_TELEMETRY=false"') < content.index("-m sync.cli")


def test_sync_launchd_plist_carries_overlay(tmp_path, monkeypatch):
    import sync.scheduler as scheduler

    class _Cfg:
        _config_path = None
        log_dir = str(tmp_path / "logs")
        local_path = str(REPO_ROOT)

    sched = scheduler.LaunchdScheduler(_Cfg())
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    # The schedule/argv helpers read many RcloneConfig fields irrelevant to
    # env delivery -- stub them so this test pins exactly one thing: the
    # generated document's EnvironmentVariables.
    monkeypatch.setattr(scheduler, "_launchd_calendar_interval", lambda cfg: {"Hour": 3, "Minute": 30})
    monkeypatch.setattr(scheduler, "_launchd_program_arguments", lambda cfg: ["/usr/bin/python3", "-m", "sync.cli", "sync"])
    monkeypatch.setattr(scheduler, "_repo_root", lambda cfg: str(REPO_ROOT))
    sched.install()
    plist_path = tmp_path / "home" / "Library" / "LaunchAgents" / f"{scheduler.LAUNCHD_LABEL}.plist"
    document = plistlib.loads(plist_path.read_bytes())
    assert document["EnvironmentVariables"] == scheduler_env_overlay()


# ---------------------------------------------------------------------------
# Launcher scripts (static text -- .sh/.ps1 cannot execute cross-platform here)
# ---------------------------------------------------------------------------


def test_macos_launcher_exports_canonical_block():
    text = (REPO_ROOT / "macos" / "invoke-cyclaw.sh").read_text(encoding="utf-8")
    # -S -E is part of the contract: no site init in the helper interpreter.
    assert "-S -E -m utils.telemetry_kill --export shell" in text
    # Overwrite semantics: the eval must come AFTER the dotenv sourcing.
    assert text.index("_source_dotenv") < text.index("--export shell")
    # And BEFORE the servers start.
    assert text.index("--export shell") < text.index('-m uvicorn gate:app --host 127.0.0.1')


def test_powershell_launcher_exports_canonical_block():
    text = (REPO_ROOT / "powershell" / "Invoke-CyClaw.ps1").read_text(encoding="utf-8")
    assert "-S -E -m utils.telemetry_kill --export powershell" in text
    assert text.index("Import-CyclawDotenv") < text.index("--export powershell")
    assert text.index("--export powershell") < text.index("-m harness.server")


def test_install_shim_sets_pwsh_optouts_before_powershell_starts():
    text = (REPO_ROOT / "powershell" / "Install-CyClaw.ps1").read_text(encoding="utf-8")
    shim_start = text.index("cyclaw.cmd")
    ps_line = text.index("powershell -NoProfile", shim_start)
    assert shim_start < text.index('set "POWERSHELL_TELEMETRY_OPTOUT=1"', shim_start) < ps_line
    assert shim_start < text.index('set "POWERSHELL_UPDATECHECK=Off"', shim_start) < ps_line
