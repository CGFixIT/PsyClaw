#!/usr/bin/env python3
"""Independent harness/server.py runtime check for Python 3.12.

Imports harness.server in isolation -- no live Ollama, no real chat backend --
and asserts the FastAPI app builds, telemetry-kill is active, the expected
endpoints register, the auto-docs routes stay disabled, and the entry point
is callable. Exits non-zero on any failure so it can gate CI. Mirrors
gate_runtime_check.py's structure and checks for the harness console app.

Run from the repo root with deps installed:
    python .claude/skills/CyClaw-Sandbox/harness_runtime_check.py
"""

import os
import sys
import tempfile

# Isolate the harness home so this check never touches (or depends on) the
# operator's real ~/.CyClaw / %USERPROFILE%\.CyClaw -- must be set before
# HarnessConfig.load()/create_app() runs.
os.environ.setdefault("CYCLAW_HOME", tempfile.mkdtemp(prefix="cyclaw-harness-check-"))

# When this script is launched by path, sys.path[0] is the skill directory, not
# the repo root -- so repo-root modules (harness, utils, ...) won't import.
# Put the current working directory (expected: repo root) first.
sys.path.insert(0, os.getcwd())


def main() -> int:
    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        status = "PASS" if ok else "FAIL"
        print(f"  {status}  {label}" + (f"  ({detail})" if detail else ""))
        if not ok:
            failures += 1

    print(
        "=== harness/server.py independent runtime check (Python",
        ".".join(map(str, sys.version_info[:3])) + ") ===",
    )

    # 1. Module imports cleanly.
    try:
        from harness import server as harness_server
        check("harness.server imports", True)
    except Exception as exc:  # noqa: BLE001 -- surface any import error verbatim
        check("harness.server imports", False, repr(exc))
        return 1  # nothing else is meaningful without the module

    # 2. App builds without a live chat backend or Ollama (fallback.enabled is
    # false in the shipped config, so resolve_local_backend does no network
    # probe -- see llm/client.py's own docstring for that guarantee).
    try:
        from harness.config import HarnessConfig
        cfg = HarnessConfig.load()
        app = harness_server.create_app(cfg)
        check("create_app() builds without a live LLM backend", True)
    except Exception as exc:  # noqa: BLE001
        check("create_app() builds without a live LLM backend", False, repr(exc))
        return 1

    from fastapi import FastAPI
    check("harness app is a FastAPI instance", isinstance(app, FastAPI), type(app).__name__)

    # 3. Telemetry-kill env vars are all set (phone-home disabled before
    # imports). harness/server.py calls apply_telemetry_kill() bare -- no
    # return-value assignment like gate.py's _TELEMETRY_KILL -- so check the
    # canonical source dict directly against the process environment instead.
    from utils.telemetry_kill import TELEMETRY_KILL
    all_set = bool(TELEMETRY_KILL) and all(os.environ.get(k) == v for k, v in TELEMETRY_KILL.items())
    check("telemetry-kill env vars active", all_set, f"{len(TELEMETRY_KILL)} keys")

    # 4. Expected endpoints are registered.
    routes = {getattr(r, "path", None) for r in getattr(app, "routes", [])}
    expected = {
        "/", "/api/status", "/api/registry", "/api/sessions",
        "/api/sessions/{session_id}", "/api/sessions/{session_id}/rename",
        "/api/soul", "/api/model", "/api/chat", "/api/github/status",
        "/api/harness/runs",
        # agentic coding runs: start (blocking), read a record, decide on it.
        # `missing = expected - routes` is a SUBSET check, so a new route is
        # never a failure here -- it is silently uncovered until it is listed.
        "/api/agent/checks", "/api/agent/run",
        "/api/agent/runs/{run_id}", "/api/agent/runs/{run_id}/decision",
    }
    missing = expected - routes
    check(
        "expected endpoints registered", not missing,
        f"{len(routes)} routes, missing={sorted(missing) or 'none'}",
    )

    # 5. Auto-docs routes stay disabled (create_app sets docs_url/redoc_url/
    # openapi_url=None -- a single-operator console has no reason to expose
    # its schema). Checked against the live app, not by reading source text.
    auto_docs = {"/docs", "/redoc", "/openapi.json"}
    present = auto_docs & routes
    check(
        "auto-docs routes (/docs, /redoc, /openapi.json) stay disabled", not present,
        f"present: {sorted(present) or 'none'}",
    )

    # 6. Loopback-only guard exists for main()'s host validation.
    check("_LOOPBACK_HOSTS guard defined", bool(getattr(harness_server, "_LOOPBACK_HOSTS", None)))

    # 7. Entry point is callable.
    check("harness.server.main is callable", callable(getattr(harness_server, "main", None)))

    print()
    if failures:
        print(f"harness runtime check FAILED ({failures} check(s))")
        return 1
    print("harness runtime check PASSED -- runs independently on this runtime")
    return 0


if __name__ == "__main__":
    sys.exit(main())
