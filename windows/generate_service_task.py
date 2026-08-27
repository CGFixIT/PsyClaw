#!/usr/bin/env python3
"""Generate (never register) a supervised Task Scheduler job for gate.py or
the coding harness. Windows-only.

READ THIS FIRST -- the highest-risk of CyClaw's Windows task generators
(twin of macos/generate_service_plist.py / PR #912): a logon-triggered
task with RestartOnFailure turns gate.py or the harness into an
ALWAYS-RUNNING, AUTO-RESTARTING network listener. That survives logout,
reboot, and process crashes. This script does not judge whether that
posture is appropriate; it only writes XML + a .cmd launcher, and —
unlike the trash/Telegram generators — refuses to do even that without
an explicit --confirm and a non-empty --reason.

Usage:
  python windows/generate_service_task.py --service gate \\
      --reason "keep the RAG server up across reboots" --confirm
  python windows/generate_service_task.py --service harness \\
      --reason "keep the coding console up across reboots" --confirm
  python windows/generate_service_task.py --service gate \\
      --api-key-target com.cgfixit.cyclaw.api-key \\
      --reason "..." --confirm

Standalone script (no CyClaw package import beyond the stdlib-only
utils.win_schtasks helper) -- reads api.port directly from config.yaml
via PyYAML so it never imports gate.py, graph.py, or mcp_hybrid_server.py
(I6). Never wired into any HTTP route.

Never calls schtasks /Create itself.
"""

from __future__ import annotations

import argparse
import platform
import sys
import types
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils import win_schtasks  # noqa: E402
from utils.telemetry_kill import scheduler_env_overlay  # noqa: E402

_TASK_NAMES = types.MappingProxyType({
    "gate": "CyClaw gate",
    "harness": "CyClaw harness",
})
_DEFAULT_HARNESS_PORT = 8790
_DEFAULT_THROTTLE_SEC = 30

_RISK_TEXT = """
This writes a Task Scheduler XML that, once you separately register it,
makes this service an ALWAYS-ON, AUTO-RESTARTING background listener:
  - starts at logon / after every reboot (LogonTrigger)
  - restarts itself if it crashes (RestartOnFailure, throttled)
  - keeps running even if you close every terminal window

That is a real change to this machine's security/availability posture, not
just a convenience. Read docs/work/MACOS_LAUNCHD_INTEGRATION_PLAN.md and
docs/THREAT_MODEL.md first. Re-run with --confirm and a non-empty --reason
once you have.
""".strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python windows/generate_service_task.py",
        description=(
            "Generate (never register) a supervised Task Scheduler job "
            "for gate.py or the harness. Windows-only."
        ),
    )
    parser.add_argument("--service", choices=sorted(_TASK_NAMES), required=True)
    parser.add_argument(
        "--config",
        default=str(_REPO_ROOT / "config.yaml"),
        help="Path to config.yaml, read for api.port (gate only; default: repo config.yaml).",
    )
    parser.add_argument(
        "--api-key-target",
        default="",
        help="Optional Credential Manager target holding CYCLAW_API_KEY (unset: not injected).",
    )
    parser.add_argument(
        "--throttle-sec",
        type=int,
        default=_DEFAULT_THROTTLE_SEC,
        help="Minimum seconds between restart attempts (default: %(default)s).",
    )
    parser.add_argument("--reason", default="", help="Required: why this service should be supervised.")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required: acknowledges the always-on/auto-restart posture change before writing XML.",
    )
    return parser


def _read_gate_port(config_path: Path) -> int:
    try:
        with open(config_path, encoding="utf-8") as config_file:
            doc = yaml.safe_load(config_file) or {}
    except OSError as exc:
        print(f"error: could not read {config_path}: {exc}", file=sys.stderr)
        sys.exit(3)
    api_cfg = doc.get("api") if isinstance(doc, dict) else None
    if not isinstance(api_cfg, dict):
        return 8787
    if "port" not in api_cfg:
        return 8787
    raw = api_cfg["port"]
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        print(f"error: api.port must be an integer, got {raw!r}", file=sys.stderr)
        sys.exit(3)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        print(f"error: api.port must be an integer, got {raw!r}", file=sys.stderr)
        sys.exit(3)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if platform.system() != "Windows":
        print("error: this generator is Windows-only (writes Task Scheduler XML).", file=sys.stderr)
        return 3

    if not args.confirm or not args.reason.strip():
        print(_RISK_TEXT, file=sys.stderr)
        missing = []
        if not args.confirm:
            missing.append("--confirm")
        if not args.reason.strip():
            missing.append("--reason")
        print(f"\nRefusing to write a task without: {', '.join(missing)}", file=sys.stderr)
        return 1

    if args.throttle_sec <= 0:
        print("error: --throttle-sec must be > 0", file=sys.stderr)
        return 2

    task_name = _TASK_NAMES[args.service]
    # Canonical telemetry/update-check block first: Task Scheduler starts the
    # job from a near-empty environment, and the generated .cmd's set-lines
    # are the only env channel (Task XML has none). cmd's `set "NAME="`
    # DELETES a var -- deliberate for the two blank CHROMA_OTEL_* names (see
    # utils/win_schtasks.write_cmd_launcher).
    env: dict[str, str] = dict(scheduler_env_overlay())

    if args.service == "gate":
        port = _read_gate_port(Path(args.config).resolve())
        inner_argv = [win_schtasks.python_executable(), str(_REPO_ROOT / "gate.py")]
    else:
        port = _DEFAULT_HARNESS_PORT
        inner_argv = [win_schtasks.python_executable(), "-m", "harness.server"]
        env["CYCLAW_HOME"] = str(Path.home() / ".CyClaw")
        env["CYCLAW_REPO"] = str(_REPO_ROOT)

    secrets: list[tuple[str, str]] = []
    if args.api_key_target:
        secrets.append((args.api_key_target, "CYCLAW_API_KEY"))
    wrapper = win_schtasks.credman_wrapper_path(_REPO_ROOT)
    argv_out = win_schtasks.wrap_with_credman_secrets(inner_argv, secrets, wrapper)

    path, _launcher = win_schtasks.write_generated_task(
        task_name=task_name,
        argv=argv_out,
        working_directory=str(_REPO_ROOT),
        triggers=win_schtasks.logon_trigger(),
        restart_interval=f"PT{args.throttle_sec}S",
        restart_count=5,
        execution_time_limit="PT0S",
        env=env or None,
    )

    print(f"Wrote {path}")
    print(f"  service:        {args.service} (port {port})")
    print(f"  reason:         {args.reason.strip()}")
    print(f"  restart policy: crash-only RestartOnFailure, max 5 restarts, throttled to {args.throttle_sec}s")
    print()
    if args.service == "gate" and args.config != str(_REPO_ROOT / "config.yaml"):
        print("  WARNING: --config was passed, but gate.py always loads config.yaml from")
        print(f"  the repo root, NOT from {args.config}. Verify config.yaml has the correct")
        print("  port and settings before registering the task.")
        print()
    print("  This makes the service ALWAYS-ON: it survives logout/reboot and")
    print("  auto-restarts on crash. To stop it for good, use")
    print(f'  schtasks /Delete /TN "{task_name}" /F')
    print(f"  NOT registered. Run to activate: {win_schtasks.register_hint(task_name, path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
