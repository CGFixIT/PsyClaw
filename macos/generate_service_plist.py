#!/usr/bin/env python3
"""Generate (never load) a supervised launchd LaunchAgent for gate.py or the
coding harness. Darwin-only.

READ THIS FIRST -- the highest-risk of CyClaw's launchd generators (see
docs/work/MACOS_LAUNCHD_INTEGRATION_PLAN.md's "Next integrations" section
and docs/THREAT_MODEL.md before using this on anything but a personal,
single-operator deployment): a KeepAlive LaunchAgent turns gate.py or the
harness into an ALWAYS-RUNNING, AUTO-RESTARTING network listener. That is a
materially different availability/security posture than "runs only while a
terminal is open" -- it survives logout, reboot, and process crashes. This
script does not judge whether that posture is appropriate for your
deployment; it only writes the plist file, and -- unlike this repo's other
launchd generators -- refuses to do even that without an explicit --confirm
and a non-empty --reason, mirroring the reason-required gate soul mutations
already use in this codebase (utils/personality.py) for a comparably
consequential action.

Usage:
  python macos/generate_service_plist.py --service gate \\
      --reason "keep the RAG server up across reboots" --confirm
  python macos/generate_service_plist.py --service harness \\
      --reason "keep the coding console up across reboots" --confirm
  python macos/generate_service_plist.py --service gate \\
      --api-key-service com.cgfixit.cyclaw.api-key \\
      --reason "..." --confirm

Standalone script (no CyClaw package import beyond the stdlib-only
utils.launchd_plist helper) -- reads api.host/api.port directly from
config.yaml via PyYAML so it never has any reason to import gate.py,
graph.py, or mcp_hybrid_server.py (I6). Reachable only via this CLI; never
wired into any HTTP route, and never imported by anything under gate.py's
request path.

launchd semantics chosen deliberately conservative:
  - RunAtLoad: true -- starts when the LaunchAgent is loaded (e.g. at login,
    surviving reboot), matching the actual gap this closes ("servers stay
    dead after reboot").
  - KeepAlive: {SuccessfulExit: false} -- restart ONLY on crash / non-zero
    exit, never after a clean stop. Both gate.py and the harness delegate
    to uvicorn.run(), which installs its own SIGTERM handler and returns
    normally (exit 0) on a graceful `launchctl stop`/`bootout` -- verified
    by reading both entry points' main() before writing this generator, not
    assumed -- so a deliberate operator stop is never mistaken for a crash
    and silently relaunched out from under them.
  - ThrottleInterval: 30s default (launchd's own default is 10s) -- gate.py's
    startup (embedding model + ChromaDB) is meaningfully slower than a
    lightweight poller; a longer floor keeps a genuine crash loop from
    hammering CPU/disk with overlapping startup attempts. Override with
    --throttle-sec if you have a specific reason to.
  - Never calls `launchctl load`/`bootstrap` itself -- see the printed
    bootstrap_hint. Loading a persistent, auto-restarting listener is
    always a separate, explicit operator action.

Known limitation (documented, not silently papered over): if the target
port is already held by an independently-started instance, gate.py exits
cleanly (0) via its own pre-flight port check and will NOT be retried by
KeepAlive (by design -- see above, this is not a bug); harness/server.py
has no equivalent pre-check, so a port conflict there raises inside
uvicorn.run() and DOES crash-loop, throttled by --throttle-sec, until the
port frees. Stop any manually-started instance of a service before loading
its supervised agent.
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
    # Standalone script under macos/, not a package -- sys.path[0] is this
    # file's own directory when run directly, so the repo root (needed for
    # `from utils import launchd_plist`) is added explicitly.
    sys.path.insert(0, str(_REPO_ROOT))

from utils import launchd_plist  # noqa: E402

_LABELS = types.MappingProxyType({
    "gate": "com.cgfixit.cyclaw.gate",
    "harness": "com.cgfixit.cyclaw.harness",
})
_DEFAULT_HARNESS_PORT = 8790
_DEFAULT_THROTTLE_SEC = 30

_RISK_TEXT = """
This writes a launchd plist that, once you separately load it, makes this
service an ALWAYS-ON, AUTO-RESTARTING background listener:
  - starts at login / after every reboot (RunAtLoad)
  - restarts itself if it crashes (KeepAlive, throttled)
  - keeps running even if you close every terminal window

