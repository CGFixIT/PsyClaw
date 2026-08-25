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
        # _capture_third_party attaches a FileHandler to the REAL root, and its
        # filter deliberately passes cyclaw.* through at any level -- so when it
        # attaches, that single handler already writes BOTH cyclaw and
        # third-party records to this path. A second FileHandler on the "cyclaw"
        # logger would then write every CyClaw line twice (once here, once at
        # root via propagation) and hold two fds on one file. Only own the file
        # directly when third-party capture is switched off and nothing else will.
        if not _capture_third_party(log_cfg, anchored_log_file, fmt):
            fh = logging.FileHandler(anchored_log_file, encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)

    _logging_initialized = True


# Loggers outside the "cyclaw" namespace: httpx, chromadb, uvicorn, langgraph.
# Their records never reach the handlers attached to the "cyclaw" logger above,
# so before this they went wherever the process's root logger happened to point
# -- in practice stderr, i.e. nowhere durable.
_THIRD_PARTY_DEFAULT_LEVEL = "INFO"


class _ThirdPartyFloor(logging.Filter):
    """Let ``cyclaw.*`` through at any level; hold everything else at a floor.

    This exists for a security reason, not a noise one. ``logging.level`` is now
    DEBUG so CyClaw's own modules are fully traced, but attaching that same
    level to third-party libraries would put ``httpcore``'s wire-level DEBUG
    output -- which includes the ``Authorization:`` header on every outbound
    Grok/Claude/Ollama call -- into a file on disk. CyClaw's own DEBUG lines
    were audited for this: the four in graph.py log chunk counts and budget
    arithmetic, never query text, prompts, or answers.
    """

    def __init__(self, floor: int) -> None:
        super().__init__()
        self.floor = floor

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "cyclaw" or record.name.startswith("cyclaw."):
            return True
        return record.levelno >= self.floor


def _capture_third_party(
    log_cfg: dict, log_path: Path, fmt: logging.Formatter,
) -> bool:
    """Route non-CyClaw loggers into the same file, at a safer level.

    Opt-out via ``logging.capture_third_party: false``. The floor is
    ``logging.third_party_level`` (default INFO) rather than the global DEBUG --
    see ``_ThirdPartyFloor`` for why that gap is deliberate.

    ONE FileHandler, on the real root logger. Records from cyclaw.* propagate up
    to root and ``_ThirdPartyFloor`` passes them at any level, so this handler is
    the file's single writer for both namespaces -- setup_logging deliberately
    does NOT also attach one to the "cyclaw" logger while this is active, or
    every CyClaw line would land in the file twice.

    Returns True when the handler was attached, False on the opt-out path, so
    the caller knows whether it still needs its own file handler.
    """
    if log_cfg.get("capture_third_party") is False:
        return False
    floor_name = str(log_cfg.get("third_party_level", _THIRD_PARTY_DEFAULT_LEVEL)).upper()
    floor = getattr(logging, floor_name, logging.INFO)

    real_root = logging.getLogger()
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(fmt)
    handler.addFilter(_ThirdPartyFloor(floor))
    # The handler's own level stays at the floor; the filter is what allows
    # cyclaw.* records through below it. Both are needed -- a handler level
    # above a record's level drops it before any filter runs.
    handler.setLevel(min(floor, logging.DEBUG))
    real_root.addHandler(handler)
    if real_root.level == logging.NOTSET or real_root.level > floor:
        real_root.setLevel(floor)
    return True

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
    redact_emails: bool,
    redact_ips: bool,
    secret_patterns: tuple[tuple[int, str], ...],
    invalid_secret_patterns: tuple[tuple[int, str], ...],
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
    for idx, pattern_type in invalid_secret_patterns:
        logger.warning(
            "privacy redaction pattern #%d has non-string type %s; it is "
            "skipped, so matching values pass through un-redacted until it "
            "is corrected.",
            idx, pattern_type,
        )
    for idx, pattern in secret_patterns:
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

def _resolve_redactors(cfg: dict) -> tuple[tuple[re.Pattern, str], ...]:
    """Resolve cfg's privacy settings to a compiled redactor tuple.

    Split out of redact_sensitive so a caller redacting many strings against
    the same cfg (audit_log's per-event walk over every field, recursing into
    nested dicts/lists) can resolve this once instead of re-deriving it --
    re-enumerating redact_secrets_like into two fresh tuples and re-hashing
    the 4-element lru_cache key -- for every string in the record.
    """
    privacy = cfg.get("policy", {}).get("privacy", {})
    configured_patterns = privacy.get("redact_secrets_like", []) or []
    return _compiled_redactors(
        privacy.get("redact_emails", False),
        privacy.get("redact_ips", False),
        tuple((idx, pattern) for idx, pattern in enumerate(configured_patterns) if isinstance(pattern, str)),
        tuple(
            (idx, type(pattern).__name__)
            for idx, pattern in enumerate(configured_patterns)
            if not isinstance(pattern, str)
        ),
    )


