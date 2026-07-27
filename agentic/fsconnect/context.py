"""Higher-level read bundlers over :class:`agentic.fsconnect.client.FsClient`.

Thin convenience layer for the CLI: each function opens a short-lived client,
dispatches one allow-listed read op (the op-allow-list guard lives in the client),
and returns a JSON-serializable dict. Never imported by gate.py / graph.py /
mcp_hybrid_server.py.
"""

from __future__ import annotations

from agentic.fsconnect.client import FsClient
from agentic.fsconnect.config import FsConnectConfig
from utils.errors import FsConnectError


def run_read(
    cfg: dict,
    fs_cfg: FsConnectConfig,
    op: str,
    *,
    config_path: str = "config.yaml",
    target: str = "",
    root: str | None = None,
    pattern: str | None = None,
    regex: bool = False,
    recursive: bool = True,
) -> dict:
    """Open a client, run one read op, and return its result bundle."""
    with FsClient(cfg, fs_cfg, config_path=config_path) as client:
        if op == "fs_list":
            return client.fs_list(target, root=root)
        if op == "fs_stat":
            return client.fs_stat(target, root=root)
        if op == "fs_read":
            return client.fs_read(target, root=root)
        if op == "fs_grep":
            return client.fs_grep(target, pattern or "", root=root, regex=regex)
        if op == "fs_glob":
            return client.fs_glob(target, pattern or "", root=root, recursive=recursive)
        # Typed, not ValueError: fsconnect/cli.py's _run_read() only catches
        # FsConnectError, so a bare builtin would escape as an uncaught traceback
        # (exit 1) instead of the documented fsconnect exit-code API. Mirrors
        # sqlconnect/context.py's run_op() for the same reason.
        raise FsConnectError(
            f"unknown read op: {op!r}",
            code="FSCONNECT_BAD_ARG",
            details={"op": op},
        )


def overview(cfg: dict, fs_cfg: FsConnectConfig, *, config_path: str = "config.yaml") -> dict:
    """A small bundle: each allowed root with its top-level entries (best effort)."""
    out: list[dict] = []
    with FsClient(cfg, fs_cfg, config_path=config_path) as client:
        for sr in client._roots.roots:
            try:
                listing = client.fs_list("", root=sr.requested)
                out.append({"root": sr.requested, "resolved": str(sr.path),
                            "count": listing["count"], "entries": listing["entries"][:50]})
            except Exception as exc:  # noqa: BLE001 -- overview is best-effort per root
                out.append({"root": sr.requested, "error": str(exc)})
    return {"op": "overview", "roots": out}


__all__ = ["run_read", "overview"]
