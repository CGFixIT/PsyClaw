#!/usr/bin/env python3
"""check_otel.py – static validation of CyClaw's telemetry-kill contract.

Usage:
    python3 .claude/skills/OTel-Hardening/check_otel.py [--repo-root PATH]
                                                        [--strict] [--json]

CyClaw's threat model (docs/THREAT_MODEL.md) forbids telemetry outright. The
actual defense is a fixed dict of environment variables in
utils/telemetry_kill.py plus one conditional pair in retrieval/embeddings.py --
both are prose-verified against each vendor's *current* source at some past
date, not continuously re-checked. A vendor can change its own telemetry
contract in a later release without CyClaw's pins moving, so a pin bump with
no matching re-verification is exactly the silent-drift case this exists to
catch.

This script only proves the STATIC half of that: the kill dict still has the
shape/keys/credential-scrub it is supposed to have, the conditional HF Hub
wiring is still present, the reference .env doc has not drifted from the code,
and -- most importantly -- whether any of the vendor packages this dict targets
have been pinned to a newer version than the one recorded as last-verified
here. A pin drift is NOT proof the vendor changed its telemetry contract; it is
a prompt to go re-read that vendor's current source/docs, which this script
cannot do (see SKILL.md Step 2 for the live web-search half of the process).

Zero third-party imports (tomllib is stdlib on 3.12) -- runs in a fresh clone
before any pip install, same as dep-guard.

Severity:
    FAIL  a kill-switch invariant actually broke (exit 2).
    WARN  a vendor pin moved past the last-verified baseline, or the
          last-verified date is stale -- re-verification, not a proven leak
          (exit 0; --strict escalates every WARN to a failure).
    INFO  advisory (e.g. a transitive-only vendor's installed version, or a new
          key found beyond the recorded baseline); never affects the exit code.

Exit codes (repo convention):
    0  contract holds (warnings may be present without --strict)
    2  a FAIL check tripped (or a WARN under --strict)
    3  env/config error (utils/telemetry_kill.py missing or unparseable)
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import date
from importlib import metadata
from pathlib import Path

# ---------------------------------------------------------------------------
# Baselines -- update these in the SAME commit as any deliberate kill-dict
# change, exactly like dep-guard's _PYDANTIC_LOCKSTEP. A checker with a stale
# baseline just nags forever; one with no baseline can't tell drift from noise.
# ---------------------------------------------------------------------------

# Keys TELEMETRY_KILL is known to carry as of the last full verification pass.
# T2 FAILs if any of these disappear (a real weakening); a key present in the
# dict but absent here is new and expected -- T2 reports it as INFO so this
# list gets updated, not because it is dangerous by itself.
BASELINE_KEYS = frozenset({
    "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGSMITH_OTEL_ENABLED",
    "LANGGRAPH_CLI_NO_ANALYTICS",
    "NEMO_GUARDRAILS_NO_USAGE_STATS", "ANONYMIZED_TELEMETRY",
    "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK", "ORT_TELEMETRY_OPT_OUT",
    "CHROMA_OTEL_GRANULARITY", "CHROMA_OTEL_COLLECTION_ENDPOINT",
    "CHROMA_OTEL_SERVICE_NAME", "OTEL_SDK_DISABLED", "OTEL_TRACES_EXPORTER",
    "OTEL_METRICS_EXPORTER", "OTEL_LOGS_EXPORTER",
})

BASELINE_CREDENTIALS = frozenset({"LANGCHAIN_API_KEY", "LANGSMITH_API_KEY", "LANGCHAIN_ENDPOINT"})

# Vendor pins whose telemetry surface this dict targets, and the version last
# verified against that vendor's actual source (not just its docs). Pulled
# from pyproject.toml's direct dependencies. A pin here that no longer matches
# what pyproject.toml ships is not a failure -- it is the prompt to re-run the
# live-search half of this skill against that vendor's current release notes.
LAST_VERIFIED_VENDOR_PINS = {
    "chromadb": "1.5.9",
    "langchain": "1.3.11",
    "langchain-core": "1.4.8",
    "langgraph": "1.2.6",
    "nemoguardrails": "0.23.0",
    "sentence-transformers": "5.6.0",
}

# Transitive-only vendors (no direct pyproject pin -- pulled in by the packages
# above). Best-effort: report the installed version if this environment
# happens to have it, purely as INFO context for the live-search step: never
# FAIL or WARN on these, since a fresh clone legitimately has none installed.
TRANSITIVE_VENDORS_TO_REPORT = ("huggingface_hub", "onnxruntime", "opentelemetry-sdk")

# Re-verification is stale after this many days -- a soft prompt, not a hard
# rule; vendors do not ship telemetry changes on a schedule.
STALE_AFTER_DAYS = 120

_TODAY = date(2026, 7, 29)  # scan-day anchor for THIS script's own last edit; see SKILL.md Step 4

_VERIFIED_DATE_RE = re.compile(r"[Vv]erified\s+(\d{4}-\d{2}-\d{2})")

_fails: list[dict[str, str]] = []
_warns: list[dict[str, str]] = []


def fail(check: str, detail: str) -> None:
    _fails.append({"check": check, "detail": detail})
    print(f"  FAIL  [{check}] {detail}")


def warn(check: str, detail: str) -> None:
    _warns.append({"check": check, "detail": detail})
    print(f"  WARN  [{check}] {detail}")


def ok(check: str, detail: str) -> None:
    print(f"  ok    [{check}] {detail}")


def info(check: str, detail: str) -> None:
    print(f"  info  [{check}] {detail}")


def _assign_targets(node: ast.stmt) -> list[ast.expr]:
    """Uniform target list for both ``x = ...`` and annotated ``x: T = ...``."""
    if isinstance(node, ast.Assign):
        return node.targets
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [node.target]
    return []


def _load_telemetry_kill_dict(path: Path) -> dict[str, str]:
    """AST-parse TELEMETRY_KILL without importing the module (no os.environ side effect).

    TELEMETRY_KILL carries a ``: dict[str, str]`` annotation, so it parses as
    ``ast.AnnAssign``, not the plain ``ast.Assign`` a bare ``NAME = {...}`` would.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for tgt in _assign_targets(node):
                if isinstance(tgt, ast.Name) and tgt.id == "TELEMETRY_KILL":
                    value = ast.literal_eval(node.value)
                    if not isinstance(value, dict):
                        raise ValueError("TELEMETRY_KILL is not a dict literal")
                    return value
    raise ValueError("TELEMETRY_KILL assignment not found")