def redact_sensitive(text: str, cfg: dict | None = None) -> str:
    if cfg is None:
        cfg = _get_config()
    for pattern, replacement in _resolve_redactors(cfg):
        text = pattern.sub(replacement, text)
    return text


# Keys whose top-level value must NOT be redacted: query_hash is already a SHA-256
# digest, timestamp is structural ISO-8601, and event is the event-type tag.
# Applied only at the OUTER record level — nested fields named the same inside a
# dict/list value have no special meaning and pass through normal redaction.
_AUDIT_SKIP_KEYS = frozenset(("query_hash", "timestamp", "event"))


def _redact_value(value: object, redactors: tuple[tuple[re.Pattern, str], ...]) -> object:
    """Recursively redact strings inside dicts and lists.

    audit_log previously only redacted top-level string fields, so an event
    like {"details": {"email": "u@example.com"}} or {"errors": ["...@..."]}
    landed in audit.jsonl with the email intact. Defense-in-depth: structured
    payloads from CLI shims and exception details can contain redact-eligible
    strings that the simple top-level loop walked past. Recurses through dict
    values and list/tuple elements; tuples are returned as lists because
    json.dumps emits both identically and the on-disk format must stay JSON.
    Non-string scalars (int/float/bool/None) pass through unchanged.

    Takes the already-resolved redactor tuple (see _resolve_redactors) rather
    than cfg, so audit_log's recursive per-field walk resolves it once per
    event instead of once per string.
    """
    if isinstance(value, str):
        for pattern, replacement in redactors:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, dict):
        return {k: _redact_value(v, redactors) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(v, redactors) for v in value]
    return value


def audit_log(event: dict, config_path: str = "config.yaml", cfg: dict | None = None) -> None:
    if cfg is None:
        cfg = _get_config(config_path)
    log_path = _anchor(cfg["logging"]["audit_file"])
    audit_fields = cfg["logging"].get("audit_fields", {})
    try:
        record = dict(event)  # work on a shallow copy — never mutate the caller's dict
        if "query" in record and audit_fields.get("include_query_hash", True):
            raw_query = record.pop("query")
            record["query_hash"] = hash_query(raw_query)
        redactors = _resolve_redactors(cfg)
        for key, value in list(record.items()):
            if key in _AUDIT_SKIP_KEYS:
                continue
            record[key] = _redact_value(value, redactors)
        record["timestamp"] = datetime.now(UTC).isoformat()
        line = json.dumps(record) + "\n"
    except (TypeError, ValueError, AttributeError, UnicodeError) as exc:
        # audit_logger is the unconditional terminal node every graph path
        # converges on (invariant I4) -- it runs AFTER the answer is already
        # computed. This function has ~100 call sites across the repo, several
        # passing through caller-supplied **fields; a non-string "query" value
        # (hash_query()'s .encode('utf-8') would raise) or any non-JSON-
        # serializable field anywhere in `event` must not turn an already-good
        # response into an HTTP 500 purely because the audit trail couldn't be
        # built. Same rationale as the OSError guard below, extended to cover
        # record-building/serialization, not just the write.
        logger.warning("audit_log failed to build event %r: %s", event.get("event"), exc)
        return
    try:
        with _AUDIT_WRITE_LOCK:
            handle = _audit_handle(log_path)
            handle.write(line)
            handle.flush()
    except OSError as exc:
        # Letting a disk-full/permission failure here escape would turn an
        # already-good response into an HTTP 500 purely because the audit
        # trail couldn't be persisted. Degrade loudly to the app log instead
        # of raising; the caller still gets its answer.
        logger.warning("audit_log write failed for %s: %s", log_path, exc)
    # Derived Numbat NDJSON projection (top-level numbat: block, shipped
    # enabled). audit.jsonl stays authoritative; the projection is fail-soft
    # and independent. Lazy import: utils/numbat_emitter.py imports _anchor
    # and _get_config from THIS module, so a top-level import would be
    # circular; keeping it inside the call also means gate.py/graph.py gain
    # no import-time numbat surface (I6 hygiene).
    #
    # The import itself is inside the guard, not just the call: it is the one
    # statement here that runs outside project_audit_record's own internal
    # Exception handler, and a derived stream must never be able to raise out
    # of the terminal audit step. Same rationale as the two guards above -- a
    # failure to project must not turn an already-good response into an
    # HTTP 500.
    try:
        from utils.numbat_emitter import project_audit_record

        project_audit_record(record, cfg=cfg)
    except Exception as exc:  # noqa: BLE001 -- derived stream, never fatal
        logger.warning("numbat projection failed for %r: %s", record.get("event"), exc)
