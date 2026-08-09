"""Structured error types for the CyClaw RAG pipeline.

Typed exceptions with code + details fields that the gateway
catches and maps to proper HTTP responses.
"""

from dataclasses import dataclass


class RAGError(Exception):
    def __init__(self, message: str, code: str = "RAG_ERROR", details: dict | None = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)

class EmbeddingServiceError(RAGError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="EMBEDDING_ERROR", details=details)

class LLMServiceError(RAGError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="LLM_SERVICE_ERROR", details=details)

class GrokServiceError(RAGError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="GROK_SERVICE_ERROR", details=details)

class ClaudeServiceError(RAGError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="CLAUDE_SERVICE_ERROR", details=details)

class IndexNotFoundError(RAGError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="INDEX_NOT_FOUND", details=details)

class CorpusEmptyError(RAGError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="CORPUS_EMPTY", details=details)

class PromptInjectionError(RAGError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="PROMPT_INJECTION_BLOCKED", details=details)


class SoulPersistenceError(RAGError):
    """A soul update failed and its filesystem compensation was incomplete."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="SOUL_PERSISTENCE_INCONSISTENT", details=details)


class ConfigError(RAGError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="CONFIG_ERROR", details=details)

class SyncError(RAGError):
    """Base error for out-of-band Dropbox corpus sync operations."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="SYNC_ERROR", details=details)


class RcloneNotInstalledError(SyncError):
    """rclone binary not found on PATH."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, details=details)
        self.code = "RCLONE_NOT_INSTALLED"


class RcloneVersionError(SyncError):
    """rclone is installed but the version is below the required floor."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, details=details)
        self.code = "RCLONE_VERSION_TOO_OLD"


class RcloneTimeoutError(SyncError):
    """rclone is installed but the version check timed out (binary stalled)."""

    def __init__(self, message: str, details: dict | None = None):
        # Call RAGError.__init__ directly with the right code so we never
        # overwrite an attribute already set by a parent __init__ call.
        # SyncError.__init__ hardcodes code="SYNC_ERROR" and provides no way
        # to pass a sub-code through, so bypassing it is intentional here.
        RAGError.__init__(self, message, code="RCLONE_TIMEOUT", details=details)


class SyncConfigError(SyncError):
    """The sync: block in config.yaml is missing or invalid."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, details=details)
        self.code = "SYNC_CONFIG_INVALID"


class SchedulerError(SyncError):
    """Cron / systemd / launchd / Task Scheduler registration or removal failed."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, details=details)
        self.code = "SYNC_SCHEDULER_ERROR"


class SyncRuntimeError(SyncError):
    """rclone subprocess failed at runtime (non-zero exit, safety-fuse abort, etc.)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, details=details)
        self.code = "SYNC_RUNTIME_ERROR"


class AgenticError(RAGError):
    """Base error for the out-of-band agentic (GitHub-context / skills) layer.

    Mirrors the SyncError convention: a dedicated hierarchy for a strictly
    out-of-band feature that is never imported by gate.py / graph.py /
    mcp_hybrid_server.py, so the gateway can stay oblivious to it.
    """

    def __init__(self, message: str, code: str = "AGENTIC_ERROR", details: dict | None = None):
        super().__init__(message, code=code, details=details)


def require_non_empty(value: str, field_name: str) -> None:
    """Raise AgenticError unless `value` is a non-empty (post-strip) string.

    Shared validator for the agentic layer's frozen dataclasses (harness_optimizer,
    deepagent_github); was copy-pasted identically across three modules before
    being consolidated here.
    """

    if not isinstance(value, str) or not value.strip():
        raise AgenticError(f"{field_name} must be a non-empty string", details={"field": field_name})


class GhNotInstalledError(AgenticError):
    """The GitHub CLI (`gh`) was not found on PATH."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="GH_NOT_INSTALLED", details=details)


class GhVersionError(AgenticError):
    """`gh` is installed but below the required version floor (or unparseable)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="GH_VERSION_TOO_OLD", details=details)


class AgenticConfigError(AgenticError):
    """The agentic: block in config.yaml is missing or invalid."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="AGENTIC_CONFIG_INVALID", details=details)


class AgenticWriteRefused(AgenticError):
    """A write was refused because the triple-gate (mode + flag + reason + confirm) failed.

    v0.1 never executes writes regardless; this is raised when a caller asks for a
    write plan without satisfying every gate.
    """

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="AGENTIC_WRITE_REFUSED", details=details)


class SkillRegistryError(AgenticError):
    """The governed skills registry could not load, validate, or apply a change."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="SKILL_REGISTRY_ERROR", details=details)


class FsConnectError(RAGError):
    """Base error for the out-of-band filesystem connector (agentic/fsconnect).

    Mirrors the AgenticError / SyncError convention: a dedicated hierarchy for a
    strictly out-of-band feature that is never imported by gate.py / graph.py /
    mcp_hybrid_server.py.
    """

    def __init__(self, message: str, code: str = "FSCONNECT_ERROR", details: dict | None = None):
        super().__init__(message, code=code, details=details)


class FsConnectConfigError(FsConnectError):
    """The fsconnect: block in config.yaml is missing or invalid."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="FSCONNECT_CONFIG_INVALID", details=details)


class FsPathError(FsConnectError):
    """A path failed the pathsafe containment check (escape, reparse point, ADS, etc.).

    Raised by the security core whenever a requested target cannot be proven to
    resolve inside an allow-listed root. Always fail-closed.
    """

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="FSCONNECT_PATH_DENIED", details=details)