def _load_tracing_credentials(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for tgt in _assign_targets(node):
                if isinstance(tgt, ast.Name) and tgt.id == "_TRACING_CREDENTIALS":
                    value = ast.literal_eval(node.value)
                    return tuple(value)
    raise ValueError("_TRACING_CREDENTIALS assignment not found")


def check_dict_shape(kill: dict[str, str]) -> None:
    if not kill:
        fail("T1", "TELEMETRY_KILL is empty")
        return
    non_str = [k for k, v in kill.items() if not isinstance(v, str)]
    if non_str:
        fail("T1", f"TELEMETRY_KILL values must all be str, non-str keys: {non_str}")
        return
    ok("T1", f"TELEMETRY_KILL parses as a non-empty dict[str, str] ({len(kill)} keys)")


def check_baseline_keys(kill: dict[str, str]) -> None:
    missing = BASELINE_KEYS - kill.keys()
    if missing:
        fail("T2", f"previously-verified kill key(s) removed: {sorted(missing)} -- this weakens the block")
        return
    new_keys = kill.keys() - BASELINE_KEYS
    if new_keys:
        info("T2", f"new key(s) beyond BASELINE_KEYS: {sorted(new_keys)} -- "
                   "if this run added them deliberately, add them to BASELINE_KEYS in this script")
    else:
        ok("T2", "all baseline kill keys present, no undocumented new keys")


def check_tracing_credentials(creds: tuple[str, ...]) -> None:
    missing = BASELINE_CREDENTIALS - set(creds)
    if missing:
        fail("T3", f"_TRACING_CREDENTIALS no longer scrubs {sorted(missing)}")
        return
    ok("T3", f"_TRACING_CREDENTIALS scrubs all {len(BASELINE_CREDENTIALS)} baseline credential names")


def check_verification_staleness(source: str) -> None:
    dates = sorted({date.fromisoformat(d) for d in _VERIFIED_DATE_RE.findall(source)})
    if not dates:
        warn("T4", "no 'Verified YYYY-MM-DD' stamp found in utils/telemetry_kill.py -- "
                   "the code comments should record when each var was last checked against vendor source")
        return
    oldest = dates[0]
    age_days = (_TODAY - oldest).days
    if age_days > STALE_AFTER_DAYS:
        warn("T4", f"oldest 'Verified' stamp is {oldest.isoformat()} ({age_days}d old, "
                   f"threshold {STALE_AFTER_DAYS}d) -- re-run the live vendor-doc scan (SKILL.md Step 2)")
        return
    ok("T4", f"oldest 'Verified' stamp is {oldest.isoformat()} ({age_days}d old, within {STALE_AFTER_DAYS}d)")


def _load_pyproject_direct_deps(pyproject_path: Path) -> dict[str, str]:
    import tomllib

    with pyproject_path.open("rb") as fh:
        data = tomllib.load(fh)
    deps: dict[str, str] = {}
    req_re = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([^\s;,]+)")
    groups: list[list[str]] = [data.get("project", {}).get("dependencies", [])]
    groups.extend(data.get("project", {}).get("optional-dependencies", {}).values())
    for group in groups:
        for entry in group:
            m = req_re.match(entry)
            if m:
                name = re.sub(r"[-_.]+", "-", m.group(1)).lower()
                deps[name] = m.group(2)
    return deps


def check_vendor_pin_drift(pyproject_path: Path) -> None:
    if not pyproject_path.exists():
        warn("T5", f"{pyproject_path} not found -- skipped vendor pin drift check")
        return
    try:
        current = _load_pyproject_direct_deps(pyproject_path)
    except Exception as exc:  # noqa: BLE001 -- report as env-style warning, not a crash
        warn("T5", f"could not parse {pyproject_path}: {exc}")
        return
    drifted = []
    for vendor, baseline_pin in LAST_VERIFIED_VENDOR_PINS.items():
        pin = current.get(vendor)
        if pin is None:
            info("T5", f"{vendor} not found as a direct pyproject pin (transitive or removed) -- skipped")
        elif pin != baseline_pin:
            drifted.append(f"{vendor}: verified {baseline_pin} -> now pinned {pin}")
    if drifted:
        for d in drifted:
            warn("T5", f"vendor pin drifted since last verification -- {d}; re-check its telemetry docs")
    else:
        ok("T5", f"all {len(LAST_VERIFIED_VENDOR_PINS)} directly-pinned vendors unchanged since last verification")


def report_transitive_vendor_versions() -> None:
    for name in TRANSITIVE_VENDORS_TO_REPORT:
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            info("T6", f"{name} not installed in this environment -- skipped (normal in a fresh clone)")
            continue
        info("T6", f"{name} installed version: {version} -- cross-check against this skill's last live-search notes")


def check_embeddings_conditional_wiring(embeddings_path: Path) -> None:
    if not embeddings_path.exists():
        fail("T7", f"{embeddings_path} not found")
        return
    source = embeddings_path.read_text(encoding="utf-8")
    needed = ("_model_offline_eligible", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "try_to_load_from_cache")
    missing = [n for n in needed if n not in source]
    if missing:
        fail("T7", f"retrieval/embeddings.py is missing expected conditional-offline symbol(s): {missing} -- "
                   "the HF Hub half of the telemetry story may have regressed")
        return
    ok("T7", "retrieval/embeddings.py still wires the conditional HF Hub offline check")


def check_reference_env_file(kill: dict[str, str], env_path: Path) -> None:
    if not env_path.exists():
        warn("T8", f"{env_path} not found -- reference doc missing")
        return
    text = env_path.read_text(encoding="utf-8")
    documented = {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    # HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE are documented there deliberately even
    # though they are NOT unconditional entries in TELEMETRY_KILL -- see both
    # files' own docstrings/headers. Expected = kill-dict keys plus that pair.
    expected = set(kill) | {"HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"}
    missing_from_doc = expected - documented
    extra_in_doc = documented - expected
    if missing_from_doc or extra_in_doc:
        detail = f"missing from doc: {sorted(missing_from_doc)}; undocumented extra: {sorted(extra_in_doc)}"
        warn("T8", f"{env_path.name} has drifted from the code -- {detail}")
        return
    ok("T8", f"{env_path.name} documents exactly the code's kill set plus the conditional HF pair")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", type=Path, default=None)
    p.add_argument("--strict", action="store_true",
                   help="treat WARN as failure (exit 2 on any warning)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    root = args.repo_root or Path(__file__).resolve().parents[3]
    kill_path = root / "utils" / "telemetry_kill.py"
    if not kill_path.exists():
        print(f"env error: not found: {kill_path}", file=sys.stderr)
        return 3

    try:
        source = kill_path.read_text(encoding="utf-8")
        kill = _load_telemetry_kill_dict(kill_path)
        creds = _load_tracing_credentials(kill_path)
    except (OSError, SyntaxError, ValueError) as exc:
        print(f"env error: could not parse {kill_path}: {exc}", file=sys.stderr)
        return 3

    print("== OTel-Hardening: static telemetry-kill contract ==")
    check_dict_shape(kill)
    check_baseline_keys(kill)
    check_tracing_credentials(creds)
    check_verification_staleness(source)
    check_vendor_pin_drift(root / "pyproject.toml")
    report_transitive_vendor_versions()
    check_embeddings_conditional_wiring(root / "retrieval" / "embeddings.py")
    check_reference_env_file(kill, root / "docs" / "security-philosophy" / "cyclaw_telemetry_kill.env")

    strict_fail = args.strict and _warns
    print(f"\n{len(_fails)} failure(s), {len(_warns)} warning(s)"
          + (" (--strict: warnings count as failures)" if args.strict else ""))
    if args.json:
        print(json.dumps({"fails": _fails, "warns": _warns, "strict": args.strict}, indent=2))
    return 2 if (_fails or strict_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
