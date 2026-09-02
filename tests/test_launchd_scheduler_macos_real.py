"""Real, unmocked execution tests for sync.scheduler.LaunchdScheduler
(Darwin-only backend).

test_launchd_scheduler.py proves this class's LOGIC by mocking
platform.system, Path.home, and (for status()/remove()) subprocess.run --
deliberately, for host-independent coverage -- but it never actually invokes
the real launchctl binary, and never round-trips a plist through the real
plistlib.dump/load path on a real macOS host.

This file is the opposite: every test here skips cleanly unless the host
really is Darwin (sys.platform == "darwin" unmodified -- no monkeypatching
of platform.system or subprocess anywhere in this module), and then
exercises install()/status()/remove() for real. Only Path.home is
redirected to tmp_path, so the runner's actual ~/Library/LaunchAgents is
never touched.

install() never calls launchctl at all (by design -- see the class's own
docstring: "generate, don't auto-enroll"), so no persistent LaunchAgent is
ever loaded into the real launchd here. status()'s and remove()'s launchctl
calls both tolerate "not loaded"/"no such process" as an expected outcome --
exactly what they see in these tests, since nothing here is ever
bootstrapped. Same real-vs-mocked split as test_fsconnect_macos_real.py
draws for agentic/fsconnect/pathsafe.py.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from sync.config import RcloneConfig
from sync.scheduler import LAUNCHD_LABEL, LaunchdScheduler

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="LaunchdScheduler is Darwin-only")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CORPUS = str(_REPO_ROOT / "data" / "corpus")


def _make_cfg(**overrides: object) -> RcloneConfig:
    kwargs: dict = dict(
        local_path=_CORPUS,
        remote_name="dropbox_cyclaw",
        remote_path="CyClaw/corpus",
        schedule_hour=2,
        schedule_min=0,
    )
    kwargs.update(overrides)
    return RcloneConfig(**kwargs)


def test_install_writes_a_real_plist_under_a_fake_home(tmp_path: Path) -> None:
    cfg = _make_cfg(schedule_hour=4, schedule_min=30)
    with patch("sync.scheduler.Path.home", return_value=tmp_path):
        entry = LaunchdScheduler(cfg).install()

    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    assert plist_path.exists()
    # A real plistlib round trip on the real interpreter/OS, not a mocked write.
    document = plistlib.loads(plist_path.read_bytes())
    assert document["Label"] == LAUNCHD_LABEL
    assert document["StartCalendarInterval"] == {"Hour": 4, "Minute": 30}
    assert entry.raw == str(plist_path)
    assert "launchctl bootstrap gui/" in entry.note


def test_status_probes_the_real_launchctl_binary(tmp_path: Path) -> None:
    """Nothing here is ever bootstrapped, so the real `launchctl print` call
    this exercises is expected to report "not loaded" -- the point is that
    the real binary is actually invoked and its real exit code interpreted,
    not that an agent is running."""
    cfg = _make_cfg()
    with patch("sync.scheduler.Path.home", return_value=tmp_path):
        LaunchdScheduler(cfg).install()
        entry = LaunchdScheduler(cfg).status()

    assert entry is not None
    assert entry.note == "not loaded"


def test_status_is_none_when_nothing_is_installed(tmp_path: Path) -> None:
    cfg = _make_cfg()
    with patch("sync.scheduler.Path.home", return_value=tmp_path):
        entry = LaunchdScheduler(cfg).status()
    assert entry is None


def test_remove_probes_the_real_launchctl_binary_and_deletes_the_plist(tmp_path: Path) -> None:
    cfg = _make_cfg()
    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    with patch("sync.scheduler.Path.home", return_value=tmp_path):
        LaunchdScheduler(cfg).install()
        assert plist_path.exists()
        removed = LaunchdScheduler(cfg).remove()

    assert removed is True
    assert not plist_path.exists()


def test_remove_on_a_never_installed_plist_is_a_no_op(tmp_path: Path) -> None:
    cfg = _make_cfg()
    with patch("sync.scheduler.Path.home", return_value=tmp_path):
        removed = LaunchdScheduler(cfg).remove()
    assert removed is False


def test_launchd_lifecycle_bootstrap_status_and_remove_report_real_state(tmp_path: Path) -> None:
    """The tests above only prove the real launchctl binary is invoked and a
    nonzero/failed result is tolerated -- status() maps EVERY nonzero
    ``launchctl print`` exit to "not loaded", so a malformed argv (wrong
    subcommand, domain, uid, or label) would read exactly the same as
    "nothing is loaded" and none of those tests would catch it.

    This test bootstraps the real generated plist for real (bypassing
    install()'s own deliberate "never auto-enroll" choice, only for this one
    test), confirms status() reports the genuinely-loaded state, then
    confirms remove() performs a real successful unload -- not just a
    tolerated failure -- and deletes the plist. The bootstrapped agent has
    RunAtLoad: False and a fixed StartCalendarInterval (see _make_cfg), so it
    never actually executes sync.cli; the finally block unconditionally
    boots it out even if an assertion above raises, so no test LaunchAgent
    is ever left registered on the real runner.
    """
    launchctl = shutil.which("launchctl")
    assert launchctl, "launchctl must be on PATH on a real macOS runner"
    uid = os.getuid()
    cfg = _make_cfg()
    with patch("sync.scheduler.Path.home", return_value=tmp_path):
        scheduler = LaunchdScheduler(cfg)
        entry = scheduler.install()
        plist_path = Path(entry.raw)
        try:
            bootstrap = subprocess.run(  # noqa: S603 -- fixed system binary, pytest-owned plist path
                [launchctl, "bootstrap", f"gui/{uid}", str(plist_path)],
                capture_output=True, text=True, timeout=10, check=False,
            )
            assert bootstrap.returncode == 0, f"bootstrap failed: {bootstrap.stderr}"

            status_entry = scheduler.status()
            assert status_entry is not None
            assert status_entry.note == "loaded"

            removed = scheduler.remove()
            assert removed is True
            assert not plist_path.exists()

            # remove()'s own bootout must have actually succeeded against a
            # genuinely loaded service -- confirm nothing is left registered,
            # not just that the plist file is gone.
            probe = subprocess.run(  # noqa: S603
                [launchctl, "print", f"gui/{uid}/{LAUNCHD_LABEL}"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            assert probe.returncode != 0
        finally:
            subprocess.run(  # noqa: S603
                [launchctl, "bootout", f"gui/{uid}", str(plist_path)],
                capture_output=True, text=True, timeout=10, check=False,
            )
