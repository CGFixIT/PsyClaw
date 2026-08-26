"""Allowlisted dotenv secret store behind the harness console's ``/api`` panel.

Writes the SAME file ``macos/setup-cyclaw-keys.sh`` manages --
``$CYCLAW_HOME/.env`` (default ``~/.CyClaw/.env``, mode 600 on POSIX) -- in the same
``export KEY='value'`` form, so the shell installer and the web panel can be
used interchangeably without one corrupting the other's file.

Three properties this module exists to guarantee, none of which a caller
should have to remember:

1. **Allowlist, not free-form env.** ``MANAGED_KEYS`` is a closed set. An
   arbitrary name reaching a dotenv writer is an environment-injection
   primitive (``PATH``, ``LD_PRELOAD``, ``PYTHONPATH``), so a name outside the
   set is refused rather than sanitised.
2. **Values never travel back out.** ``read_status`` reports presence and a
   masked tail only. Nothing in this module returns or logs a secret, which is
   what lets the route above it be audited by key NAME without a redaction pass.
3. **The file is never half-written.** Writes go to a sibling temp file created
   mode 600 and are moved into place with ``os.replace``, so a crash mid-write
   leaves the previous file intact rather than a truncated one -- and the
   secret is never briefly world-readable. The atomicity holds everywhere; the
   mode bits are POSIX-only (see ``_FILE_MODE``).

Deliberately NOT in scope: loading these values into the running process.
Nothing in CyClaw reads ``.env`` at runtime (there is no ``python-dotenv``
dependency); the file is sourced by the operator's shell, so a write here
takes effect on the next start of ``gate.py``. ``write_keys`` reports that
rather than pretending otherwise -- see ``docs/HARNESS_API_KEYS.md``.
"""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

# Mirrors harness/config.py's _HOME_ENV resolution rather than importing it:
# this module is deliberately importable without a HarnessConfig (the tests
# drive it with a tmp_path home, and gate-side tooling may want it later).
_HOME_ENV = "CYCLAW_HOME"
_PROFILE_ENV = "USERPROFILE"
_HOME_DIRNAME = ".CyClaw"
_ENV_FILENAME = ".env"

# 0600 / 0700: the file holds live credentials, so group and other get nothing.
# POSIX only. Windows os.chmod() honours just the read-only bit -- it cannot
# express an owner-only mode, so a file written here reports 0o666 there. The
# equivalent confinement on Windows comes from the NTFS ACL %USERPROFILE%\.CyClaw
# inherits (owner + SYSTEM + Administrators), not from these bits. Stated so a
# reader does not take the chmod below for a cross-platform confidentiality gate.
_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR
_DIR_MODE = stat.S_IRWXU

# A dotenv line is newline-delimited, so a value containing CR or LF could
# forge additional assignments. Refused rather than escaped -- no legitimate
# API key or database URL contains one.
_FORBIDDEN_CHARS = ("\n", "\r", "\x00")

# Generous next to any real credential (a JWT-shaped token is well under this)
# while still bounding what a single request can append to the file.
_MAX_VALUE_LEN = 4096

# Below this length a tail would disclose most of the secret, so the mask shows
# nothing but its shape. Twelve is comfortably under every credential CyClaw
# actually uses and comfortably over a value where 4 chars would be most of it.
_MIN_LEN_FOR_TAIL = 12
_TAIL_LEN = 4
_MASK_CHAR = "•"
_MASK_WIDTH = 8

# How a literal single quote is represented inside a POSIX single-quoted
# string: close, escape, reopen. Byte-identical to what setup-cyclaw-keys.sh's
# own _shell_single_quote emits, so the two writers agree on the same file.
_QUOTE_ESCAPE = r"'\''"

# An opening and a closing quote: the shortest possible quoted token ("").
_MIN_QUOTED_LEN = 2

_HEADER_LINES = (
    "# CyClaw secrets - chmod 600. Managed by macos/setup-cyclaw-keys.sh",
    "# and the harness console's /api panel.",
    "# Do not commit. Do not copy into config.yaml. Do not paste into chat logs.",
)


@dataclass(frozen=True)
class KeySpec:
    """One settable credential: its env var, a UI label, and why it exists."""

    name: str
    label: str
    detail: str
    # True for the credential that guards the very route setting it. The route
    # refuses to apply it live for that reason; see write_keys' return value.
    self_auth: bool = False