class FsWriteRefused(FsConnectError):
    """A write was refused because a gate (writes_enabled / reason / confirm) failed.

    The connector is content-agnostic and confined to writable_roots; this is the
    out-of-band analogue of the agentic write gate, applied to the local filesystem.
    """

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="FSCONNECT_WRITE_REFUSED", details=details)


class FsConnectRuntimeError(FsConnectError):
    """A read/write/index filesystem operation failed at runtime (I/O error, cap, etc.)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="FSCONNECT_RUNTIME_ERROR", details=details)


class SqlConnectError(RAGError):
    """Base error for the out-of-band read-only SQL connector (agentic/sqlconnect).

    Disabled-by-default scaffold; never imported by gate.py / graph.py /
    mcp_hybrid_server.py.
    """

    def __init__(self, message: str, code: str = "SQLCONNECT_ERROR", details: dict | None = None):
        super().__init__(message, code=code, details=details)


class SqlConnectConfigError(SqlConnectError):
    """The sqlconnect: block in config.yaml is missing or invalid."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="SQLCONNECT_CONFIG_INVALID", details=details)


class SqlDriverNotInstalledError(SqlConnectError):
    """The configured SQL driver (psycopg / pyodbc) is not importable."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="SQL_DRIVER_NOT_INSTALLED", details=details)


class SqlConnectRuntimeError(SqlConnectError):
    """A SQL operation failed at runtime (connection error, query error, timeout)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="SQLCONNECT_RUNTIME_ERROR", details=details)


class TelegramError(RAGError):
    """Base error for the out-of-band Telegram channel layer.

    Mirrors SyncError / AgenticError: a dedicated hierarchy for a strictly
    out-of-band feature that is never imported by gate.py / graph.py /
    mcp_hybrid_server.py.
    """

    def __init__(self, message: str, code: str = "TELEGRAM_ERROR", details: dict | None = None):
        super().__init__(message, code=code, details=details)


class TelegramConfigError(TelegramError):
    """The telegram: block in config.yaml is missing or invalid."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="TELEGRAM_CONFIG_INVALID", details=details)


class TelegramRefused(TelegramError):
    """A Telegram operation was refused by a gate (allowlist, mode, rate limit)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="TELEGRAM_REFUSED", details=details)


class TelegramRuntimeError(TelegramError):
    """A Telegram or CyClaw HTTP call failed at runtime."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="TELEGRAM_RUNTIME_ERROR", details=details)


class AuthError(RAGError):
    """Base error for the per-user authentication layer
    (docs/AUTHENTICATION_DESIGN.md). Stage 1 primitives live in utils/authn.py,
    the store in utils/authn_store.py, and the manager tying them together in
    utils/authn_manager.py -- all reachable from gate.py, unlike the
    out-of-band hierarchies above, since this IS core request-path auth.
    """

    def __init__(self, message: str, code: str = "AUTH_ERROR", details: dict | None = None):
        super().__init__(message, code=code, details=details)


class AuthConfigError(AuthError):
    """The auth: block in config.yaml is missing or invalid."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="AUTH_CONFIG_INVALID", details=details)


class AuthLoginFailed(AuthError):
    """Invalid username or password.

    Deliberately the SAME error, message, and status for an unknown username,
    a wrong password, and a disabled account -- distinguishing any of them
    would let a caller enumerate valid usernames or account state.
    """

    def __init__(self, message: str = "invalid username or password", details: dict | None = None):
        super().__init__(message, code="AUTH_LOGIN_FAILED", details=details)


class AuthAccountLocked(AuthError):
    """Too many consecutive failures; locked out until retry_after_sec elapses.

    A distinct error from AuthLoginFailed on purpose: the client needs the
    retry delay to back off correctly, the same reason the existing per-IP
    rate limiter's 429 carries a concrete number rather than a generic denial.

    Accepted, known tradeoff: being distinct is itself a small username-
    enumeration signal -- only an account that EXISTS accumulates
    failed_count and can ever transition from AuthLoginFailed (401) to this
    (423), so five failed attempts against a genuine username eventually
    look different from five against a nonexistent one, even though any
    single attempt's response is identical either way (AuthManager.login's
    unknown-username path pays the same scrypt cost via _DUMMY_RECORD). This
    is deliberate, not an oversight: the alternative -- returning 401 forever
    regardless of lockout state -- would deny the legitimate caller the
    retry-delay information they need, for a channel that costs an attacker
    five real attempts per username just to open one bit of information
    (exists / does not exist), against a system whose real secret is the
    password, not the username's existence. The same tradeoff every major
    login system with visible lockout UX makes.
    """

    def __init__(self, message: str, retry_after_sec: float, details: dict | None = None):
        full_details = {**(details or {}), "retry_after_sec": retry_after_sec}
        super().__init__(message, code="AUTH_ACCOUNT_LOCKED", details=full_details)
        self.retry_after_sec = retry_after_sec


class AuthUserExists(AuthError):
    """create_user was called with a username that already has a row."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="AUTH_USER_EXISTS", details=details)


class AuthUserNotFound(AuthError):
    """An admin operation (disable/enable/passwd/token) targeted an unknown user."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="AUTH_USER_NOT_FOUND", details=details)


class AuthTokenLabelExists(AuthError):
    """create_device_token was called with a label the account already has live.

    A label is the ONLY handle the CLI offers for revoking a device token, so
    two live tokens sharing one would make `token revoke` ambiguous -- it
    matches on (username, label) and would kill both, with no way to target
    one. Refusing the duplicate at creation keeps "one label, one token" true.
    Labels are reusable after revocation; only LIVE ones must be distinct.
    """

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="AUTH_TOKEN_LABEL_EXISTS", details=details)


@dataclass
class HealthStatus:
    name: str
    healthy: bool
    latency_ms: float | None = None
    error: str | None = None
