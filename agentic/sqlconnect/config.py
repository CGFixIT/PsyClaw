"""SqlConnectConfig dataclass and validating loader for the ``sqlconnect:`` block.

Disabled-by-default scaffold: absence of the block disables the SQL connector
entirely. v0.1 is read-only and cannot write regardless of config -- ``allow_write``
must be False and ``read_only`` must be True (a config error otherwise, fail-closed).

Never imported by gate.py / graph.py / mcp_hybrid_server.py.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from utils.errors import SqlConnectConfigError
from utils.logger import _get_config

VALID_DRIVERS = ("postgres", "mssql")
# All read-only. explain (Postgres-only) and row_count are diagnostic helpers
# added alongside the original three; every op is gated again at call time by
# SqlClient._guard_op against the operator's allowed_sql_ops.
DEFAULT_ALLOWED_SQL_OPS = ("schema_list", "table_preview", "run_select", "explain", "row_count")
VALID_SQL_OPS = frozenset(DEFAULT_ALLOWED_SQL_OPS)


@dataclass
class SqlConnectConfig:
    """Parsed and validated sqlconnect: block from config.yaml."""

    driver: str = "postgres"
    dsn_env: str = "CYCLAW_SQL_DSN"
    allowed_sql_ops: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_SQL_OPS))
    read_only: bool = True
    statement_timeout_ms: int = 5000
    max_rows: int = 1000
    allow_write: bool = False

    def __post_init__(self) -> None:
        if self.driver not in VALID_DRIVERS:
            raise SqlConnectConfigError(
                f"sqlconnect.driver must be one of {VALID_DRIVERS}, got: {self.driver!r}",
                details={"received": self.driver, "valid": list(VALID_DRIVERS)},
            )
        if not isinstance(self.dsn_env, str) or not self.dsn_env.strip():
            raise SqlConnectConfigError("sqlconnect.dsn_env must be a non-empty string")
        bad = [op for op in self.allowed_sql_ops if op not in VALID_SQL_OPS]
        if bad:
            raise SqlConnectConfigError(
                f"sqlconnect.allowed_sql_ops contains unknown ops: {bad!r}",
                details={"unknown": bad, "valid": sorted(VALID_SQL_OPS)},
            )
        for name in ("statement_timeout_ms", "max_rows"):
            val = getattr(self, name)
            if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
                raise SqlConnectConfigError(
                    f"sqlconnect.{name} must be a positive integer, got: {val!r}",
                    details={"field": name},
                )
        # v0.1 is read-only by construction -- refuse to start otherwise (fail-closed).
        if not self.read_only:
            raise SqlConnectConfigError("sqlconnect.read_only must be true in v0.1")
        if self.allow_write:
            raise SqlConnectConfigError("sqlconnect.allow_write must be false in v0.1")

    def to_dict(self) -> dict:
        return asdict(self)


def load_sqlconnect_config(config_path: str = "config.yaml") -> SqlConnectConfig:
    """Read config.yaml's sqlconnect: block and return a validated SqlConnectConfig."""
    cfg = _get_config(config_path) or {}
    block = cfg.get("sqlconnect")
    if not block:
        raise SqlConnectConfigError(
            "sqlconnect: block missing from config.yaml",
            details={"hint": "Append the sqlconnect: block; see agentic/sqlconnect/config.py."},
        )
    if not isinstance(block, dict):
        raise SqlConnectConfigError(
            f"sqlconnect: block must be a mapping, got {type(block).__name__}",
        )
    for name in ("enabled", "read_only", "allow_write"):
        if name in block and not isinstance(block[name], bool):
            raise SqlConnectConfigError(
                f"sqlconnect.{name} must be a boolean true/false, got: {block[name]!r}",
                details={"field": name, "received": repr(block[name])},
            )
    known = set(SqlConnectConfig.__dataclass_fields__)
    unknown = set(block.keys()) - known
    unknown.discard("enabled")
    kwargs = {k: v for k, v in block.items() if k in known}
    try:
        sc = SqlConnectConfig(**kwargs)
    except TypeError as exc:
        raise SqlConnectConfigError(
            f"sqlconnect: block invalid: {exc}", details={"unknown_keys": sorted(unknown)}
        ) from exc
    sc.enabled = block.get("enabled", False)  # type: ignore[attr-defined]
    sc._unknown_keys = sorted(unknown)  # type: ignore[attr-defined]
    return sc