# Every entry is an env var this repository actually reads. Verified by grep
# over os.environ.get/os.getenv rather than assembled from documentation, so
# the panel cannot offer a key that nothing consumes.
MANAGED_KEYS: tuple[KeySpec, ...] = (
    KeySpec(
        name="CYCLAW_API_KEY",
        label="CyClaw API key",
        detail="Bearer secret for /soul/*, /ops/*, /memory/*, /audit/summary and this console.",
        self_auth=True,
    ),
    KeySpec(
        name="GROK_API_KEY",
        label="Grok (xAI)",
        detail="Online fallback provider. Still triple-gated: hybrid mode + enabled + per-query confirmation.",
    ),
    KeySpec(
        name="ANTHROPIC_API_KEY",
        label="Claude (Anthropic)",
        detail="Second online fallback provider. Same triple gate as Grok.",
    ),
    KeySpec(
        name="DEEPAGENT_API_KEY",
        label="Cloud planner",
        detail="Optional cloud planner for the agentic real-repo loop (default disarmed).",
    ),
    KeySpec(
        name="TELEGRAM_BOT_TOKEN",
        label="Telegram bot token",
        detail="Out-of-band Telegram channel (telegram.enabled ships false).",
    ),
    KeySpec(
        name="CYCLAW_DB_URL",
        label="Personality DB URL",
        detail="Postgres URL for the soul store. May embed a password, so it lives here not in config.yaml.",
    ),
    KeySpec(
        name="CYCLAW_AUTH_DB_URL",
        label="Auth DB URL",
        detail="Postgres URL for users/sessions/device tokens.",
    ),
    KeySpec(
        name="CYCLAW_RATELIMIT_DB_URL",
        label="Rate-limit DB URL",
        detail="Postgres URL for per-IP rate-limit counters.",
    ),
    KeySpec(
        name="CYCLAW_VECTOR_DB_URL",
        label="Vector DB URL",
        detail="pgvector URL when not using the embedded ChromaDB reader/writer.",
    ),
)

_KEYS_BY_NAME = MappingProxyType({spec.name: spec for spec in MANAGED_KEYS})


class EnvKeyError(ValueError):
    """A key name outside the allowlist, or a value that cannot be stored."""


def home_dir() -> Path:
    """Per-user CyClaw home. Same precedence as harness/config.py's resolver."""
    override = os.environ.get(_HOME_ENV, "").strip()
    if override:
        return Path(override)
    profile = os.environ.get(_PROFILE_ENV, "").strip()
    if profile:
        return Path(profile) / _HOME_DIRNAME
    return Path.home() / _HOME_DIRNAME


def env_file_path() -> Path:
    """Absolute path of the dotenv file the panel reads and writes."""
    return home_dir() / _ENV_FILENAME


def spec_for(name: str) -> KeySpec:
    """Look up an allowlisted key, or raise ``EnvKeyError`` for anything else."""
    spec = _KEYS_BY_NAME.get(name)
    if spec is None:
        raise EnvKeyError(f"{name!r} is not a settable CyClaw key")
    return spec


def validate_value(name: str, secret: str) -> str:
    """Return the storable form of ``secret``, or raise ``EnvKeyError``.

    Surrounding whitespace is stripped because it is nearly always a paste
    artifact and an API key with a trailing newline is an illegal HTTP header
    value (the same failure utils/health.py strips for on the read side).
    """
    spec_for(name)
    cleaned = secret.strip()
    if not cleaned:
        raise EnvKeyError(f"{name} value is empty")
    for char in _FORBIDDEN_CHARS:
        if char in cleaned:
            raise EnvKeyError(f"{name} value contains a forbidden control character")
    if len(cleaned) > _MAX_VALUE_LEN:
        raise EnvKeyError(f"{name} value exceeds {_MAX_VALUE_LEN} characters")
    return cleaned


def mask(secret: str) -> str:
    """Render a secret as a fixed-width mask plus, when safe, its last 4 chars.

    Never returns the value. Short secrets get no tail at all, so the mask
    cannot become most of a weak credential.
    """
    bullets = _MASK_CHAR * _MASK_WIDTH
    if len(secret) < _MIN_LEN_FOR_TAIL:
        return bullets
    tail = secret[-_TAIL_LEN:]
    return f"{bullets}{tail}"


def _shell_single_quote(secret: str) -> str:
    r"""POSIX single-quote a value, matching setup-cyclaw-keys.sh's quoting.

    A literal ``'`` is closed, escaped, and reopened (``'\\''``) -- the only
    way to represent one inside single quotes, and what the shell script's
    own ``_shell_single_quote`` emits, so the two writers agree byte for byte.
    """
    escaped = secret.replace("'", _QUOTE_ESCAPE)
    return f"'{escaped}'"


def _is_quoted(token: str) -> bool:
    """True when ``token`` is wrapped in one matching pair of quotes."""
    if len(token) < _MIN_QUOTED_LEN:
        return False
    return token[0] == token[-1] and token[0] in {"'", '"'}


def _unquote(raw: str) -> str:
    """Inverse of _shell_single_quote, tolerant of a hand-edited file.

    Accepts a bare token or one pair of single/double quotes, mirroring the
    shell script's ``_env_unquote``.
    """
    token = raw.strip()
    if not _is_quoted(token):
        return token
    inner = token[1:-1]
    if token.startswith("'"):
        return inner.replace(_QUOTE_ESCAPE, "'")
    return inner


