"""Behavior tests for the macOS fsconnect jail and config setup."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from macos._enable_fsconnect_readlist import enable_readlist

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HELPER = _REPO_ROOT / "macos" / "_enable_fsconnect_readlist.py"
_SETUP = _REPO_ROOT / "macos" / "setup-fsconnect.sh"
_INSTALLER = _REPO_ROOT / "macos" / "install-cyclaw.sh"
_UNINSTALLER = _REPO_ROOT / "macos" / "uninstall-cyclaw.sh"
_EXPECTED_OPS = ["fs_list", "fs_stat", "fs_read"]
_BASH = shutil.which("bash") or "bash"


def _copy_config(tmp_path: Path) -> Path:
    target = tmp_path / "config.yaml"
    shutil.copyfile(_REPO_ROOT / "config.yaml", target)
    return target


def _run_script(
    script: Path,
    *args: str,
    home: Path,
    config: Path,
    input_text: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "SHELL": "/bin/bash",
            "CYCLAW_FSCONNECT_CONFIG": str(config),
            "CYCLAW_FSCONNECT_PYTHON": sys.executable,
        }
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_BASH, str(script), *args],  # noqa: S603
        check=False,
        capture_output=True,
        input=input_text,
        text=True,
        env=env,
        timeout=60,
    )


def _run_cli(config: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentic.fsconnect.cli", "--config", str(config), *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        timeout=60,
    )


def test_helper_applies_exact_safe_contract_and_is_byte_idempotent(tmp_path: Path) -> None:
    config = _copy_config(tmp_path)
    root = tmp_path / "CyClaw-FS"
    root.mkdir()

    assert enable_readlist(config, root) is True
    first = config.read_bytes()
    block = yaml.safe_load(first)["fsconnect"]
    resolved = str(root.resolve())
    assert block["enabled"] is True
    assert block["allowed_roots"] == [resolved]
    assert block["allowed_fs_ops"] == _EXPECTED_OPS
    assert block["writes_enabled"] is False
    assert block["writable_roots"] == [resolved]
    assert block["strict_roots"] is True
    assert block["index_enabled"] is False
    assert block["allow_hard_delete"] is False
    assert block["allow_unc_roots"] is False
    assert block["allow_macos_volume_roots"] is False
    assert block["follow_symlinks"] is False
    assert block["scan_content"] is True
    assert enable_readlist(config, root) is False
    assert config.read_bytes() == first


def test_helper_resets_macos_volume_opt_in(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "before: keep\n"
        "fsconnect:\n"
        "  enabled: false\n"
        "  allow_macos_volume_roots: true\n"
        "after: keep\n",
        encoding="utf-8",
    )
    root = tmp_path / "CyClaw-FS"
    root.mkdir()

    assert enable_readlist(config, root) is True
    first = config.read_bytes()
    assert yaml.safe_load(first)["fsconnect"]["allow_macos_volume_roots"] is False
    assert enable_readlist(config, root) is False
    assert config.read_bytes() == first


def test_helper_preserves_config_outside_fsconnect_and_file_mode(tmp_path: Path) -> None:
    config = _copy_config(tmp_path)
    config.chmod(0o640)
    before = config.read_text(encoding="utf-8")
    prefix, rest = before.split("fsconnect:", maxsplit=1)
    _old_block, suffix = rest.split("# ===========================\n# SQL connector", maxsplit=1)
    root = tmp_path / "CyClaw-FS"
    root.mkdir()

    enable_readlist(config, root)
    after = config.read_text(encoding="utf-8")
    assert after.startswith(prefix + "fsconnect:")
    assert after.endswith("# ===========================\n# SQL connector" + suffix)
    if os.name != "nt":
        assert stat.S_IMODE(config.stat().st_mode) == 0o640


def test_helper_invalid_document_is_unchanged(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("- not-a-mapping\n", encoding="utf-8")
    root = tmp_path / "CyClaw-FS"
    root.mkdir()
    before = config.read_bytes()

    completed = subprocess.run(  # noqa: S603
        [sys.executable, str(_HELPER), "--config", str(config), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 1
    assert config.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX shell and chmod semantics")
def test_setup_prepare_only_then_default_is_idempotent(tmp_path: Path) -> None:
    config = _copy_config(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    before = config.read_bytes()

    prepared = _run_script(_SETUP, "--prepare-only", home=home, config=config)
    assert prepared.returncode == 0, prepared.stderr
    jail = home / "CyClaw-FS"
    assert stat.S_IMODE(jail.stat().st_mode) == 0o700
    readme = jail / "README.txt"
    assert "Writes and indexing are off" in readme.read_text(encoding="utf-8")
    assert config.read_bytes() == before

    first = _run_script(_SETUP, home=home, config=config)
    assert first.returncode == 0, first.stderr
    enabled = config.read_bytes()
    second = _run_script(_SETUP, home=home, config=config)
    assert second.returncode == 0, second.stderr
    assert config.read_bytes() == enabled


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX installer integration")
def test_installer_default_enables_but_no_fsconnect_does_not(tmp_path: Path) -> None:
    enabled_home = tmp_path / "enabled-home"
    enabled_home.mkdir()
    enabled_dir = tmp_path / "enabled"
    enabled_dir.mkdir()
    enabled_config = _copy_config(enabled_dir)
    enabled = _run_script(
        _INSTALLER,
        "--repo-path",
        str(_REPO_ROOT),
        "--skip-python-deps",
        "--no-profile-edit",
        "--no-path-edit",
        home=enabled_home,
        config=enabled_config,
    )
    assert enabled.returncode == 0, enabled.stderr
    enabled_block = yaml.safe_load(enabled_config.read_text(encoding="utf-8"))["fsconnect"]
    assert enabled_block["enabled"] is True

    skipped_dir = tmp_path / "skipped"
    skipped_dir.mkdir()
    skipped_config = _copy_config(skipped_dir)
    skipped_home = tmp_path / "skipped-home"
    skipped_home.mkdir()
    skipped = _run_script(
        _INSTALLER,
        "--repo-path",
        str(_REPO_ROOT),
        "--skip-python-deps",
        "--no-profile-edit",
        "--no-path-edit",
        "--no-fsconnect",
        home=skipped_home,
        config=skipped_config,
    )
    assert skipped.returncode == 0, skipped.stderr
    skipped_block = yaml.safe_load(skipped_config.read_text(encoding="utf-8"))["fsconnect"]
    assert skipped_block["enabled"] is False
    assert (skipped_home / "CyClaw-FS").is_dir()


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX installer integration")
def test_installer_enabled_profile_lists_stats_reads_and_dry_runs_writes(tmp_path: Path) -> None:
    """Exercise the installed Mac profile through the real fsconnect CLI."""
    home = tmp_path / "home"
    home.mkdir()
    config = _copy_config(tmp_path)
    audit = tmp_path / "audit.jsonl"
    config_text = config.read_text(encoding="utf-8")
    audit_needle = 'audit_file: "logs/audit.jsonl"'
    assert audit_needle in config_text
    # JSON strings are valid YAML scalars and avoid PyYAML's standalone-scalar
    # document terminator (``...``), which would split this copied config.
    audit_yaml = json.dumps(str(audit))
    config.write_text(
        config_text.replace(audit_needle, f"audit_file: {audit_yaml}"),
        encoding="utf-8",
    )

    installed = _run_script(
        _INSTALLER,
        "--repo-path",
        str(_REPO_ROOT),
        "--skip-python-deps",
        "--no-profile-edit",
        "--no-path-edit",
        home=home,
        config=config,
    )
    assert installed.returncode == 0, installed.stderr

    jail = home / "CyClaw-FS"
    note = jail / "note.txt"
    content = "operator-provided Mac jail note\n"
    note.write_text(content, encoding="utf-8")

    block = yaml.safe_load(config.read_text(encoding="utf-8"))["fsconnect"]
    assert block["enabled"] is True
    assert block["allowed_roots"] == [str(jail.resolve())]
    assert block["allowed_fs_ops"] == _EXPECTED_OPS
    assert block["writes_enabled"] is False
    assert block["index_enabled"] is False

    status = _run_cli(config, "status")
    assert status.returncode == 0, status.stderr
    assert "writes_enabled" in status.stdout and "False" in status.stdout

    listed = _run_cli(config, "list", "--root", str(jail))
    assert listed.returncode == 0, listed.stderr
    list_result = yaml.safe_load(listed.stdout)
    assert {entry["name"] for entry in list_result["entries"]} >= {"README.txt", "note.txt"}

    stated = _run_cli(config, "stat", "--root", str(jail), "--path", "note.txt")
    assert stated.returncode == 0, stated.stderr
    stat_result = yaml.safe_load(stated.stdout)
    assert stat_result["type"] == "file"
    assert stat_result["size"] == len(content.encode("utf-8"))

    read = _run_cli(config, "read", "--root", str(jail), "--path", "note.txt")
    assert read.returncode == 0, read.stderr
    read_result = yaml.safe_load(read.stdout)
    assert read_result["content"] == content

    denied = jail / "must-not-exist.txt"
    write = _run_cli(
        config,
        "write",
        "--root",
        str(jail),
        "--path",
        denied.name,
        "--body",
        "blocked",
        "--reason",
        "CI verifies the default dry-run gate",
    )
    assert write.returncode == 0, write.stderr
    write_result = yaml.safe_load(write.stdout)
    assert write_result["executed"] is False
    assert not denied.exists()

    if sys.platform == "darwin" and "requested a Time Machine exclusion" in installed.stdout:
        excluded = subprocess.run(
            ["tmutil", "isexcluded", str(jail)],  # noqa: S603,S607 -- macOS system utility, argv list
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert excluded.returncode == 0, excluded.stderr
        assert "[Excluded]" in excluded.stdout


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX uninstall integration")
def test_uninstall_retains_jail_unless_confirmed(tmp_path: Path) -> None:
    config = _copy_config(tmp_path)
    home = tmp_path / "home"
    jail = home / "CyClaw-FS"
    jail.mkdir(parents=True)
    (jail / "keep.txt").write_text("keep", encoding="utf-8")

    default = _run_script(_UNINSTALLER, home=home, config=config)
    assert default.returncode == 0, default.stderr
    assert jail.is_dir()

    declined = _run_script(_UNINSTALLER, "--remove-fsconnect", home=home, config=config, input_text="n\n")
    assert declined.returncode == 0, declined.stderr
    assert jail.is_dir()

    removed = _run_script(_UNINSTALLER, "--remove-fsconnect", home=home, config=config, input_text="y\n")
    assert removed.returncode == 0, removed.stderr
    assert not jail.exists()


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX uninstall integration")
def test_uninstall_skips_unschedule_when_no_repo_present(tmp_path: Path) -> None:
    """No ~/.CyClaw/repo (harness never installed, or --skip-python-deps only) -- silent no-op."""
    config = _copy_config(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    result = _run_script(_UNINSTALLER, home=home, config=config)
    assert result.returncode == 0, result.stderr
    assert "checking for a registered sync schedule" not in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX uninstall integration")
def test_uninstall_unschedule_is_nonfatal_on_broken_sync_config(tmp_path: Path) -> None:
    """A present but sync-less config.yaml must not abort the rest of uninstall.

    This never reaches (and therefore never touches) any real scheduler
    backend -- sync.cli's own config loader raises before get_scheduler() is
    called, since the sync: block is absent -- so this is safe to run against
    the real crontab/launchd state of whatever host runs the test.
    """
    home = tmp_path / "home"
    repo_dir = home / ".CyClaw" / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "config.yaml").write_text("unrelated: true\n", encoding="utf-8")
    config = _copy_config(tmp_path)  # unused by uninstall-cyclaw.sh itself; _run_script requires it

    result = _run_script(
        _UNINSTALLER,
        home=home,
        config=config,
        extra_env={"PYTHONPATH": str(_REPO_ROOT)},
    )
    assert result.returncode == 0, result.stderr
    assert "checking for a registered sync schedule" in result.stdout
    assert "WARNING: could not clean up the sync schedule" in result.stderr
    assert "uninstall complete" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX uninstall integration")
def test_uninstall_removes_landed_launchagent_plists(tmp_path: Path) -> None:
    """Generated telegram/fsconnect plists at the well-known path must not survive uninstall.

    launchctl bootout is Darwin-only; the file delete is what this test pins
    (Linux CI has no launchd). Missing plists are a silent no-op.
    """
    config = _copy_config(tmp_path)
    home = tmp_path / "home"
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    keep = agents / "com.example.unrelated.plist"
    keep.write_text("keep", encoding="utf-8")
    landed = (
        "com.cgfixit.cyclaw.telegram-poll.plist",
        "com.cgfixit.cyclaw.telegram-health.plist",
        "com.cgfixit.cyclaw.fsconnect-trash.plist",
        "com.cgfixit.cyclaw.gate.plist",
        "com.cgfixit.cyclaw.harness.plist",
        "com.cgfixit.cyclaw.opentweet.plist",
        "com.cgfixit.cyclaw.sync.plist",
    )
    for name in landed:
        (agents / name).write_text("generated", encoding="utf-8")

    result = _run_script(_UNINSTALLER, home=home, config=config)
    assert result.returncode == 0, result.stderr
    for name in landed:
        assert not (agents / name).exists(), name
        assert f"removing LaunchAgent {name.removesuffix('.plist')}" in result.stdout
    assert keep.is_file()
    assert "uninstall complete" in result.stdout
