"""Stdlib-only Windows Task Scheduler XML helpers.

Shared by every out-of-band package (``sync`` callers stay on the existing
live ``WindowsTaskScheduler``; ``agentic.fsconnect``, ``telegram``, and
``windows/generate_service_task.py`` generate XML here) that writes a
resolved scheduled-task document instead of a ``REPLACE_*`` template.

Never imported by ``gate.py``/``graph.py``/``mcp_hybrid_server.py`` (I6).
Never calls ``schtasks /Create`` — :func:`register_hint` returns the command
an operator must run by hand.

No secrets in the file. Use :func:`wrap_with_credman_secrets` so a token is
resolved at process-start time by ``powershell/CyClaw-CredMan-Env.ps1``.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import escape

_CREDMAN_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

CREDMAN_WRAPPER_RELATIVE_PATH = "powershell/CyClaw-CredMan-Env.ps1"

# launchd Weekday 0/7 = Sunday … 6 = Saturday. Task Scheduler uses English
# day element names under ScheduleByWeek/DaysOfWeek.
_WEEKDAY_XML = {
    0: "Sunday",
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
    7: "Sunday",
}

# A Sunday in the past so StartBoundary + DaysOfWeek is well-formed regardless
# of when the XML is generated. Task Scheduler requires StartBoundary.
_SUNDAY_ANCHOR = "2026-01-04"


def python_executable() -> str:
    """Best-guess python interpreter for a generated task (mirrors launchd_plist)."""
    candidate = sys.executable or "python"
    if candidate and os.path.isfile(candidate):
        return candidate
    found = shutil.which("python") or shutil.which("python3")
    return found or "python"


def powershell_executable() -> str:
    """powershell.exe (Windows PowerShell 5.1) or pwsh, for the CredMan wrapper."""
    found = shutil.which("powershell") or shutil.which("pwsh")
    return found or "powershell.exe"


def tasks_dir() -> Path:
    """``~/.CyClaw/tasks`` — generated XML + .cmd launchers."""
    path = Path.home() / ".CyClaw" / "tasks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    """``~/.CyClaw/logs`` — Windows twin of ``~/Library/Logs/CyClaw``."""
    path = Path.home() / ".CyClaw" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug(task_name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in task_name).strip("-")


def xml_path(task_name: str) -> Path:
    return tasks_dir() / f"{_slug(task_name)}.xml"


def cmd_path(task_name: str) -> Path:
    return tasks_dir() / f"{_slug(task_name)}.cmd"


def bat_quote(s: str) -> str:
    """Quote a token for a ``.cmd`` line (spaces + doubled ``%``)."""
    if "\r" in s or "\n" in s:
        raise ValueError("token must not contain CR/LF")
    return '"' + s.replace("%", "%%") + '"'


def write_cmd_launcher(
    path: Path, argv: list[str], env: dict[str, str] | None = None
) -> None:
    """Atomically write a one-shot ``.cmd`` that execs *argv* with quoted tokens.

    Optional *env* becomes ``set "NAME=value"`` lines (non-secret only —
    callers must not pass tokens here).

    cmd.exe semantics for an EMPTY value: ``set "NAME="`` DELETES the variable
    rather than setting it to an empty string — there is no way to express an
    empty-but-present variable in a ``.cmd``. That is accepted deliberately
    for the two blank ``CHROMA_OTEL_*`` names in the canonical telemetry
    overlay (utils/telemetry_kill.scheduler_env_overlay): absent is exactly
    the state their scrub wants, the real switch is
    ``CHROMA_OTEL_GRANULARITY=none`` (non-empty, delivered normally), and the
    Python child re-blanks both at import. Do not "fix" this by skipping
    empty values — the deletion line is a real directive against an ambient
    machine-level value.
    """
    if not argv:
        raise ValueError("argv must be non-empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["@echo off"]
    for name, value in (env or {}).items():
        if not name.isidentifier() or not name.isascii():
            raise ValueError(f"refusing invalid env name: {name!r}")
        if '"' in value or "\n" in value or "\r" in value:
            raise ValueError(f"refusing env value with quotes/newlines: {name}")
        lines.append(f'set "{name}={value.replace("%", "%%")}"')
    lines.append(" ".join(bat_quote(part) for part in argv))
    content = "\r\n".join(lines) + "\r\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content.encode("utf-8"))
    os.replace(tmp, path)


def register_hint(task_name: str, path: Path) -> str:
    """The ``schtasks /Create /XML`` command an operator must run by hand."""
    return f'schtasks /Create /TN "{task_name}" /XML "{path}" /F'


def credman_wrapper_path(repo_root: str | Path) -> str:
    return str(Path(repo_root) / CREDMAN_WRAPPER_RELATIVE_PATH)


def wrap_with_credman_secrets(
    argv: list[str],
    secrets: list[tuple[str, str]],
    wrapper_path: str,
    powershell: str | None = None,
) -> list[str]:
    """Prepend one CredMan-wrapper layer per ``(target, env_var_name)`` pair.

    Each layer is ``powershell -NoProfile -ExecutionPolicy Bypass -File
    <wrapper> <target> <env_var> --``; the innermost layer reaches *argv*.
    An empty *secrets* list returns *argv* unchanged.
    """
    ps = powershell or powershell_executable()
    result = list(argv)
    for target, var_name in reversed(secrets):
        if not _CREDMAN_TOKEN_RE.fullmatch(target):
            raise ValueError(f"CredMan target must match {_CREDMAN_TOKEN_RE.pattern}: {target!r}")
        if not _CREDMAN_TOKEN_RE.fullmatch(var_name):
            raise ValueError(f"CredMan var_name must match {_CREDMAN_TOKEN_RE.pattern}: {var_name!r}")
        result = [
            ps,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            wrapper_path,
            target,
            var_name,
            "--",
            *result,
        ]
    return result


def _settings_xml(
    *,
    restart_interval: str | None,
    restart_count: int,
    execution_time_limit: str,
    allow_demand: bool = True,
) -> str:
    restart = ""
    if restart_interval:
        restart = (
            "    <RestartOnFailure>\n"
            f"      <Interval>{escape(restart_interval)}</Interval>\n"
            f"      <Count>{int(restart_count)}</Count>\n"
            "    </RestartOnFailure>\n"
        )
    demand = "true" if allow_demand else "false"
    return (
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowHardTerminate>true</AllowHardTerminate>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        f"    <AllowStartOnDemand>{demand}</AllowStartOnDemand>\n"
        "    <Enabled>true</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        "    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n"
        "    <WakeToRun>false</WakeToRun>\n"
        f"    <ExecutionTimeLimit>{escape(execution_time_limit)}</ExecutionTimeLimit>\n"
        "    <Priority>7</Priority>\n"
        f"{restart}"
        "  </Settings>\n"
    )


def _exec_xml(command: str, arguments: str, working_directory: str) -> str:
    return (
        "  <Actions Context=\"Author\">\n"
        "    <Exec>\n"
        f"      <Command>{escape(command)}</Command>\n"
        f"      <Arguments>{escape(arguments)}</Arguments>\n"
        f"      <WorkingDirectory>{escape(working_directory)}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
    )


def _envelope(task_name: str, triggers: str, settings: str, actions: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        f"    <Description>{escape('CyClaw generated task: ' + task_name)}</Description>\n"
        "    <URI>\\" + escape(task_name) + "</URI>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        f"{triggers}"
        "  </Triggers>\n"
        "  <Principals>\n"
        "    <Principal id=\"Author\">\n"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        f"{settings}"
        f"{actions}"
        "</Task>\n"
    )


def write_task_xml(path: Path, document: str) -> None:
    """Atomically write UTF-16 LE (BOM) Task Scheduler XML to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(document.encode("utf-16"))
    os.replace(tmp, path)


