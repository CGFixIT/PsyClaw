"""Cross-platform scheduler abstraction for the CyClaw Dropbox corpus sync.

Linux/macOS: crontab manipulation via ``crontab -l`` and ``crontab -`` piping
             (never ``crontab -e`` -- no interactive editor in an unattended
             flow). A single tagged line is added/replaced/removed.
Windows:     ``schtasks`` for Task Scheduler create/delete/query.

We intentionally avoid third-party deps (python-crontab, pywin32) so this plugs
into CyClaw's offline-first / minimal-deps philosophy. The trade-off is
platform-specific subprocess calls -- kept tight, argv-list only, never
``shell=True``, and binaries resolved via ``shutil.which`` so no partial-path
taint reaches the kernel.

systemd note (Linux): the more robust Linux option (per the implementation plan
section 5.1) is a systemd ``--user`` ``Type=oneshot`` service driven by a timer
unit: it gives inherent overlap protection, journald logging, and
``Persistent=true`` catch-up after downtime. We do not auto-generate the unit
here; **cron is the implemented portable baseline** (works on macOS/WSL/BSD
too). Operators who prefer systemd should run the sync via a ``--user`` timer
calling ``python -m sync.cli sync`` and skip ``schedule``/``unschedule``. The
cron baseline has no built-in single-instance guard, so a wrapper-level lockfile
(or systemd) is recommended if manual and scheduled runs might collide.

macOS note: ``LaunchdScheduler`` below is a second, opt-in Darwin-only backend
(``sync.scheduler_backend: "launchd"`` in config.yaml; the shipped default
stays ``"cron"``, so this is zero behavior change unless an operator opts in).
It generates a real plist from resolved install paths -- no ``REPLACE_*``
placeholders -- and supports daily/weekly/monthly ``StartCalendarInterval``
schedules. It deliberately never calls ``launchctl load``/``bootstrap``
itself: ``install()`` only writes the plist file and returns the exact
``launchctl bootstrap`` command the operator must run by hand. See
``docs/work/MACOS_LAUNCHD_INTEGRATION_PLAN.md`` for the full rationale.

Scheduler identity: every task we register is tagged with ``TASK_TAG`` (a
trailing comment on Linux/macOS, the task name on Windows) so install/remove
only ever touch our own entry and never anything the user added by hand.
``LaunchdScheduler`` uses ``LAUNCHD_LABEL`` the same way -- one fixed plist
filename it alone owns.
"""

from __future__ import annotations

import logging
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from sync.config import RcloneConfig
from utils.errors import SchedulerError
from utils.telemetry_kill import SCRUBBED_ENV_KEYS, scheduler_env_overlay

logger = logging.getLogger(__name__)

TASK_TAG = "CYCLAW_DROPBOX_SYNC"
WINDOWS_TASK_NAME = "CyClaw Dropbox Sync"
LAUNCHD_LABEL = "com.cgfixit.cyclaw.sync"


def _is_managed_cron_line(line: str) -> bool:
    """Return whether *line* ends with CyClaw's exact cron ownership marker."""
    return line.rstrip().endswith(f" # {TASK_TAG}")


@dataclass
class ScheduleEntry:
    """Description of a scheduled job, in platform-neutral form."""

    platform_name: str  # "linux", "darwin", "windows"
    command: str  # the actual command line that will be run
    cron_or_time: str  # cron expression OR HH:MM
    raw: str  # the raw line / schtasks output for debugging
    # Backend-specific extra guidance for the operator, e.g. LaunchdScheduler's
    # required (never auto-run) `launchctl bootstrap` command. Empty for
    # backends where install() is already the complete, live action (cron,
    # schtasks) -- new field with a default so existing positional/keyword
    # construction of ScheduleEntry elsewhere is unaffected.
    note: str = ""


def _python_executable() -> str:
    """Best guess at the python interpreter to invoke from the scheduler."""
    candidate = sys.executable or "python"
    if candidate and os.path.isfile(candidate):
        return candidate
    found = shutil.which("python3") or shutil.which("python")
    return found or "python"


