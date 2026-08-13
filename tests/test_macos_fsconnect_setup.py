"""Behavior tests for the macOS fsconnect jail and config setup."""

from __future__ import annotations

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
    return subprocess.run(
        [_BASH, str(script), *args],  # noqa: S603
        check=False,
        capture_output=True,
        input=input_text,
        text=True,
        env=env,
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
    assert block["follow_symlinks"] is False
    assert block["scan_content"] is True
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
