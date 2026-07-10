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


@dataclass
class HealthStatus:
    name: str
    healthy: bool
    latency_ms: float | None = None
    error: str | None = None