def weekly_calendar_trigger(weekday: int, hour: int, minute: int) -> str:
    if weekday not in _WEEKDAY_XML:
        raise ValueError("weekday must be 0-7 (0 or 7 = Sunday)")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("hour must be 0-23 and minute must be 0-59")
    day = _WEEKDAY_XML[weekday]
    # Anchor Sunday + weekday offset (7 → 0).
    offset = 0 if weekday == 7 else weekday
    start = f"{_SUNDAY_ANCHOR[:8]}{4 + offset:02d}T{hour:02d}:{minute:02d}:00"
    return (
        "    <CalendarTrigger>\n"
        f"      <StartBoundary>{escape(start)}</StartBoundary>\n"
        "      <Enabled>true</Enabled>\n"
        "      <ScheduleByWeek>\n"
        "        <DaysOfWeek>\n"
        f"          <{day} />\n"
        "        </DaysOfWeek>\n"
        "        <WeeksInterval>1</WeeksInterval>\n"
        "      </ScheduleByWeek>\n"
        "    </CalendarTrigger>\n"
    )


def interval_trigger(interval_sec: int) -> str:
    if interval_sec <= 0:
        raise ValueError("interval_sec must be > 0")
    return (
        "    <TimeTrigger>\n"
        f"      <StartBoundary>{_SUNDAY_ANCHOR}T00:00:00</StartBoundary>\n"
        "      <Enabled>true</Enabled>\n"
        "      <Repetition>\n"
        f"        <Interval>PT{int(interval_sec)}S</Interval>\n"
        "        <StopAtDurationEnd>false</StopAtDurationEnd>\n"
        "      </Repetition>\n"
        "    </TimeTrigger>\n"
    )


def logon_trigger() -> str:
    return (
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        "    </LogonTrigger>\n"
    )


def build_task_xml(
    *,
    task_name: str,
    command: str,
    arguments: str,
    working_directory: str,
    triggers: str,
    restart_interval: str | None = None,
    restart_count: int = 3,
    execution_time_limit: str = "PT4H",
) -> str:
    settings = _settings_xml(
        restart_interval=restart_interval,
        restart_count=restart_count,
        execution_time_limit=execution_time_limit,
    )
    actions = _exec_xml(command, arguments, working_directory)
    return _envelope(task_name, triggers, settings, actions)


def write_generated_task(
    *,
    task_name: str,
    argv: list[str],
    working_directory: str,
    triggers: str,
    restart_interval: str | None = None,
    restart_count: int = 3,
    execution_time_limit: str = "PT4H",
    env: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """Write ``.cmd`` + UTF-16 XML. Returns ``(xml_path, cmd_path)``.

    The XML Exec's Command is ``cmd.exe /c`` the launcher so quoting stays
    inside the ``.cmd`` (same reason ``sync.scheduler`` uses a ``.bat``).
    """
    launcher = cmd_path(task_name)
    write_cmd_launcher(launcher, argv, env=env)
    document = build_task_xml(
        task_name=task_name,
        command=os.environ.get("COMSPEC", "cmd.exe"),
        arguments=f"/c {bat_quote(str(launcher))}",
        working_directory=working_directory,
        triggers=triggers,
        restart_interval=restart_interval,
        restart_count=restart_count,
        execution_time_limit=execution_time_limit,
    )
    path = xml_path(task_name)
    write_task_xml(path, document)
    return path, launcher
