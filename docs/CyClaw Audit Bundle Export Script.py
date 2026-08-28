#!/usr/bin/env python3
"""Export CyClaw audit logs into a tamper-evident bundle.

Creates a ZIP archive containing:
  - manifest.json   — bundle metadata, file list, SHA-256 hashes
  - audit.jsonl     — copy of logs/audit.jsonl (if present)
  - spend.jsonl     — copy of logs/spend.jsonl (if present)
  - numbat-events.ndjsonl — copy of logs/numbat-events.ndjsonl (if present)
  - config-hash.txt — SHA-256 of the deployed config.yaml
  - chain.txt       — SHA-256 hash chain for tamper evidence

Usage:
  python scripts/export-audit-bundle.py --output audit-export.zip
  python scripts/export-audit-bundle.py --list audit-export.zip
  python scripts/export-audit-bundle.py --verify audit-export.zip
  python scripts/export-audit-bundle.py --purge-older-than 1095 --dry-run
  python scripts/export-audit-bundle.py --purge-older-than 1095

This script is deliberately stdlib-only (zipfile, hashlib, json, argparse,
pathlib) — no CyClaw imports — so it can run on any machine with Python 3.10+
without installing the project.

Not a substitute for a formal audit. The hash chain provides cryptographic
tamper evidence for the audit log; it does not prove the logs are complete
(missing entries are undetectable). An MSP or compliance officer should
supplement this with operational controls (append-only file permissions,
off-site backup, periodic review).

License: MIT (same as CyClaw)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

# --- Configuration -----------------------------------------------------------

DEFAULT_LOG_DIR = Path("logs")
DEFAULT_CONFIG_PATH = Path("config.yaml")

LOG_FILES = {
    "audit.jsonl": "logs/audit.jsonl",
    "spend.jsonl": "logs/spend.jsonl",
    "numbat-events.ndjsonl": "logs/numbat-events.ndjsonl",
}


# --- Helpers -----------------------------------------------------------------


def sha256_file(path: Path) -> str:
    """SHA-256 of a file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    """SHA-256 of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_hash_chain(lines: list[str]) -> str:
    """Build a SHA-256 hash chain from log lines.

    chain[0] = SHA256(lines[0] + "0")
    chain[n] = SHA256(lines[n] + chain[n-1])

    Any modification to a historical entry breaks the chain from that point
    forward. Missing entries are NOT detectable by this mechanism — this is
    tamper evidence, not completeness proof.
    """
    chain_entries: list[str] = []
    prev = "0"
    for line in lines:
        line_stripped = line.rstrip("\n")
        current = sha256_text(line_stripped + prev)
        chain_entries.append(current)
        prev = current
    return "\n".join(chain_entries)


def verify_hash_chain(lines: list[str], chain_text: str) -> bool:
    """Verify a hash chain against log lines. Returns True if intact."""
    chain_entries = chain_text.strip().split("\n")
    if len(chain_entries) != len(lines):
        return False
    prev = "0"
    for i, line in enumerate(lines):
        line_stripped = line.rstrip("\n")
        expected = sha256_text(line_stripped + prev)
        if chain_entries[i] != expected:
            return False
        prev = expected
    return True


def parse_timestamp_from_jsonl(line: str) -> datetime | None:
    """Attempt to extract a timestamp from a JSONL log line."""
    try:
        obj = json.loads(line)
        ts = obj.get("timestamp") or obj.get("ts") or obj.get("time")
        if isinstance(ts, str):
            # Try ISO format with timezone
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return None


# --- Commands ----------------------------------------------------------------


def cmd_export(args: argparse.Namespace) -> int:
    """Export audit logs into a tamper-evident ZIP bundle."""
    output_path = Path(args.output)
    if output_path.exists() and not args.force:
        print(f"Error: {output_path} already exists. Use --force to overwrite.", file=sys.stderr)
        return 1

    manifest: dict[str, object] = {
        "created": datetime.now(UTC).isoformat(),
        "cyclaw_version": "v1.9.x",
        "bundle_format": "1.0",
        "files": {},
    }

    # Collect log files that exist
    files_to_bundle: dict[str, Path] = {}
    for name, log_path in LOG_FILES.items():
        p = Path(log_path)
        if p.exists() and p.stat().st_size > 0:
            files_to_bundle[name] = p

    # Config hash
    config_path = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    if config_path.exists():
        config_hash = sha256_file(config_path)
        manifest["config_hash"] = config_hash
        manifest["config_path"] = str(config_path)

    # Build hash chain for audit log
    chain_text = ""
    if "audit.jsonl" in files_to_bundle:
        audit_path = files_to_bundle["audit.jsonl"]
        lines = audit_path.read_text(encoding="utf-8", errors="replace").splitlines()
        chain_text = build_hash_chain(lines)
        manifest["audit_chain_entries"] = len(lines)

    # Write bundle
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, path in files_to_bundle.items():
            zf.write(path, name)
            manifest["files"][name] = {
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }

        # Config hash file
        if config_path.exists():
            zf.writestr("config-hash.txt", f"{config_hash}\n")

        # Hash chain
        if chain_text:
            zf.writestr("chain.txt", chain_text + "\n")

        # Manifest (written last, after all files are hashed)
        manifest["manifest_sha256"] = sha256_text(
            json.dumps(manifest, sort_keys=True, indent=2)
        )
        zf.writestr(
            "manifest.json",
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        )

    print(f"Bundle created: {output_path}")
    print(f"  Files: {len(files_to_bundle)}")
    print(f"  Audit entries: {manifest.get('audit_chain_entries', 0)}")
    print(f"  Config hash: {manifest.get('config_hash', 'N/A')[:16]}...")
    print(f"  Bundle size: {output_path.stat().st_size:,} bytes")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List contents of an audit bundle."""
    bundle_path = Path(args.bundle)
    if not bundle_path.exists():
        print(f"Error: {bundle_path} not found", file=sys.stderr)
        return 1

    with zipfile.ZipFile(bundle_path, "r") as zf:
        manifest = None
        for name in zf.namelist():
            if name == "manifest.json":
                manifest = json.loads(zf.read(name).decode("utf-8"))
                break

        print(f"Bundle: {bundle_path}")
        print(f"Created: {manifest.get('created', 'unknown') if manifest else 'unknown'}")
        print()
        print(f"{'File':<30} {'Size':>12} {'SHA-256 (first 16)':>20}")
        print("-" * 65)
        for name in zf.namelist():
            info = zf.getinfo(name)
            file_hash = sha256_text(zf.read(name).decode("utf-8", errors="replace"))[:16]
            print(f"{name:<30} {info.file_size:>12,} {file_hash:>20}")

        if manifest:
            print()
            print(f"Audit chain entries: {manifest.get('audit_chain_entries', 'N/A')}")
            print(f"Config hash: {manifest.get('config_hash', 'N/A')[:32]}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify bundle integrity and hash chain."""
    bundle_path = Path(args.bundle)
    if not bundle_path.exists():
        print(f"Error: {bundle_path} not found", file=sys.stderr)
        return 1

    with zipfile.ZipFile(bundle_path, "r") as zf:
        names = zf.namelist()
        if "manifest.json" not in names:
            print("FAIL: manifest.json missing from bundle", file=sys.stderr)
            return 1

        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

        # Verify file hashes
        all_ok = True
        for name, info in manifest.get("files", {}).items():
            if name not in names:
                print(f"FAIL: {name} listed in manifest but missing from bundle")
                all_ok = False
                continue
            actual_hash = (
                sha256_file.__wrapped__(zf.read(name))
                if hasattr(sha256_file, "__wrapped__")
                else hashlib.sha256(zf.read(name)).hexdigest()
            )
            if actual_hash != info["sha256"]:
                print(f"FAIL: {name} hash mismatch (expected {info['sha256'][:16]}, got {actual_hash[:16]})")
                all_ok = False
            else:
                print(f"OK: {name} hash verified")

        # Verify hash chain
        if "audit.jsonl" in names and "chain.txt" in names:
            audit_lines = zf.read("audit.jsonl").decode("utf-8", errors="replace").splitlines()
            chain_text = zf.read("chain.txt").decode("utf-8", errors="replace")
            if verify_hash_chain(audit_lines, chain_text):
                print(f"OK: hash chain verified ({len(audit_lines)} entries)")
            else:
                print("FAIL: hash chain broken — possible tampering detected")
                all_ok = False
        elif "audit.jsonl" in names:
            print("WARN: audit.jsonl present but chain.txt missing — cannot verify tamper evidence")
        else:
            print("INFO: no audit.jsonl in bundle — nothing to chain-verify")

        if all_ok:
            print("\nBundle integrity: VERIFIED")
            return 0
        else:
            print("\nBundle integrity: FAILED", file=sys.stderr)
            return 1


def cmd_purge(args: argparse.Namespace) -> int:
    """Purge audit log entries older than N days."""
    retention_days = args.purge_older_than
    dry_run = args.dry_run
    audit_path = Path(LOG_FILES["audit.jsonl"])

    if not audit_path.exists():
        print(f"No audit log found at {audit_path}")
        return 0

    cutoff = datetime.now(UTC).timestamp() - (retention_days * 86400)
    lines = audit_path.read_text(encoding="utf-8", errors="replace").splitlines()

    kept: list[str] = []
    purged = 0
    for line in lines:
        ts = parse_timestamp_from_jsonl(line)
        if ts and ts.timestamp() < cutoff:
            purged += 1
        else:
            kept.append(line)

    action = "Would purge" if dry_run else "Purged"
    print(f"{action} {purged} entries (older than {retention_days} days)")
    print(f"Keeping {len(kept)} entries")

    if dry_run:
        print("\n(dry-run — no changes made)")
        return 0

    # Write retained entries back
    audit_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    print(f"Audit log updated: {audit_path}")
    return 0


# --- CLI ---------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export CyClaw audit logs into a tamper-evident bundle.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # export
    p_export = sub.add_parser("export", help="Create an audit bundle")
    p_export.add_argument("--output", "-o", default="audit-export.zip", help="Output ZIP path")
    p_export.add_argument("--force", action="store_true", help="Overwrite existing output")
    p_export.add_argument("--config", help="Path to config.yaml (default: config.yaml)")
    p_export.set_defaults(func=cmd_export)

    # list
    p_list = sub.add_parser("list", help="List contents of an audit bundle")
    p_list.add_argument("bundle", help="Path to the audit bundle ZIP")
    p_list.set_defaults(func=cmd_list)

    # verify
    p_verify = sub.add_parser("verify", help="Verify bundle integrity and hash chain")
    p_verify.add_argument("bundle", help="Path to the audit bundle ZIP")
    p_verify.set_defaults(func=cmd_verify)

    # purge
    p_purge = sub.add_parser("purge", help="Purge old audit log entries")
    p_purge.add_argument("--purge-older-than", type=int, required=True, help="Days to retain")
    p_purge.add_argument("--dry-run", action="store_true", help="Show what would be purged without modifying")
    p_purge.set_defaults(func=cmd_purge)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