def _repo_root(cfg: RcloneConfig) -> str:
    """Directory the scheduled command should cd into before running sync.

    Prefers the canonical root carried on the config object (derived from the
    code location at load time). The legacy fallback derives it from
    ``cfg.local_path`` -- correct ONLY for the flat ``.../data/corpus`` layout:
    a local_path nested below data/corpus would resolve to ``repo/data``
    instead of the repo (codex finding), so the depth-based derivation is
    never used when the canonical root is available.
    """
    canonical = getattr(cfg, "repo_root", None)
    if canonical:
        return str(canonical)
    corpus = os.path.abspath(cfg.local_path)
    repo_root = os.path.dirname(os.path.dirname(corpus))  # .../data/corpus -> repo
    return repo_root


def _bat_quote(s: str) -> str:
    """Quote a path for safe literal use inside a cmd.exe ``.bat`` line.

    Wraps in double quotes (so spaces, ``&``, ``(``, ``)`` are inert) and doubles
    every ``%`` so a segment like ``%TEMP%`` is not expanded as an environment
    variable when the scheduled task runs (codex #592: a naive ``f'"{path}"'``
    let ``%VAR%`` expansion and unbalanced quoting through). Windows filenames
    cannot contain a literal ``"``, so no inner-quote escaping is needed; our
    ``.bat`` leaves delayed expansion off, so ``!`` stays literal too.
    """
    return '"' + s.replace("%", "%%") + '"'


def _cron_escape_command(cmd: str) -> str:
    """Escape crontab(5) command-field specials so paths with ``%`` stay intact.

    On POSIX the installed line is ``MIN HOUR * * * <command> # tag``. Before
    the shell ever sees ``<command>``, crontab treats an unescaped ``%`` as a
    newline and feeds everything after it as stdin — silently truncating a
    schedule whose repo or ``--config`` path contains ``%`` (Windows ``.bat``
    already doubles ``%`` via :func:`_bat_quote`; this is the POSIX twin).
    Backslash-escaped ``\\%`` is the documented fix.
    """
    return cmd.replace("%", r"\%")


def _sync_command(cfg: RcloneConfig) -> str:
    """The actual command the scheduler will invoke.

    cd into the repo root (so ``config.yaml`` resolves correctly), then run
    ``python -m sync.cli sync`` as a separate process, carrying the loaded
    config's identity via ``--config`` so a schedule installed with a custom
    config keeps reading THAT file.

    POSIX: this string IS the cron line, so every operator-influenced token is
    ``shlex.quote``-d -- a repo path or config path containing spaces or shell
    metacharacters (``$()``, backticks, ``;``, ``&``) becomes a single inert
    argument that cannot break out of the command (codex #592).

    Windows: the scheduler does NOT register this string -- see
    ``_write_windows_launcher`` and ``WindowsTaskScheduler.install``. A full
    ``cmd /c`` string through ``schtasks /TR`` is quote-fragile, so a ``.bat``
    launcher is used instead; this string is kept only for status output.
    """
    py = _python_executable()
    root = _repo_root(cfg)
    cfg_path = getattr(cfg, "_config_path", None)
    if platform.system() == "Windows":
        config_arg = f"--config {_bat_quote(cfg_path)} " if cfg_path else ""
        return f'cmd /c "cd /d {_bat_quote(root)} && {_bat_quote(py)} -m sync.cli {config_arg}sync"'
    # env(1) prefix so the canonical telemetry/update-check block exists
    # BEFORE the interpreter starts -- cron hands a job a near-empty
    # environment, and sync/__init__.py's import-time apply (the second
    # layer) cannot run earlier than the interpreter itself. Empty values
    # ("K=") are valid for env(1) and deliberate for the two blank
    # CHROMA_OTEL_* names. The -u unsets come FIRST: a positive assignment
    # cannot REMOVE an inherited scrubbed name (a crontab-file
    # OTEL_CONFIG_FILE would otherwise survive until Python-level scrub,
    # after any sitecustomize hook), and -u is supported by both GNU and BSD
    # env (Codex P1).
    env_unsets = [arg for name in SCRUBBED_ENV_KEYS for arg in ("-u", name)]
    env_pairs = [shlex.quote(f"{k}={v}") for k, v in scheduler_env_overlay().items()]
    tokens = ["cd", shlex.quote(root), "&&", "env", *env_unsets, *env_pairs, shlex.quote(py), "-m", "sync.cli"]
    if cfg_path:
        tokens += ["--config", shlex.quote(cfg_path)]
    tokens.append("sync")
    return " ".join(tokens)


