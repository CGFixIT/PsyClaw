"""Read operations for the filesystem connector, over the pathsafe security core.

Exposes the read allow-list -- ``fs_list``, ``fs_stat``, ``fs_read``, ``fs_grep`` --
each scoped to ``allowed_roots`` via :class:`agentic.fsconnect.pathsafe.ScopedRoots`,
audited per call, and (for content) optionally scanned for prompt-injection patterns
(advisory only -- file content is untrusted *data*, never an instruction, so a flag
never blocks a read; it just surfaces risk to the operator/RAG pipeline downstream).

The injection scanner reuses the SAME ``OWASP ∪ policy.prompt_filter.banned_patterns``
union the soul/registry scanners use, so the three never drift.

Never imported by gate.py / graph.py / mcp_hybrid_server.py.
"""

from __future__ import annotations

import fnmatch
import re

from agentic.fsconnect.config import FsConnectConfig
from agentic.fsconnect.pathsafe import ScopedRoots
from guardrails.rails import build_injection_patterns as _shared_build_injection_patterns
from utils.errors import FsConnectError
from utils.logger import audit_log

_MAX_GREP_MATCHES = 200
_MAX_GLOB_MATCHES = 1000


def build_injection_patterns(cfg: dict) -> list[tuple[str, re.Pattern[str]]]:
    """Compile ``OWASP ∪ policy.prompt_filter.banned_patterns`` (advisory scanner).

    Thin re-export of the shared builder in ``guardrails.rails`` -- the pattern
    union, dedup order, IGNORECASE flag, and memoization all live there now so
    this scanner cannot drift from the skills-registry and harness-optimizer
    ones. The ADVISORY posture stays here: guardrails owns how the pattern set is
    built, never whether a given caller blocks on a hit.
    """
    return _shared_build_injection_patterns(cfg)


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:4096]