def _split_assignment(line: str) -> tuple[str, str] | None:
    """Parse one dotenv line into (name, raw_value), or None if it is not one.

    Accepts both ``KEY=`` and ``export KEY=`` forms on the way in; comments and
    blank lines are not assignments.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export "):].lstrip()
    name, separator, raw_value = stripped.partition("=")
    if not separator:
        return None
    return name.strip(), raw_value


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """Parse the dotenv file into {name: value} for allowlisted keys only.

    Values are returned so callers can mask them; no caller may forward them to
    a response body. A missing or unreadable file reads as empty rather than
    raising -- an absent .env is the normal first-run state, not an error.
    """
    target = path or env_file_path()
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    found: dict[str, str] = {}
    for line in text.splitlines():
        parsed = _split_assignment(line)
        if parsed is None:
            continue
        name, raw_value = parsed
        if name in _KEYS_BY_NAME:
            found[name] = _unquote(raw_value)
    return found


def read_status(path: Path | None = None) -> list[dict[str, object]]:
    """Presence + masked tail for every managed key. Never returns a value.

    ``source`` distinguishes a key the running process actually has (``env``)
    from one that is only written to the file (``file``). They differ exactly
    when a key was saved here but the process has not been restarted, which is
    the state the panel most needs to make visible.
    """
    stored = read_env_file(path)
    rows: list[dict[str, object]] = []
    for spec in MANAGED_KEYS:
        live = os.environ.get(spec.name, "").strip()
        in_file = stored.get(spec.name, "")
        value_for_mask = live or in_file
        if live:
            source = "env"
        elif in_file:
            source = "file"
        else:
            source = "unset"
        rows.append({
            "name": spec.name,
            "label": spec.label,
            "detail": spec.detail,
            "self_auth": spec.self_auth,
            "configured": bool(value_for_mask),
            "masked": mask(value_for_mask) if value_for_mask else "",
            "source": source,
            # True when the file holds a value the running process does not
            # have (or a different one) -- i.e. a restart would change behavior.
            "pending_restart": bool(in_file) and in_file != live,
        })
    return rows


def _render_file(existing_lines: list[str], updates: dict[str, str]) -> str:
    """Rebuild the file with ``updates`` applied, preserving everything else.

    Unrelated assignments and comments survive verbatim -- the file is shared
    with setup-cyclaw-keys.sh, which writes keys this panel does not manage.
    """
    replaced: set[str] = set()
    kept: list[str] = []
    for line in existing_lines:
        parsed = _split_assignment(line)
        if parsed is not None and parsed[0] in updates:
            # Drop the old assignment; the new one is appended in order below.
            replaced.add(parsed[0])
            continue
        kept.append(line)
    if not kept:
        kept = list(_HEADER_LINES)
    for name, secret in updates.items():
        quoted = _shell_single_quote(secret)
        kept.append(f"export {name}={quoted}")
    body = "\n".join(kept)
    return f"{body}\n"


def _write_temp_file(directory: Path, rendered: str) -> Path:
    """Write ``rendered`` to a fresh 0600 temp file beside the target.

    Same directory so the caller's ``os.replace`` is a same-filesystem rename
    (atomic). ``mkstemp`` creates the file 0600 already, so the secret is never
    momentarily readable by anyone else; the explicit chmod only restates it
    against a permissive inherited umask.
    """
    descriptor, temp_name = tempfile.mkstemp(prefix=".env.", dir=str(directory))
    temp_path = Path(temp_name)
    # The staged file exists on disk the moment mkstemp returns and already
    # holds every managed secret (_render_file re-emits existing keys), so
    # EVERY failure path between here and the caller's os.replace has to
    # remove it or a secret-bearing orphan is left in ~/.CyClaw/ forever --
    # same idiom (and same BaseException, for a KeyboardInterrupt mid-write)
    # as harness/config.py's _atomic_write_json.
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)
        temp_path.chmod(_FILE_MODE)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def write_keys(updates: dict[str, str], path: Path | None = None) -> dict[str, object]:
    """Validate and atomically persist ``updates``. Returns names only.

    Every value is validated BEFORE anything is written, so a rejected key in
    the batch cannot leave a partial update on disk.

    The return value carries no secrets -- only which names were written and
    the fact that a restart is required for them to reach ``gate.py``. Callers
    may audit-log this dict as-is.
    """
    if not updates:
        raise EnvKeyError("no keys supplied")
    cleaned = {name: validate_value(name, secret) for name, secret in updates.items()}

    target = path or env_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Best-effort: a pre-existing home may be group-readable, and tightening it
    # must not fail the write on a platform that disallows the chmod.
    try:
        target.parent.chmod(_DIR_MODE)
    except OSError:
        pass  # noqa: WPS420 - permission hardening is advisory, not a precondition

    try:
        existing = target.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        existing = []

    rendered = _render_file(existing, cleaned)

    # Temp file in the SAME directory so os.replace is a same-filesystem rename
    # (atomic); mkstemp creates it 0600 already, which is why the secret is
    # never briefly readable by anyone else.
    temp_path = _write_temp_file(target.parent, rendered)
    try:
        os.replace(temp_path, target)
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise
    target.chmod(_FILE_MODE)

    return {
        "written": sorted(cleaned),
        "path": str(target),
        # Nothing in CyClaw reads .env at runtime, so a write here cannot
        # affect the live gate.py process. Stated as data, not prose, so the
        # console can render it without inferring it.
        "restart_required": True,
        "self_auth_written": sorted(
            name for name in cleaned if _KEYS_BY_NAME[name].self_auth
        ),
    }
