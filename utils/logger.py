"""Append-only audit logging with query hashing and privacy redaction,
plus standard Python logging setup for operational diagnostics.

Every query, miss, escalation, and error gets a JSONL line. When
logging.audit_fields.include_query_hash is true (the shipped default),
query text is SHA256-hashed so the audit log cannot become a data
exfiltration vector; setting that toggle false stores the raw query text
(PII redaction still applies) and is privacy-affecting — see config.yaml
logging.audit_fields and the invariant in tests/test_due_diligence_invariants.py.
"""

import atexit
import hashlib
import json
import logging
import re
import threading
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import TextIO

import yaml

logger = logging.getLogger("cyclaw.logger")

_logging_initialized = False
_AUDIT_WRITE_LOCK = threading.Lock()
# Anchor relative config_path lookups to the repo root, mirroring gate.py's
# _BASE_DIR pattern. Without this, audit_log()/setup_logging() callers that
# don't pass cfg explicitly (graph.py, utils/personality.py) resolve the
# default "config.yaml" against the process CWD, which raises
# FileNotFoundError whenever cyclaw-server is invoked from outside the repo
# root — exactly the fragility _BASE_DIR exists to prevent in gate.py itself.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# audit_log() previously opened, wrote, and closed the audit file on every
# single call — each event paid a fresh open() (path resolution, inode
# lookup, possible file creation) plus a close(). Under sustained query
# volume that syscall overhead dominates the write itself. Instead, keep one
# append-mode handle open per resolved audit-file path and reuse it across
# calls; still flush() after every write so readers observe each event
# immediately (audit_log's synchronous-visibility contract is unchanged —
# only the repeated open/close is eliminated, not the durability guarantee).
_AUDIT_HANDLES: dict[str, TextIO] = {}


def _audit_handle(log_path: Path) -> TextIO:
    """Return the cached append-mode handle for log_path, opening it if needed.

    Caller must hold _AUDIT_WRITE_LOCK.
    """
    key = str(log_path)
    handle = _AUDIT_HANDLES.get(key)
    if handle is None or handle.closed:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Intentionally long-lived: cached in _AUDIT_HANDLES and reused across
        # every subsequent audit_log() call for this path (see module docstring
        # above). A static file-not-closed check cannot see across that
        # module-level lifetime from this function alone, so the close is
        # registered right here (not only in the batch close_audit_handles()
        # below) -- closing an already-closed file object is a no-op, so the
        # two closers never conflict.
        handle = open(log_path, "a", encoding="utf-8")  # noqa: SIM115  # codeql[py/file-not-closed] closed via atexit.register below and close_audit_handles()
        atexit.register(handle.close)
        _AUDIT_HANDLES[key] = handle
    return handle


def close_audit_handles() -> None:
    """Flush and close all cached audit file handles.

    Called automatically at process exit; also useful for tests that need to
    release file descriptors before deleting their tmp_path audit files.
    """
    with _AUDIT_WRITE_LOCK:
        for handle in _AUDIT_HANDLES.values():
            try:
                handle.close()
            except OSError:
                # Best-effort at process-exit/test-teardown: a handle that fails to
                # close (e.g. its underlying fd was already torn down) has nothing
                # else useful to do here, and _AUDIT_HANDLES.clear() below still
                # drops our reference so a future audit_log() call reopens cleanly.
                pass
        _AUDIT_HANDLES.clear()


atexit.register(close_audit_handles)


def _anchor(path_str: str) -> Path:
    """Resolve path_str against the repo root when it isn't already absolute.

    Mirrors _get_config's own anchoring (see _REPO_ROOT above) so log_file/
    audit_file values read from config.yaml don't depend on the process cwd.
    """
    path = Path(path_str).expanduser()
    return path if path.is_absolute() else _REPO_ROOT / path


def setup_logging(cfg: dict | None = None) -> None:
    global _logging_initialized
    if _logging_initialized:
        return
    if cfg is None:
        cfg = _get_config()
    log_cfg = cfg.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("log_file", "")

    root = logging.getLogger("cyclaw")
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    if log_file:
        anchored_log_file = _anchor(log_file)
        anchored_log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(anchored_log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)

    _logging_initialized = True

def resolve_config_path(config_path: str = "config.yaml") -> Path:
    """Resolve a config path exactly as ``_get_config`` loads it.

    Relative paths anchor to the repo root (``_REPO_ROOT``), never the process
    cwd, so a caller that records "which config did I load" gets the SAME file
    the loader opened. The sync scheduler relies on this: it re-invokes the CLI
    with the recorded path, and a cwd-anchored ``os.path.abspath`` could name a
    different (or missing) file than the one actually read (codex #592 P1).
    """
    path = Path(config_path).expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return path.resolve()