class FsClient:
    """Read-only filesystem client bound to a config's ``allowed_roots``.

    Use as a context manager so the held root directory fds are released::

        with FsClient(cfg, fs_cfg) as client:
            client.fs_read("notes.txt")
    """

    def __init__(self, cfg: dict, fs_cfg: FsConnectConfig, config_path: str = "config.yaml") -> None:
        self.cfg = cfg
        self.fs_cfg = fs_cfg
        self.config_path = config_path
        self._roots = ScopedRoots(
            fs_cfg.allowed_roots, create=False, allow_unc=fs_cfg.allow_unc_roots
        )
        self._patterns = build_injection_patterns(cfg) if fs_cfg.scan_content else []

    # --- lifecycle --------------------------------------------------------

    def close(self) -> None:
        self._roots.close()

    def __enter__(self) -> FsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- guards / helpers -------------------------------------------------

    def _guard_op(self, op: str) -> None:
        if op not in self.fs_cfg.allowed_fs_ops:
            raise FsConnectError(
                f"fs op {op!r} is not in allowed_fs_ops",
                code="FSCONNECT_OP_NOT_ALLOWED",
                details={"op": op, "allowed": list(self.fs_cfg.allowed_fs_ops)},
            )

    def _scan(self, text: str) -> list[str]:
        return [src for src, pat in self._patterns if pat.search(text)]

    def _audit(self, event: dict) -> None:
        audit_log(event, self.config_path)

    # --- operations -------------------------------------------------------

    def fs_list(self, target: str = "", *, root: str | None = None) -> dict:
        self._guard_op("fs_list")
        entries = self._roots.list_dir(target, root=root)
        self._audit({"event": "fsconnect_read", "op": "fs_list", "path": target or ".",
                     "count": len(entries)})
        return {"op": "fs_list", "path": target or ".", "count": len(entries), "entries": entries}

    def fs_stat(self, target: str, *, root: str | None = None) -> dict:
        self._guard_op("fs_stat")
        info = self._roots.stat(target, root=root)
        self._audit({"event": "fsconnect_read", "op": "fs_stat", "path": target})
        return {"op": "fs_stat", "path": target, **info}

    def fs_read(self, target: str, *, root: str | None = None) -> dict:
        self._guard_op("fs_read")
        data = self._roots.read_bytes(target, root=root, max_bytes=self.fs_cfg.max_file_bytes)
        is_binary = _looks_binary(data)
        flags: list[str] = []
        content: str | None = None
        encoding: str | None = None
        if is_binary:
            content = None
        else:
            encoding = "utf-8"
            content = data.decode("utf-8", errors="replace")
            if self.fs_cfg.scan_content:
                flags = self._scan(content)
        self._audit({
            "event": "fsconnect_read", "op": "fs_read", "path": target,
            "size": len(data), "is_binary": is_binary,
            "injection_flag_count": len(flags),
        })
        return {
            "op": "fs_read", "path": target, "size": len(data),
            "is_binary": is_binary, "encoding": encoding, "content": content,
            "injection_flags": flags, "injection_flag_count": len(flags),
        }

    def fs_grep(
        self, target: str, pattern: str, *, root: str | None = None, regex: bool = False
    ) -> dict:
        self._guard_op("fs_grep")
        if not pattern:
            raise FsConnectError("fs_grep requires a non-empty pattern", code="FSCONNECT_BAD_ARG")
        data = self._roots.read_bytes(target, root=root, max_bytes=self.fs_cfg.max_file_bytes)
        if _looks_binary(data):
            raise FsConnectError(
                "fs_grep target appears to be binary",
                code="FSCONNECT_BINARY", details={"path": target},
            )
        try:
            rx = re.compile(pattern if regex else re.escape(pattern), re.IGNORECASE)
        except re.error as exc:
            raise FsConnectError(
                f"invalid grep regex: {exc}", code="FSCONNECT_BAD_ARG", details={"pattern": pattern}
            ) from exc
        matches: list[dict] = []
        truncated = False
        for lineno, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
            if rx.search(line):
                if len(matches) >= _MAX_GREP_MATCHES:
                    truncated = True
                    break
                matches.append({"line": lineno, "text": line})
        self._audit({"event": "fsconnect_read", "op": "fs_grep", "path": target,
                     "match_count": len(matches)})
        return {"op": "fs_grep", "path": target, "pattern": pattern, "regex": regex,
                "match_count": len(matches), "truncated": truncated, "matches": matches}

    def fs_glob(
        self, target: str = "", pattern: str = "", *, root: str | None = None, recursive: bool = True
    ) -> dict:
        """Find entries under *target* whose path matches a glob *pattern*.

        Enumeration goes through the SAME pathsafe ``list_dir`` descent every read
        uses (per-component ``O_NOFOLLOW``) -- never Python's ``glob``, which would
        resolve paths outside the security core. The pattern is matched (via
        ``fnmatch``) against each entry's path RELATIVE to *target*; ``*`` spans
        ``/``, so ``*.md`` finds matches at any depth when ``recursive`` is true.
        Results are capped at ``_MAX_GLOB_MATCHES`` (``truncated`` flags the cap).
        """
        self._guard_op("fs_glob")
        if not pattern:
            raise FsConnectError("fs_glob requires a non-empty pattern", code="FSCONNECT_BAD_ARG")
        matches: list[dict] = []
        prefix = f"{target}/" if target else ""

        def _walk(rel: str) -> bool:
            """Enumerate *rel*; return False once the match cap is hit (stop)."""
            for entry in self._roots.list_dir(rel, root=root):
                name = entry["name"]
                child = f"{rel}/{name}" if rel else name
                relpath = child[len(prefix):] if prefix and child.startswith(prefix) else child
                if fnmatch.fnmatch(relpath, pattern):
                    if len(matches) >= _MAX_GLOB_MATCHES:
                        return False
                    matches.append({"path": child, "type": entry["type"],
                                    "size": entry.get("size", 0)})
                if recursive and entry["type"] == "dir" and not _walk(child):
                    return False
            return True

        truncated = not _walk(target)
        self._audit({"event": "fsconnect_read", "op": "fs_glob", "path": target or ".",
                     "match_count": len(matches)})
        return {"op": "fs_glob", "path": target or ".", "pattern": pattern,
                "recursive": recursive, "match_count": len(matches),
                "truncated": truncated, "matches": matches}


__all__ = ["FsClient", "build_injection_patterns"]
