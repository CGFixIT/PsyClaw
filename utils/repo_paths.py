"""Repo-relative path safety — shared by harness and ops_runner.

Why this module exists
----------------------
``agentic.deepagent_github.repo_workspace.canonical_repo_path`` is the jail's
source of truth for "will this path write/read under the clone root." Harness
and ``utils.ops_runner`` must not import ``agentic`` (I6 / isolation), but they
must reject the same escapes *before* a browser-staged path is forwarded as
``--read-file``.

This module is a stdlib-only mirror of that acceptance rule. A drift test in
``tests/test_harness_agent_routes.py`` asserts the two functions agree on a
matrix of safe and hostile inputs so a future jail change cannot leave the
control plane accepting what the clone will skip (or vice versa).
"""

from __future__ import annotations

from pathlib import PureWindowsPath


def canonical_repo_relative_path(target: str) -> str | None:
    """Return ``target``'s canonical repo-relative form, or ``None`` if unsafe.

    Same acceptance contract as ``agentic``'s ``canonical_repo_path``:

    * empty / non-str / NUL → reject
    * absolute (POSIX or Windows drive-qualified) → reject
    * leading ``-`` (flag injection into argv) → reject
    * ``..`` segment or ``:`` in a segment → reject
    * empty / ``.`` segments dropped; remaining parts joined with ``/``

    Returns ``None`` rather than a "cleaned" string for rejectable inputs so
    ``/etc/passwd`` never becomes ``etc/passwd``.
    """
    if not isinstance(target, str) or not target or "\x00" in target:
        return None
    normalized = target.replace("\\", "/")
    if normalized.startswith(("/", "-")) or PureWindowsPath(target).is_absolute():
        return None
    parts = tuple(part for part in normalized.split("/") if part not in {"", "."})
    if not parts or any(part == ".." or ":" in part for part in parts):
        return None
    return "/".join(parts)