@lru_cache(maxsize=8)
def _get_config(config_path: str = "config.yaml") -> dict:
    with open(resolve_config_path(config_path), encoding="utf-8") as f:
        return yaml.safe_load(f)

def reset_config_cache() -> None:
    clear = getattr(_get_config, "cache_clear", None)
    if clear is not None:
        clear()

def hash_query(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()

@lru_cache(maxsize=8)
def _compiled_redactors(
    redact_emails: bool, redact_ips: bool, secret_patterns: tuple[str, ...]
) -> tuple[tuple[re.Pattern, str], ...]:
    """Compile the active redaction patterns once per privacy configuration.

    redact_sensitive runs on every audited field of every query; recompiling
    these regexes each call was pure overhead. Keyed on the (hashable) privacy
    settings so a config change still produces a fresh pattern set.
    """
    compiled = []
    if redact_emails:
        compiled.append((re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'),
                         '[REDACTED_EMAIL]'))
    if redact_ips:
        compiled.append((re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'),
                         '[REDACTED_IP]'))
    for idx, pattern in enumerate(secret_patterns):
        try:
            compiled.append((re.compile(pattern), '[REDACTED_SECRET]'))
        except re.error as exc:
            # Silently dropping an invalid pattern disables redaction for that
            # shape with no signal — matching values then reach audit.jsonl and
            # /ops/* output verbatim. Warn instead (mirroring the sanitizer's
            # warn-on-degrade) so a config typo is visible rather than a silent
            # security regression. Log only the entry index and the compile
            # error (which carries the position of the fault) — never the
            # pattern text itself: it comes from privacy config and echoing
            # config values into logs is exactly what clear-text-logging
            # scanners flag. Cached per config, so it fires once per bad entry.
            logger.warning(
                "privacy redaction pattern #%d failed to compile (%s); it is "
                "skipped, so matching values pass through un-redacted until it "
                "is corrected.",
                idx, exc,
            )
    return tuple(compiled)

def redact_sensitive(text: str, cfg: dict | None = None) -> str:
    if cfg is None:
        cfg = _get_config()
    privacy = cfg.get("policy", {}).get("privacy", {})
    redactors = _compiled_redactors(
        privacy.get("redact_emails", False),
        privacy.get("redact_ips", False),
        tuple(privacy.get("redact_secrets_like", []) or []),
    )
    for pattern, replacement in redactors:
        text = pattern.sub(replacement, text)
    return text


# Keys whose top-level value must NOT be redacted: query_hash is already a SHA-256
# digest, timestamp is structural ISO-8601, and event is the event-type tag.
# Applied only at the OUTER record level — nested fields named the same inside a
# dict/list value have no special meaning and pass through normal redaction.
_AUDIT_SKIP_KEYS = frozenset(("query_hash", "timestamp", "event"))


def _redact_value(value: object, cfg: dict) -> object:
    """Recursively redact strings inside dicts and lists.

    audit_log previously only redacted top-level string fields, so an event
    like {"details": {"email": "u@example.com"}} or {"errors": ["...@..."]}
    landed in audit.jsonl with the email intact. Defense-in-depth: structured
    payloads from CLI shims and exception details can contain redact-eligible
    strings that the simple top-level loop walked past. Recurses through dict
    values and list/tuple elements; tuples are returned as lists because
    json.dumps emits both identically and the on-disk format must stay JSON.
    Non-string scalars (int/float/bool/None) pass through unchanged.
    """
    if isinstance(value, str):
        return redact_sensitive(value, cfg)
    if isinstance(value, dict):
        return {k: _redact_value(v, cfg) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(v, cfg) for v in value]
    return value


def audit_log(event: dict, config_path: str = "config.yaml", cfg: dict | None = None) -> None:
    if cfg is None:
        cfg = _get_config(config_path)
    log_path = _anchor(cfg["logging"]["audit_file"])
    audit_fields = cfg["logging"].get("audit_fields", {})
    record = dict(event)  # work on a shallow copy — never mutate the caller's dict
    if "query" in record and audit_fields.get("include_query_hash", True):
        raw_query = record.pop("query")
        record["query_hash"] = hash_query(raw_query)
    for key, value in list(record.items()):
        if key in _AUDIT_SKIP_KEYS:
            continue
        record[key] = _redact_value(value, cfg)
    record["timestamp"] = datetime.now(UTC).isoformat()
    line = json.dumps(record) + "\n"
    with _AUDIT_WRITE_LOCK:
        handle = _audit_handle(log_path)
        handle.write(line)
        handle.flush()
