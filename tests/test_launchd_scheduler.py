"""Self-contained tests for sync.scheduler's LaunchdScheduler (Darwin-only backend).

Runnable with ``pytest --noconftest`` (no conftest fixtures), matching
``tests/test_sync_scheduler.py``'s pattern: builds an ``RcloneConfig`` directly,
patches the subprocess / which / platform / Path.home boundary. No real
``launchctl`` is ever invoked and no real ``~/Library/LaunchAgents`` is ever
touched -- ``Path.home`` is monkeypatched to a ``tmp_path`` for every test that
writes or reads a plist.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sync.config import RcloneConfig
from sync.scheduler import (
    LAUNCHD_LABEL,
    LaunchdScheduler,
    ScheduleEntry,
    get_scheduler,
)
from utils.errors import SchedulerError, SyncConfigError
from utils.telemetry_kill import scheduler_env_overlay

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CORPUS = str(_REPO_ROOT / "data" / "corpus")


def _make_cfg(**overrides) -> RcloneConfig:
    kwargs: dict = dict(
        local_path=_CORPUS,
        remote_name="dropbox_cyclaw",
        remote_path="CyClaw/corpus",
        schedule_hour=2,
        schedule_min=0,
    )
    kwargs.update(overrides)
    return RcloneConfig(**kwargs)


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# RcloneConfig validation of the new scheduling fields
# ---------------------------------------------------------------------------


def test_default_scheduler_backend_and_frequency_are_cron_and_daily() -> None:
    cfg = _make_cfg()
    assert cfg.scheduler_backend == "cron"
    assert cfg.schedule_frequency == "daily"
    assert cfg.schedule_weekday == 1
    assert cfg.schedule_day == 1


@pytest.mark.parametrize("backend", ["darwin", "LAUNCHD", "systemd", ""])
def test_invalid_scheduler_backend_raises(backend: str) -> None:
    with pytest.raises(SyncConfigError):
        _make_cfg(scheduler_backend=backend)


@pytest.mark.parametrize("freq", ["hourly", "DAILY", "yearly", ""])
def test_invalid_schedule_frequency_raises(freq: str) -> None:
    with pytest.raises(SyncConfigError):
        _make_cfg(schedule_frequency=freq)


@pytest.mark.parametrize("weekday", [-1, 8, 100])
def test_invalid_schedule_weekday_raises(weekday: int) -> None:
    with pytest.raises(SyncConfigError):
        _make_cfg(schedule_weekday=weekday)


@pytest.mark.parametrize("day", [0, 32, -1])
def test_invalid_schedule_day_raises(day: int) -> None:
    with pytest.raises(SyncConfigError):
        _make_cfg(schedule_day=day)


def test_valid_weekday_boundaries_accepted() -> None:
    # 0 and 7 both mean Sunday in launchd's own convention.
    assert _make_cfg(schedule_weekday=0).schedule_weekday == 0
    assert _make_cfg(schedule_weekday=7).schedule_weekday == 7


# ---------------------------------------------------------------------------
# get_scheduler backend selection
# ---------------------------------------------------------------------------


def test_get_scheduler_default_backend_darwin_is_still_cron() -> None:
    # Zero behavior change for existing operators: scheduler_backend defaults
    # to "cron", so Darwin with no override keeps using CronScheduler.
    from sync.scheduler import CronScheduler

    cfg = _make_cfg()
    with patch("sync.scheduler.platform.system", return_value="Darwin"):
        sched = get_scheduler(cfg)
    assert isinstance(sched, CronScheduler)


def test_get_scheduler_launchd_backend_on_darwin_returns_launchd_scheduler() -> None:
    cfg = _make_cfg(scheduler_backend="launchd")
    with patch("sync.scheduler.platform.system", return_value="Darwin"):
        sched = get_scheduler(cfg)
    assert isinstance(sched, LaunchdScheduler)


@pytest.mark.parametrize("system", ["Linux", "Windows"])
def test_get_scheduler_launchd_backend_off_darwin_raises(system: str) -> None:
    cfg = _make_cfg(scheduler_backend="launchd")
    with patch("sync.scheduler.platform.system", return_value=system):
        with pytest.raises(SchedulerError, match="launchd"):
            get_scheduler(cfg)


# ---------------------------------------------------------------------------
# LaunchdScheduler.install()
# ---------------------------------------------------------------------------


def _install(cfg: RcloneConfig, home: Path) -> ScheduleEntry:
    with (
        patch("sync.scheduler.platform.system", return_value="Darwin"),
        patch("sync.scheduler.Path.home", return_value=home),
    ):
        return LaunchdScheduler(cfg).install()


def test_install_writes_valid_plist(tmp_path: Path) -> None:
    cfg = _make_cfg(schedule_hour=3, schedule_min=15)
    entry = _install(cfg, tmp_path)

    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    assert plist_path.exists()
    document = plistlib.loads(plist_path.read_bytes())

    assert document["Label"] == LAUNCHD_LABEL
    assert document["RunAtLoad"] is False
    assert document["StartCalendarInterval"] == {"Hour": 3, "Minute": 15}
    assert document["ProgramArguments"][1:3] == ["-m", "sync.cli"]
    assert document["ProgramArguments"][-1] == "sync"
    assert document["WorkingDirectory"] == str(_REPO_ROOT)
    log_path = str(tmp_path / "Library" / "Logs" / "CyClaw" / "sync.log")
    assert document["StandardOutPath"] == log_path
    assert document["StandardErrorPath"] == log_path

    assert entry.platform_name == "darwin"
    assert entry.cron_or_time == "daily 03:15"
    assert entry.raw == str(plist_path)


def test_install_never_embeds_a_secret_or_environment_variables(tmp_path: Path) -> None:
    cfg = _make_cfg()
    _install(cfg, tmp_path)

    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    document = plistlib.loads(plist_path.read_bytes())
    raw_bytes = plist_path.read_bytes()

    # Exactly the canonical non-secret overlay -- the "never embeds a
    # secret" guarantee now reads: nothing beyond these fixed literals.
    assert document["EnvironmentVariables"] == scheduler_env_overlay()
    assert b"TOKEN" not in raw_bytes
    assert b"SECRET" not in raw_bytes
    assert b"REPLACE_" not in raw_bytes  # generated, not a hand-edit template


@pytest.mark.parametrize(
    ("frequency", "overrides", "expected_interval", "expected_human"),
    [
        ("daily", {}, {"Hour": 2, "Minute": 0}, "daily 02:00"),
        (
            "weekly",
            {"schedule_weekday": 1},
            {"Hour": 2, "Minute": 0, "Weekday": 1},
            "weekly Mon 02:00",
        ),
        (
            "monthly",
            {"schedule_day": 15},
            {"Hour": 2, "Minute": 0, "Day": 15},
            "monthly day 15 02:00",
        ),
    ],
)
def test_install_frequency_shapes_calendar_interval(
    tmp_path: Path, frequency: str, overrides: dict, expected_interval: dict, expected_human: str
) -> None:
    cfg = _make_cfg(schedule_frequency=frequency, **overrides)
    entry = _install(cfg, tmp_path)

    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    document = plistlib.loads(plist_path.read_bytes())
    assert document["StartCalendarInterval"] == expected_interval
    assert entry.cron_or_time == expected_human


def test_install_returns_bootstrap_hint_and_never_calls_subprocess(tmp_path: Path) -> None:
    cfg = _make_cfg()
    with (
        patch("sync.scheduler.platform.system", return_value="Darwin"),
        patch("sync.scheduler.Path.home", return_value=tmp_path),
        patch("sync.scheduler.subprocess.run") as mock_run,
    ):
        entry = LaunchdScheduler(cfg).install()

    mock_run.assert_not_called()  # install() must never auto-load the agent
    assert "launchctl bootstrap gui/" in entry.note
    assert str(tmp_path / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist") in entry.note
    assert "NOT loaded" in entry.note


def test_install_is_idempotent_and_overwrites(tmp_path: Path) -> None:
    cfg = _make_cfg(schedule_hour=1, schedule_min=0)
    _install(cfg, tmp_path)

    cfg2 = _make_cfg(schedule_hour=9, schedule_min=45)
    _install(cfg2, tmp_path)

    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    # Exactly one plist -- no leftover .tmp file, no duplicate.
    assert list((tmp_path / "Library" / "LaunchAgents").glob(f"{LAUNCHD_LABEL}*")) == [plist_path]
    document = plistlib.loads(plist_path.read_bytes())
    assert document["StartCalendarInterval"] == {"Hour": 9, "Minute": 45}


def test_install_on_non_darwin_raises() -> None:
    cfg = _make_cfg()
    with patch("sync.scheduler.platform.system", return_value="Linux"):
        with pytest.raises(SchedulerError, match="Darwin-only"):
            LaunchdScheduler(cfg).install()


# ---------------------------------------------------------------------------
# LaunchdScheduler.remove()
# ---------------------------------------------------------------------------


def test_remove_missing_plist_returns_false_with_no_subprocess_call(tmp_path: Path) -> None:
    cfg = _make_cfg()
    with (
        patch("sync.scheduler.platform.system", return_value="Darwin"),
        patch("sync.scheduler.Path.home", return_value=tmp_path),
        patch("sync.scheduler.subprocess.run") as mock_run,
    ):
        result = LaunchdScheduler(cfg).remove()

    assert result is False
    mock_run.assert_not_called()


def test_remove_existing_plist_boots_out_then_deletes(tmp_path: Path) -> None:
    cfg = _make_cfg()
    _install(cfg, tmp_path)
    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    assert plist_path.exists()

    with (
        patch("sync.scheduler.platform.system", return_value="Darwin"),
        patch("sync.scheduler.Path.home", return_value=tmp_path),
        patch("sync.scheduler.shutil.which", return_value="/bin/launchctl"),
        patch("sync.scheduler.subprocess.run", return_value=_completed()) as mock_run,
    ):
        result = LaunchdScheduler(cfg).remove()

    assert result is True
    assert not plist_path.exists()
    argv = mock_run.call_args.args[0]
    assert argv[0] == "/bin/launchctl"
    assert argv[1] == "bootout"
    assert str(plist_path) in argv


def test_remove_tolerates_missing_launchctl_binary(tmp_path: Path) -> None:
    cfg = _make_cfg()
    _install(cfg, tmp_path)
    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"

    with (
        patch("sync.scheduler.platform.system", return_value="Darwin"),
        patch("sync.scheduler.Path.home", return_value=tmp_path),
        patch("sync.scheduler.shutil.which", return_value=None),
        patch("sync.scheduler.subprocess.run") as mock_run,
    ):
        result = LaunchdScheduler(cfg).remove()

    assert result is True
    assert not plist_path.exists()
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# LaunchdScheduler.status()
# ---------------------------------------------------------------------------


def test_status_returns_none_when_no_plist(tmp_path: Path) -> None:
    cfg = _make_cfg()
    with (
        patch("sync.scheduler.platform.system", return_value="Darwin"),
        patch("sync.scheduler.Path.home", return_value=tmp_path),
    ):
        assert LaunchdScheduler(cfg).status() is None


def test_status_reports_loaded_state_from_launchctl_print(tmp_path: Path) -> None:
    cfg = _make_cfg(schedule_frequency="weekly", schedule_weekday=3)
    _install(cfg, tmp_path)

    with (
        patch("sync.scheduler.platform.system", return_value="Darwin"),
        patch("sync.scheduler.Path.home", return_value=tmp_path),
        patch("sync.scheduler.shutil.which", return_value="/bin/launchctl"),
        patch("sync.scheduler.subprocess.run", return_value=_completed(returncode=0)) as mock_run,
    ):
        entry = LaunchdScheduler(cfg).status()

    assert entry is not None
    assert entry.note == "loaded"
    assert entry.cron_or_time == "weekly Wed 02:00"
    argv = mock_run.call_args.args[0]
    assert argv[0] == "/bin/launchctl"
    assert argv[1] == "print"


def test_status_reports_not_loaded_when_launchctl_print_fails(tmp_path: Path) -> None:
    cfg = _make_cfg()
    _install(cfg, tmp_path)

    with (
        patch("sync.scheduler.platform.system", return_value="Darwin"),
        patch("sync.scheduler.Path.home", return_value=tmp_path),
        patch("sync.scheduler.shutil.which", return_value="/bin/launchctl"),
        patch("sync.scheduler.subprocess.run", return_value=_completed(returncode=1)),
    ):
        entry = LaunchdScheduler(cfg).status()

    assert entry is not None
    assert entry.note == "not loaded"


def test_status_tolerates_missing_launchctl_binary(tmp_path: Path) -> None:
    cfg = _make_cfg()
    _install(cfg, tmp_path)

    with (
        patch("sync.scheduler.platform.system", return_value="Darwin"),
        patch("sync.scheduler.Path.home", return_value=tmp_path),
        patch("sync.scheduler.shutil.which", return_value=None),
        patch("sync.scheduler.subprocess.run") as mock_run,
    ):
        entry = LaunchdScheduler(cfg).status()

    assert entry is not None
    assert "unavailable" in entry.note
    mock_run.assert_not_called()


def test_status_reflects_on_disk_plist_not_live_config(tmp_path: Path) -> None:
    # Install daily, then read status through a *different* cfg object that
    # has since drifted to weekly -- status must report what's on disk.
    installed_cfg = _make_cfg(schedule_frequency="daily", schedule_hour=4, schedule_min=30)
    _install(installed_cfg, tmp_path)

    drifted_cfg = _make_cfg(schedule_frequency="weekly", schedule_weekday=5)
    with (
        patch("sync.scheduler.platform.system", return_value="Darwin"),
        patch("sync.scheduler.Path.home", return_value=tmp_path),
        patch("sync.scheduler.shutil.which", return_value=None),
    ):
        entry = LaunchdScheduler(drifted_cfg).status()

    assert entry is not None
    assert entry.cron_or_time == "daily 04:30"  # from disk, not drifted_cfg


# ---------------------------------------------------------------------------
# Never touches the real filesystem outside a monkeypatched home
# ---------------------------------------------------------------------------


def test_status_and_remove_on_non_darwin_raise() -> None:
    cfg = _make_cfg()
    with patch("sync.scheduler.platform.system", return_value="Windows"):
        with pytest.raises(SchedulerError, match="Darwin-only"):
            LaunchdScheduler(cfg).status()
        with pytest.raises(SchedulerError, match="Darwin-only"):
            LaunchdScheduler(cfg).remove()
