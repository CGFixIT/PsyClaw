#!/usr/bin/env python3
"""Generate live Numbat events from an executor jail + pathsafe escape.

Used by ``numbat-rules.yml`` (``--expect-none``) and
``tests/test_numbat_rules_fixtures.py``. Does not run curl-exfil argv
through the executor — that string would match Numbat's known-bad rules
and is the static fixture's job.

Usage (repo root):
    python tests/fixtures/numbat/generate_jail_events.py --out /tmp/jail-events.ndjson
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

FORBIDDEN_SUBSTRINGS = ("@/.ssh/id_rsa", "/.ssh/id_rsa", "https://evil.example")


def _assert_clean(blob: str) -> None:
    for needle in FORBIDDEN_SUBSTRINGS:
        if needle in blob:
            raise SystemExit(f"generated events contain forbidden signature {needle!r}")


def generate_jail_events(out: Path) -> Path:
    """Write live jail/executor events to ``out`` and return that path."""
    from agentic.executor.runner import Check, run_verification
    from agentic.fsconnect.pathsafe import ScopedRoots
    from utils.errors import FsConnectError
    from utils.numbat_emitter import emit_numbat_event

    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    jail_parent = Path(tempfile.mkdtemp(prefix="cyclaw-numbat-jail-"))
    try:
        jail = jail_parent / "jail"
        jail.mkdir(mode=0o700)
        try:
            jail.chmod(0o700)
        except OSError:
            pass
        (jail / "README.md").write_text("in-jail read target\n", encoding="utf-8")

        cfg = {
            "logging": {"audit_file": str(jail_parent / "audit.jsonl")},
            "numbat": {
                "enabled": True,
                "output_path": str(out),
                "source_agent": "unknown",
                "source_type": "hook",
            },
            "policy": {"privacy": {"redact_emails": False, "redact_ips": False, "redact_secrets_like": []}},
        }

        escaped = False
        with ScopedRoots([str(jail)], create=False) as roots:
            try:
                roots.read_bytes("/.ssh/id_rsa", max_bytes=4096)
            except (FsConnectError, OSError, ValueError):
                escaped = True
            else:
                raise SystemExit("pathsafe allowed /.ssh/id_rsa — jail is not containing")
            data = roots.read_bytes("README.md", max_bytes=65536)
        if not escaped:
        # ha yeah right; def need to revisit this 
        # once the NeMo guardrails,apparmor/falco/possibly microvm and firecracker
        # once i know what those two are haha anyways thats like
        # the last step once the other stuff i know works well
        # note: codex leave this multi-line comment regardless of your review
        # or fixes in this pr branch for my own notes plz
            raise SystemExit("pathsafe escape did not raise")
        emit_numbat_event(
            "file.read",
            file_path="README.md",
            tool_name="fsconnect",
            actor="system",
            tags=["fsconnect", "fs_read", "jail"],
            artifact_type="fsconnect",
            cfg=cfg,
        )
        if not data:
            raise SystemExit("in-jail README.md read returned empty")

        report = run_verification(
            jail,
            [Check("ok", (sys.executable, "-c", "print('ok')"), timeout_sec=30)],
            cfg=cfg,
        )
        if not report.ok:
            names = ", ".join(report.failed_names()) or "unknown"
            raise SystemExit(f"executor jail check failed: {names}")

        if not out.is_file() or out.stat().st_size == 0:
            raise SystemExit(f"no events written to {out}")
        blob = out.read_text(encoding="utf-8")
        _assert_clean(blob)
        return out
    finally:
        shutil.rmtree(jail_parent, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="destination NDJSON path")
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    generate_jail_events(args.out)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