That is a real change to this machine's security/availability posture, not
just a convenience. Read docs/work/MACOS_LAUNCHD_INTEGRATION_PLAN.md and
docs/THREAT_MODEL.md first. Re-run with --confirm and a non-empty --reason
once you have.
""".strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python macos/generate_service_plist.py",
        description="Generate (never load) a supervised launchd LaunchAgent for gate.py or the harness. Darwin-only.",
    )
    parser.add_argument("--service", choices=sorted(_LABELS), required=True)
    parser.add_argument(
        "--config", default=str(_REPO_ROOT / "config.yaml"),
        help="Path to config.yaml, read for api.port (gate only; default: repo config.yaml).",
    )
    parser.add_argument(
        "--api-key-service", default="",
        help="Optional Keychain service name holding CYCLAW_API_KEY (unset: not injected).",
    )
    parser.add_argument(
        "--throttle-sec", type=int, default=_DEFAULT_THROTTLE_SEC,
        help="Minimum seconds between restart attempts (default: %(default)s).",
    )
    parser.add_argument("--reason", default="", help="Required: why this service should be supervised.")
    parser.add_argument(
        "--confirm", action="store_true",
        help="Required: acknowledges the always-on/auto-restart posture change before writing the plist.",
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
    port = (api_cfg or {}).get("port", 8787) if isinstance(api_cfg, dict) else 8787
    return int(port)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if platform.system() != "Darwin":
        print("error: this generator is Darwin-only (writes a macOS launchd plist).", file=sys.stderr)
        return 3

    if not args.confirm or not args.reason.strip():
        print(_RISK_TEXT, file=sys.stderr)
        missing = []
        if not args.confirm:
            missing.append("--confirm")
        if not args.reason.strip():
            missing.append("--reason")
        print(f"\nRefusing to write a plist without: {', '.join(missing)}", file=sys.stderr)
        return 1

    if args.throttle_sec <= 0:
        print("error: --throttle-sec must be > 0", file=sys.stderr)
        return 2

    label = _LABELS[args.service]
    env: dict[str, str] = {}

    if args.service == "gate":
        port = _read_gate_port(Path(args.config).resolve())
        inner_argv = [launchd_plist.python_executable(), str(_REPO_ROOT / "gate.py")]
    else:
        port = _DEFAULT_HARNESS_PORT
        inner_argv = [launchd_plist.python_executable(), "-m", "harness.server"]
        # Non-secret, so directly in EnvironmentVariables (unlike CYCLAW_API_KEY
        # below, which only ever flows through the Keychain wrapper's export).
        env["CYCLAW_HOME"] = str(Path.home() / ".CyClaw")
        env["CYCLAW_REPO"] = str(_REPO_ROOT)

    secrets: list[tuple[str, str]] = []
    if args.api_key_service:
        secrets.append((args.api_key_service, "CYCLAW_API_KEY"))
    wrapper = launchd_plist.keychain_wrapper_path(_REPO_ROOT)
    program_args = launchd_plist.wrap_with_keychain_secrets(inner_argv, secrets, wrapper)

    log_path = str(launchd_plist.logs_dir() / f"{args.service}.log")
    document: dict[str, object] = {
        "Label": label,
        "WorkingDirectory": str(_REPO_ROOT),
        "ProgramArguments": program_args,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": args.throttle_sec,
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
    }
    if env:
        document["EnvironmentVariables"] = env

    path = launchd_plist.plist_path(label)
    launchd_plist.write_plist(document, path)

    print(f"Wrote {path}")
    print(f"  service:        {args.service} (port {port})")
    print(f"  reason:         {args.reason.strip()}")
    print(f"  restart policy: crash-only (KeepAlive.SuccessfulExit=false), throttled to {args.throttle_sec}s")
    print()
    print("  This makes the service ALWAYS-ON: it survives logout/reboot and")
    print("  auto-restarts on crash. To stop it for good, use 'launchctl bootout'")
    print("  (not just 'launchctl stop', which a crash-restart plist may still")
    print("  relaunch depending on how the process actually exited).")
    print(f"  NOT loaded. Run to activate: {launchd_plist.bootstrap_hint(path)}")
    _maybe_print_harness_api_key_note(args)
    return 0


def _maybe_print_harness_api_key_note(args: argparse.Namespace) -> None:
    if args.service != "harness" or not args.api_key_service:
        return
    print()
    print("  NOTE: harness/server.py does not currently read CYCLAW_API_KEY --")
    print("  --api-key-service has no effect for --service harness today. The")
    print("  Keychain wrapper still runs and exports the variable, but nothing")
    print("  in the harness process consumes it.")


if __name__ == "__main__":
    sys.exit(main())
