#!/usr/bin/env python3
"""Independent gate.py runtime check for Python 3.12.

Imports gate.py in isolation — no uvicorn, no live LM Studio — and asserts the
FastAPI app builds, telemetry-kill is active, the expected endpoints register,
and the entry point is callable. Exits non-zero on any failure so it can gate CI.

Run from the repo root with deps installed:
    GROK_API_KEY=dummy python .claude/skills/CyClaw-Sandbox/gate_runtime_check.py
"""

import os
import sys

# Offline-friendly defaults so importing gate.py never blocks on missing env.
os.environ.setdefault("GROK_API_KEY", "dummy")

# When this script is launched by path, sys.path[0] is the skill directory, not
# the repo root — so repo-root modules (gate, retrieval, ...) won't import.
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

    print("=== gate.py independent runtime check (Python", ".".join(map(str, sys.version_info[:3])) + ") ===")

    # 1. Module imports cleanly.
    try:
        import gate
        check("gate.py imports", True)
    except Exception as exc:  # noqa: BLE001 — surface any import error verbatim
        check("gate.py imports", False, repr(exc))
        return 1  # nothing else is meaningful without the module

    # 2. FastAPI app object is the right type.
    from fastapi import FastAPI
    app = getattr(gate, "app", None)
    check("gate.app is a FastAPI instance", isinstance(app, FastAPI),
          type(app).__name__)

    # 3. Telemetry-kill env vars are all set (phone-home disabled before imports).
    kill = getattr(gate, "_TELEMETRY_KILL", {})
    all_set = bool(kill) and all(os.environ.get(k) == v for k, v in kill.items())
    check("telemetry-kill env vars active", all_set, f"{len(kill)} keys")

    # 4. Expected endpoints are registered.
    routes = {getattr(r, "path", None) for r in getattr(app, "routes", [])}
    expected = {"/health", "/query", "/soul", "/soul/propose", "/soul/apply",
                "/soul/reload", "/"}
    missing = expected - routes
    check("expected endpoints registered", not missing,
          f"{len(routes)} routes, missing={sorted(missing) or 'none'}")

    # 5. Entry point is callable.
    check("gate.main is callable", callable(getattr(gate, "main", None)))

    print()
    if failures:
        print(f"gate.py runtime check FAILED ({failures} check(s))")
        return 1
    print("gate.py runtime check PASSED — runs independently on this runtime")
    return 0


if __name__ == "__main__":
    sys.exit(main())