def _write_windows_launcher(cfg: RcloneConfig) -> str:
    """Write a ``.bat`` launcher for the scheduled sync and return its path.

    Registering a path to a one-line batch file via ``schtasks /TR`` avoids the
    fragile quoting of embedding a full ``cmd /c`` command string. Every path in
    the file is ``_bat_quote``-d: quoted against spaces and ``%``-doubled so no
    path segment is reinterpreted as an environment variable at run time
    (codex #592).
    """
    root = _repo_root(cfg)
    py = _python_executable()
    bat_dir = cfg.log_dir or root
    os.makedirs(bat_dir, exist_ok=True)
    bat_path = os.path.join(bat_dir, "cyclaw_sync.bat")
    cfg_path = getattr(cfg, "_config_path", None)
    config_arg = f"--config {_bat_quote(cfg_path)} " if cfg_path else ""
    # CRLF line endings + _bat_quote so paths with spaces or % are safe.
    # The set-lines deliver the canonical telemetry/update-check block before
    # the interpreter starts. Task Scheduler jobs DO inherit machine/user
    # environment values, so the scrub names are explicitly DELETED first:
    # cmd's `set "NAME="` deletes a variable rather than setting it empty --
    # the desired state for every scrubbed name, and equivalent for the two
    # blank CHROMA_OTEL_* names in the overlay (the child re-blanks those at
    # import).
    scrub_lines = "".join(f'set "{name}="\r\n' for name in SCRUBBED_ENV_KEYS)
    env_lines = scrub_lines + "".join(
        f'set "{name}={value.replace("%", "%%")}"\r\n'
        for name, value in scheduler_env_overlay().items()
    )
    content = (
        "@echo off\r\n"
        f"{env_lines}"
        f"cd /d {_bat_quote(root)}\r\n"
        f"{_bat_quote(py)} -m sync.cli {config_arg}sync\r\n"
    )
    with open(bat_path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return bat_path


def _frequency_drift_note(cfg: RcloneConfig, backend: str) -> str:
    """Operator-facing note when a configured non-daily frequency is not honored.

    Only LaunchdScheduler maps weekly/monthly into the installed job; the cron
    line and the schtasks registration both hardcode a daily run (documented on
    the schedule_frequency field in sync/config.py). Without this note,
    cmd_schedule/cmd_status print the CONFIGURED frequency as if it were live
    while the installed job actually fires daily.
    """
    if getattr(cfg, "schedule_frequency", "daily") == "daily":
        return ""
    return (
        f"sync.schedule_frequency is {cfg.schedule_frequency!r}, but the {backend} backend installs a "
        "DAILY job at the configured time -- weekly/monthly is honored only by "
        "sync.scheduler_backend: launchd (macOS)."
    )


# ---------------------------------------------------------------------------
# Linux / macOS -- cron
# ---------------------------------------------------------------------------


class CronScheduler:
    """Manage a single CyClaw cron entry via ``crontab -l`` / ``crontab -``."""

    def __init__(self, cfg: RcloneConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def _crontab_bin() -> str:
        path = shutil.which("crontab")
        if not path:
            raise SchedulerError(
                "crontab not available on this system",
                details={"hint": "Install cron, or schedule via a systemd --user timer manually."},
            )
        return path

    # crontab interactions: avoid -e (editor); use stdin piping.
    def _read_crontab(self) -> str:
        crontab = self._crontab_bin()
        try:
            result = subprocess.run(  # noqa: S603  # argv list, crontab resolved via shutil.which
                [crontab, "-l"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SchedulerError(
                "crontab not available on this system",
                details={"hint": "Install cron or schedule via a systemd --user timer manually."},
            ) from exc
        except subprocess.TimeoutExpired as exc:
            # A wedged crontab must surface through the typed hierarchy like the
            # schtasks paths: a raw TimeoutExpired escapes main()'s typed-error
            # mapping and exits 1, colliding with EXIT_SAFETY (max-delete fuse).
            raise SchedulerError(f"crontab -l timed out: {exc}") from exc
        # `crontab -l` returns 1 when the user has no crontab -- not an error.
        if result.returncode not in (0, 1):
            raise SchedulerError(
                f"crontab -l failed (rc={result.returncode}): {result.stderr.strip()}",
                details={"stderr": result.stderr[:500]},
            )
        return result.stdout or ""

    def _write_crontab(self, content: str) -> None:
        crontab = self._crontab_bin()
        try:
            proc = subprocess.run(  # noqa: S603  # argv list, crontab resolved via shutil.which
                [crontab, "-"],
                input=content,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SchedulerError("crontab binary not available") from exc
        except subprocess.TimeoutExpired as exc:
            # Same typed-hierarchy rule as _read_crontab: never let a raw
            # timeout escape as exit 1 (EXIT_SAFETY) via main().
            raise SchedulerError(f"crontab write timed out: {exc}") from exc
        if proc.returncode != 0:
            raise SchedulerError(
                f"crontab write failed (rc={proc.returncode}): {proc.stderr.strip()}",
                details={"stderr": proc.stderr[:500]},
            )

    def _our_line(self) -> str:
        """The single tagged cron line we want active.

        Command-field ``%`` is escaped for crontab(5) (see
        :func:`_cron_escape_command`); the shell-facing argv quoting stays in
        :func:`_sync_command`.
        """
        cmd = _cron_escape_command(_sync_command(self.cfg))
        return f"{self.cfg.schedule_min} {self.cfg.schedule_hour} * * * {cmd} # {TASK_TAG}"

    def install(self) -> ScheduleEntry:
        """Add or replace the CyClaw cron entry (idempotent)."""
        current = self._read_crontab().splitlines()
        # Strip any existing CyClaw entries, then append exactly one fresh line.
        filtered = [ln for ln in current if not _is_managed_cron_line(ln)]
        line = self._our_line()
        filtered.append(line)
        new_content = "\n".join(filtered) + "\n"
        self._write_crontab(new_content)
        return ScheduleEntry(
            platform_name=platform.system().lower(),
            command=_sync_command(self.cfg),
            cron_or_time=f"{self.cfg.schedule_min} {self.cfg.schedule_hour} * * *",
            raw=line,
            note=_frequency_drift_note(self.cfg, "cron"),
        )

    def remove(self) -> bool:
        """Remove any CyClaw cron entries. Returns True if anything was removed."""
        current = self._read_crontab().splitlines()
        filtered = [ln for ln in current if not _is_managed_cron_line(ln)]
        if len(filtered) == len(current):
            return False
        new_content = "\n".join(filtered) + ("\n" if filtered else "")
        self._write_crontab(new_content)
        return True

    def status(self) -> ScheduleEntry | None:
        """Return the active entry if installed, else None."""
        for ln in self._read_crontab().splitlines():
            if not ln.lstrip().startswith("#") and _is_managed_cron_line(ln):
                # Expected shape: "MIN HOUR * * * cmd # TAG"
                parts = ln.split(maxsplit=5)
                if len(parts) >= 6:
                    cron_expr = " ".join(parts[:5])
                    return ScheduleEntry(
                        platform_name=platform.system().lower(),
                        command=parts[5].rsplit("#", 1)[0].strip(),
                        cron_or_time=cron_expr,
                        raw=ln,
                        note=_frequency_drift_note(self.cfg, "cron"),
                    )
        return None


# ---------------------------------------------------------------------------
# macOS -- launchd (Darwin-only; opt-in via sync.scheduler_backend: "launchd")
# ---------------------------------------------------------------------------

_WEEKDAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # index 0-7, 7 aliases Sunday


def _launchd_program_arguments(cfg: RcloneConfig) -> list[str]:
    """The argv launchd execs directly -- no shell, so no quoting is needed.

    Unlike ``_sync_command`` (a single string fed through a POSIX shell via
    cron), launchd's ``ProgramArguments`` is an array exec'd directly by the
    kernel: every element is already an inert, literal argument. Mirrors
    ``_sync_command``'s content (cd is unnecessary -- ``WorkingDirectory``
    covers it) so both backends invoke the identical CLI surface.
    """
    argv = [_python_executable(), "-m", "sync.cli"]
    cfg_path = getattr(cfg, "_config_path", None)
    if cfg_path:
        argv += ["--config", cfg_path]
    argv.append("sync")
    return argv


def _launchd_calendar_interval(cfg: RcloneConfig) -> dict[str, int]:
    """Build the StartCalendarInterval dict for cfg.schedule_frequency.

    daily: Hour/Minute only (fires every day). weekly: adds Weekday (launchd's
    own 0-or-7-is-Sunday convention -- see sync.config's schedule_weekday
    validation). monthly: adds Day (1-31; launchd simply does not fire in a
    month shorter than the configured day, e.g. Day=31 in April -- documented
    upstream launchd behavior, not a CyClaw bug).
    """
    interval: dict[str, int] = {"Hour": cfg.schedule_hour, "Minute": cfg.schedule_min}
    if cfg.schedule_frequency == "weekly":
        interval["Weekday"] = cfg.schedule_weekday
    elif cfg.schedule_frequency == "monthly":
        interval["Day"] = cfg.schedule_day
    return interval


def _launchd_human_schedule(interval: dict[str, int]) -> str:
    """Human-readable schedule summary, derived from a StartCalendarInterval dict.

    Takes the interval dict itself (not the live RcloneConfig) so status()
    reports what is actually written on disk, not whatever config.yaml
    happens to say right now -- the two can legitimately diverge if config.yaml
    changed since the last `sync.cli schedule` run and install() hasn't been
    re-run to pick it up.
    """
    time_str = f"{interval.get('Hour', 0):02d}:{interval.get('Minute', 0):02d}"
    if "Weekday" in interval:
        weekday = interval["Weekday"]
        # The interval dict can come from an operator-EDITED plist on disk
        # (status() re-reads the file), so the validated 0-7 range from
        # sync.config does not apply here -- index defensively.
        name = (
            _WEEKDAY_NAMES[weekday]
            if isinstance(weekday, int) and not isinstance(weekday, bool) and 0 <= weekday <= 7
            else f"weekday {weekday!r}"
        )
        return f"weekly {name} {time_str}"
    if "Day" in interval:
        return f"monthly day {interval['Day']} {time_str}"
    return f"daily {time_str}"


class LaunchdScheduler:
    """Manage a single CyClaw launchd LaunchAgent via a generated plist.

    Darwin-only (enforced by :func:`get_scheduler`, and defensively re-checked
    in every method here). Writes ``~/Library/LaunchAgents/<LAUNCHD_LABEL>.plist``
    from real, resolved install paths -- never a ``REPLACE_*`` template an
    operator must hand-edit. Never calls ``launchctl load``/``bootstrap``
    itself: loading a persistent background agent is left to an explicit
    operator step (the exact command is returned in ``ScheduleEntry.note``),
    matching the same "generate, don't auto-enroll" posture as the shipped
    ``macos/LaunchAgents/*.plist`` templates, just without the manual
    placeholder-editing they require.
    """

    def __init__(self, cfg: RcloneConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def _require_darwin() -> None:
        if platform.system() != "Darwin":
            raise SchedulerError(
                "LaunchdScheduler is Darwin-only",
                details={"platform": platform.system().lower()},
            )

    @staticmethod
    def _agents_dir() -> Path:
        return Path.home() / "Library" / "LaunchAgents"

    @staticmethod
    def _log_dir() -> Path:
        return Path.home() / "Library" / "Logs" / "CyClaw"

    @classmethod
    def _plist_path(cls) -> Path:
        return cls._agents_dir() / f"{LAUNCHD_LABEL}.plist"

    @staticmethod
    def _launchctl() -> str | None:
        """Best-effort launchctl lookup; None (not an error) when absent.

        Unlike CronScheduler/WindowsTaskScheduler's binary lookups (which
        raise), a missing launchctl must not block install() from writing the
        plist -- the file is useful evidence/state even before the operator's
        own launchctl bootstrap step. remove()/status() degrade to
        file-only behavior when this returns None.
        """
        return shutil.which("launchctl")

    @staticmethod
    def _uid() -> int:
        """POSIX uid, or 0 as an inert placeholder on a non-POSIX Python.

        This class only ever runs for real on Darwin (every public method
        calls _require_darwin() first), where os.getuid always exists. The
        fallback exists solely so this class's own tests -- which mock
        platform.system() to "Darwin" but still execute on whatever
        interpreter CI actually is -- don't crash: CPython's os module omits
        getuid entirely on Windows, independent of what platform.system() is
        mocked to return (confirmed via CI: AttributeError, not a permission
        or value problem).
        """
        return os.getuid() if hasattr(os, "getuid") else 0

    def _bootstrap_hint(self, plist_path: Path) -> str:
        return f"launchctl bootstrap gui/{self._uid()} {plist_path}"

    def install(self) -> ScheduleEntry:
        """Write (or overwrite) the CyClaw sync LaunchAgent plist.

        Idempotent: re-running replaces the file in place. Never loads the
        agent into launchd -- see the class docstring. No secret/token is
        embedded: the `sync` job needs none (rclone's own OAuth state lives
        under ~/.config/rclone, untouched by this plist).
        """
        self._require_darwin()
        agents_dir = self._agents_dir()
        log_dir = self._log_dir()
        agents_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)

        log_path = str(log_dir / "sync.log")
        interval = _launchd_calendar_interval(self.cfg)
        document = {
            "Label": LAUNCHD_LABEL,
            "WorkingDirectory": _repo_root(self.cfg),
            "ProgramArguments": _launchd_program_arguments(self.cfg),
            # launchd hands a job a near-empty environment; deliver the
            # canonical telemetry/update-check block before the interpreter
            # starts. Non-secret by construction (fixed literals only).
            "EnvironmentVariables": scheduler_env_overlay(),
            "StartCalendarInterval": interval,
            "RunAtLoad": False,
            "StandardOutPath": log_path,
            "StandardErrorPath": log_path,
        }

        plist_path = self._plist_path()
        tmp_path = plist_path.with_suffix(".plist.tmp")
        with open(tmp_path, "wb") as f:
            plistlib.dump(document, f, fmt=plistlib.FMT_XML)
        os.replace(tmp_path, plist_path)  # atomic on POSIX -- never a partial plist on disk

        return ScheduleEntry(
            platform_name="darwin",
            command=" ".join(_launchd_program_arguments(self.cfg)),
            cron_or_time=_launchd_human_schedule(interval),
            raw=str(plist_path),
            note=(
                f"Plist written to {plist_path} but NOT loaded. Run "
                f"'{self._bootstrap_hint(plist_path)}' to activate it."
            ),
        )

    def remove(self) -> bool:
        """Best-effort unload, then delete the plist. Returns True if a plist was removed."""
        self._require_darwin()
        plist_path = self._plist_path()
        if not plist_path.exists():
            return False

        launchctl = self._launchctl()
        if launchctl:
            # Tolerate "not loaded"/"no such process" -- bootout on an agent
            # that was written but never bootstrapped is an expected no-op,
            # not a failure; we only need the file gone afterward either way.
            try:
                subprocess.run(  # noqa: S603  # argv list, launchctl resolved via shutil.which
                    [launchctl, "bootout", f"gui/{self._uid()}", str(plist_path)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                # Best-effort unload: a wedged launchctl (timeout) must not
                # block the plist removal below, which is the part remove()
                # is contracted to do.
                logger.warning("launchctl bootout failed (proceeding to delete plist): %s", exc)
        plist_path.unlink()
        return True

    def status(self) -> ScheduleEntry | None:
        """Return the on-disk entry if the plist exists, else None.

        Best-effort notes whether launchd currently has it loaded; never
        raises when launchctl is absent or the probe fails (mirrors
        sync/cli.py's existing tolerance for a scheduler status read that
        can't fully resolve on this host).
        """
        self._require_darwin()
        plist_path = self._plist_path()
        if not plist_path.exists():
            return None

        try:
            with open(plist_path, "rb") as f:
                document = plistlib.load(f)
        except (OSError, ValueError) as exc:
            # The plist is operator-editable state, not our own output: a
            # truncated or malformed file must surface through the typed
            # hierarchy (cmd_status catches SchedulerError and warns) rather
            # than as a raw plistlib.InvalidFileException traceback.
            raise SchedulerError(
                f"could not parse launchd plist {plist_path}: {exc}",
                details={"plist": str(plist_path)},
            ) from exc

        loaded_note = "load state unknown (launchctl unavailable)"
        launchctl = self._launchctl()
        if launchctl:
            probe = subprocess.run(  # noqa: S603  # argv list, launchctl resolved via shutil.which
                [launchctl, "print", f"gui/{self._uid()}/{LAUNCHD_LABEL}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            loaded_note = "loaded" if probe.returncode == 0 else "not loaded"

        interval = document.get("StartCalendarInterval", {})
        return ScheduleEntry(
            platform_name="darwin",
            command=" ".join(document.get("ProgramArguments", [])),
            cron_or_time=_launchd_human_schedule(interval),
            raw=str(plist_path),
            note=loaded_note,
        )


# ---------------------------------------------------------------------------
# Windows -- schtasks
# ---------------------------------------------------------------------------


class WindowsTaskScheduler:
    """Manage a single CyClaw task via schtasks.exe."""

    def __init__(self, cfg: RcloneConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def _schtasks() -> str:
        path = shutil.which("schtasks")
        if not path:
            raise SchedulerError(
                "schtasks.exe not available on PATH",
                details={"hint": "schtasks is a built-in Windows tool. Run on Windows, not WSL."},
            )
        return path

    def install(self) -> ScheduleEntry:
        schtasks = self._schtasks()
        # Register a .bat launcher path (robust) rather than an inline cmd /c
        # string (quote-fragile through schtasks /TR for paths with spaces).
        launcher = _write_windows_launcher(self.cfg)
        time_str = f"{self.cfg.schedule_hour:02d}:{self.cfg.schedule_min:02d}"
        argv = [
            schtasks,
            "/Create",
            "/TN",
            WINDOWS_TASK_NAME,
            "/TR",
            launcher,
            "/SC",
            "DAILY",
            "/ST",
            time_str,
            "/F",  # force overwrite of an existing task with the same name
            "/RL",
            "LIMITED",
        ]
        try:
            proc = subprocess.run(  # noqa: S603  # argv list, schtasks resolved via shutil.which
                argv, capture_output=True, text=True, timeout=15, check=False
            )
        except subprocess.SubprocessError as exc:
            raise SchedulerError(f"schtasks /Create failed: {exc}") from exc
        if proc.returncode != 0:
            raise SchedulerError(
                f"schtasks /Create failed (rc={proc.returncode}): {proc.stderr.strip()}",
                details={"stderr": proc.stderr[:500]},
            )
        return ScheduleEntry(
            platform_name="windows",
            command=launcher,
            cron_or_time=time_str,
            raw=proc.stdout.strip(),
            note=_frequency_drift_note(self.cfg, "schtasks"),
        )

    def remove(self) -> bool:
        argv = [self._schtasks(), "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"]
        try:
            proc = subprocess.run(  # noqa: S603  # argv list, schtasks resolved via shutil.which
                argv, capture_output=True, text=True, timeout=15, check=False
            )
        except subprocess.SubprocessError as exc:
            raise SchedulerError(f"schtasks /Delete failed: {exc}") from exc
        if proc.returncode == 0:
            return True
        # schtasks /Delete returns nonzero when the task didn't exist -- treat
        # "not found" as a no-op (False), never an error.
        combined = proc.stdout + proc.stderr
        if "cannot find the file specified" in combined or "does not exist" in combined.lower():
            return False
        if proc.returncode == 1:
            return False
        raise SchedulerError(
            f"schtasks /Delete failed (rc={proc.returncode}): {proc.stderr.strip()}",
            details={"stderr": proc.stderr[:500]},
        )

    def status(self) -> ScheduleEntry | None:
        argv = [self._schtasks(), "/Query", "/TN", WINDOWS_TASK_NAME, "/FO", "LIST"]
        try:
            proc = subprocess.run(  # noqa: S603  # argv list, schtasks resolved via shutil.which
                argv, capture_output=True, text=True, timeout=15, check=False
            )
        except subprocess.SubprocessError as exc:
            logger.warning("schtasks /Query failed: %s", exc)
            return None
        if proc.returncode != 0:
            combined = (proc.stdout or "") + (proc.stderr or "")
            # "not found" is the normal absent-task case; do not warn about it.
            if "cannot find the file specified" not in combined.lower() and "does not exist" not in combined.lower():
                logger.warning("schtasks /Query returned rc=%s: %s", proc.returncode, combined[:500].strip())
            return None
        return ScheduleEntry(
            platform_name="windows",
            command=_sync_command(self.cfg),
            cron_or_time=f"{self.cfg.schedule_hour:02d}:{self.cfg.schedule_min:02d}",
            raw=proc.stdout.strip(),
            note=_frequency_drift_note(self.cfg, "schtasks"),
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_scheduler(cfg: RcloneConfig) -> CronScheduler | WindowsTaskScheduler | LaunchdScheduler:
    """Return the right scheduler for the current OS and ``cfg.scheduler_backend``.

    ``scheduler_backend`` (default ``"cron"``, set via ``sync.scheduler_backend``
    in config.yaml) selects between the two Darwin-capable backends:
    "cron" -> CronScheduler (linux/darwin, unchanged default -- existing
    operators see zero behavior change), "launchd" -> LaunchdScheduler
    (darwin only; raises SchedulerError if selected on any other platform,
    rather than silently falling back to cron). Windows always gets
    WindowsTaskScheduler regardless of scheduler_backend. Any other platform
    raises SchedulerError.
    """
    sys_name = platform.system().lower()
    backend = getattr(cfg, "scheduler_backend", "cron")
    if backend == "launchd":
        if sys_name != "darwin":
            raise SchedulerError(
                f"sync.scheduler_backend: 'launchd' requires macOS (darwin); detected {sys_name}",
                details={"platform": sys_name, "scheduler_backend": backend},
            )
        return LaunchdScheduler(cfg)
    if sys_name == "windows":
        return WindowsTaskScheduler(cfg)
    if sys_name in ("linux", "darwin"):
        return CronScheduler(cfg)
    raise SchedulerError(
        f"Unsupported platform for scheduling: {sys_name}",
        details={"platform": sys_name, "supported": ["linux", "darwin", "windows"]},
    )
