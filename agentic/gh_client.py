"""Read-only GitHub CLI (`gh`) subprocess wrapper for the CyClaw agentic layer.

Responsibilities:
  - Locate and version-check the ``gh`` binary (configurable floor).
  - Build argv for READ-ONLY operations only (pr/issue/repo view+list, pr diff).
  - Run ``gh`` with captured output and parse JSON where applicable.
  - Emit an audit event via ``utils.logger.audit_log`` for every call.

Hard guarantees (mirrors sync/runner.py):
  - argv is ALWAYS a list; ``gh`` is ALWAYS resolved to an absolute path via
    ``shutil.which`` -- never ``shell=True``, never a string command.
  - Only an allow-listed set of read-only subcommands can be built. There is no
    code path here that mutates GitHub state -- writes live (disabled/stubbed) in
    agentic/writer.py and are never reachable from this module.
  - No token is ever placed in argv. ``gh`` resolves its own credential from its
    keyring / GH_TOKEN; CyClaw neither reads nor forwards it.

This module does NOT import anything from gate.py, graph.py, or the FastAPI / MCP
layer. It runs strictly out-of-band.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # noqa: S404 -- argv-list gh invocation only; never shell=True
import time
from functools import lru_cache

from utils.errors import AgenticError, GhNotInstalledError, GhVersionError
from utils.logger import audit_log

# ---------------------------------------------------------------------------
# Version handling
# ---------------------------------------------------------------------------

DEFAULT_MIN_GH = (2, 40, 0)

# `gh version 2.55.0 (2024-08-21)` -> capture the X.Y.Z triple.
_GH_VERSION_RE = re.compile(r"gh version\s+(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)

# A repo slug is ``owner/name``; each segment must START with an alphanumeric.
# Anchoring the first character (rather than the looser ``[A-Za-z0-9_.-]+``)
# rejects a leading-dash slug like ``-X/y``: ``repo`` flows POSITIONALLY into
# ``gh repo view <repo>`` (and as the value of ``--repo``), so a value gh reads as
# a flag is argument injection (argv is a list, so not shell injection).
# ``agentic.config`` already validates this at config-load, but ``build_read_argv``
# is a public boundary -- reused by ``run_read`` and callable with an arbitrary
# ``repo`` -- so it re-validates here. This is the same belt-and-suspenders the
# sync layer applies to ``remote_name`` / ``remote_path`` (sync/config.py).
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")


@lru_cache(maxsize=16)
def check_gh_version(
    gh_bin: str = "gh",
    min_version: tuple[int, int, int] = DEFAULT_MIN_GH,
    retries: int = 1,
) -> tuple[int, int, int]:
    """Confirm ``gh`` is installed and at/above ``min_version``.

    Returns the parsed ``(major, minor, patch)`` tuple. Raises
    ``GhNotInstalledError`` if the binary is not on PATH, or ``GhVersionError``
    if the version is too old or unparseable.

    ``retries`` adds up to N extra attempts with exponential backoff on
    ``TimeoutExpired`` (the only transient failure possible for ``gh version``).
    The default of 1 retry tolerates a single slow startup without silently
    failing the agentic layer at import time on a loaded CI runner.

    Cache lifetime: the ``lru_cache`` result lives for the whole process — a
    ``gh`` upgrade/downgrade mid-process is not re-detected. Accepted trade-off:
    the agentic CLI is a short-lived out-of-band process (python -m agentic.cli),
    so the window is one command, and the version floor is a CVE guard where a
    stale *pass* only occurs if gh was downgraded mid-command. Call
    ``check_gh_version.cache_clear()`` if a long-lived host ever embeds this.
    """
    binary = shutil.which(gh_bin)
    if binary is None:
        raise GhNotInstalledError(
            "GitHub CLI (gh) not found on PATH",
            details={
                "looked_for": gh_bin,
                "install_hint_linux": "see https://github.com/cli/cli#installation",
                "install_hint_windows": "winget install GitHub.cli",
            },
        )

    attempts = max(1, retries + 1)
    last_timeout_exc: subprocess.TimeoutExpired | None = None
    result = None
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(  # noqa: S603 -- argv list, absolute binary, no shell
                [binary, "version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            last_timeout_exc = None
            break
        except subprocess.TimeoutExpired as exc:
            last_timeout_exc = exc
            if attempt < attempts:
                time.sleep(2.0 ** (attempt - 1))
        except OSError as exc:
            raise GhNotInstalledError(
                f"Could not execute GitHub CLI (gh): {exc}",
                details={"binary": binary},
            ) from exc

    if last_timeout_exc is not None:
        raise GhVersionError(
            f"gh version check timed out after {attempts} attempt(s): {last_timeout_exc}",
            details={"binary": binary, "attempts": attempts},
        ) from last_timeout_exc

    output = (result.stdout or "") + (result.stderr or "")  # type: ignore[union-attr]
    match = _GH_VERSION_RE.search(output)
    if not match:
        raise GhVersionError(
            "Could not parse gh version output",
            details={"binary": binary, "output": output[:500]},
        )

    found = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if found < min_version:
        raise GhVersionError(
            f"gh {found[0]}.{found[1]}.{found[2]} is too old; need >= "
            f"{min_version[0]}.{min_version[1]}.{min_version[2]}",
            details={
                "found": ".".join(map(str, found)),
                "required": ">=" + ".".join(map(str, min_version)),
                "binary": binary,
            },
        )
    return found


# ---------------------------------------------------------------------------
# Read-only operation catalog
# ---------------------------------------------------------------------------

# Default --json field sets per resource. Every name is a real `gh ... --json`
# field (gh validates them against its schema and errors on an unknown one). The
# view sets are richer than the list sets on purpose: a single-item view can
# afford labels/timestamps/diffstat, while a list stays lean to keep payloads small.
_PR_FIELDS = (
    "number,title,state,author,headRefName,baseRefName,isDraft,url,body,"
    "labels,assignees,createdAt,updatedAt,mergeable,reviewDecision,"
    "additions,deletions,changedFiles"
)
_PR_LIST_FIELDS = "number,title,state,author,isDraft,url,labels,createdAt,updatedAt"
_ISSUE_FIELDS = (
    "number,title,state,author,labels,url,body,assignees,milestone,"
    "createdAt,updatedAt,comments"
)
_ISSUE_LIST_FIELDS = "number,title,state,author,url,labels,createdAt,updatedAt"
_REPO_FIELDS = (
    "name,owner,description,defaultBranchRef,isPrivate,url,"
    "isArchived,pushedAt,primaryLanguage,repositoryTopics,stargazerCount,licenseInfo"
)

# Every supported op maps to a builder. There is intentionally NO entry that
# mutates state -- this dict IS the read-only allow-list.
_READ_OPS = frozenset(
    {"pr_view", "pr_list", "pr_diff", "issue_view", "issue_list", "repo_view"}
)

# Hard ceilings on caller-tunable sizes. ``limit`` flows verbatim into
# ``gh ... --limit`` argv, and a ``pr diff`` stdout flows whole into consumers
# (CLI output, downstream harness context) -- cap both so a five-digit limit or
# a multi-MB diff cannot blow up argv or downstream memory.
MAX_LIST_LIMIT = 1000
MAX_DIFF_CHARS = 200_000


# A non-zero `gh` exit whose stderr matches this is a TRANSIENT network/server
# condition worth retrying. Deterministic failures (404 not found, bad flag, auth)
# do NOT match, so they still fail fast -- no point re-running them.
_TRANSIENT_GH_RE = re.compile(
    r"(?:timeout|timed out|temporary failure|connection (?:reset|refused|timed out)|"
    # "could not resolve HOST" is DNS (transient). NOT gh's 404 "Could not resolve
    # to a PullRequest", which must fail fast -- so the 'host' anchor is required.
    r"could not resolve host|network is unreachable|i/o timeout|\bEOF\b|"
    r"HTTP 5\d\d|HTTP 429|rate limit|server error|service unavailable|bad gateway)",
    re.IGNORECASE,
)


def _is_transient_gh_error(stderr: str) -> bool:
    """True if a non-zero gh exit looks like a transient network/server failure."""
    return bool(_TRANSIENT_GH_RE.search(stderr or ""))


def build_read_argv(
    op: str,
    repo: str,
    *,
    number: int | None = None,
    limit: int = 30,
    gh_bin: str = "gh",
) -> list[str]:
    """Build the argv list for a read-only ``gh`` operation.

    ``op`` must be one of the read-only ops in ``_READ_OPS``; anything else
    raises ``AgenticError`` (no write op can ever be built here). ``repo`` is the
    validated ``owner/name`` slug. ``number`` is required for the *_view / pr_diff
    ops. The returned list always starts with ``gh_bin`` and uses only literal
    flags plus validated inputs -- no shell, no interpolation into a string.
    """
    if op not in _READ_OPS:
        raise AgenticError(
            f"Unknown or non-read-only gh op: {op!r}",
            details={"op": op, "allowed": sorted(_READ_OPS)},
        )

    # Re-validate the repo slug at this argv boundary (defense in depth). A value
    # like "-X/y" would otherwise reach gh as a positional/`--repo` argument and be
    # parsed as a flag -- argument injection. See the _REPO_RE rationale above.
    if not _REPO_RE.match(repo):
        raise AgenticError(
            f"invalid repo slug (must be 'owner/name', alphanumeric-leading): {repo!r}",
            details={"repo": repo},
        )

    if op in ("pr_view", "pr_diff", "issue_view") and number is None:
        raise AgenticError(
            f"op {op!r} requires a 'number'",
            details={"op": op},
        )

    try:
        num = str(int(number)) if number is not None else None
    except (TypeError, ValueError) as exc:
        raise AgenticError(
            f"'number' must be an integer, got {type(number).__name__}: {number!r}",
            details={"op": op},
        ) from exc

    try:
        safe_limit = str(min(int(limit), MAX_LIST_LIMIT))
    except (TypeError, ValueError) as exc:
        raise AgenticError(
            f"'limit' must be an integer, got {type(limit).__name__}: {limit!r}",
            details={"op": op},
        ) from exc

    if op == "pr_view":
        return [gh_bin, "pr", "view", num, "--repo", repo, "--json", _PR_FIELDS]
    if op == "pr_diff":
        return [gh_bin, "pr", "diff", num, "--repo", repo]
    if op == "pr_list":
        return [gh_bin, "pr", "list", "--repo", repo, "--json", _PR_LIST_FIELDS,
                "--limit", safe_limit]
    if op == "issue_view":
        return [gh_bin, "issue", "view", num, "--repo", repo, "--json", _ISSUE_FIELDS]
    if op == "issue_list":
        return [gh_bin, "issue", "list", "--repo", repo, "--json", _ISSUE_LIST_FIELDS,
                "--limit", safe_limit]
    # op == "repo_view"
    return [gh_bin, "repo", "view", repo, "--json", _REPO_FIELDS]


def run_read(
    op: str,
    repo: str,
    *,
    number: int | None = None,
    limit: int = 30,
    gh_bin: str = "gh",
    min_version: tuple[int, int, int] = DEFAULT_MIN_GH,
    timeout: int = 30,
    retries: int = 0,
    retry_backoff_sec: float = 2.0,
) -> dict:
    """Run a read-only ``gh`` op and return a structured result dict.

    Verifies the gh version, resolves the binary to an absolute path, runs it
    (argv list, no shell), and audits the call. ``pr_diff`` returns text under
    ``{"diff": ...}``, capped at ``MAX_DIFF_CHARS`` with a truncation marker;
    the JSON ops return ``{"data": <parsed>}``. Raises ``AgenticError`` on
    non-zero exit. Never raises with secret-bearing details.

    ``retries`` adds up to N extra attempts with exponential backoff, but ONLY on
    a transient failure: a timeout, or a non-zero exit whose stderr matches a
    network/server pattern (see ``_is_transient_gh_error``). Deterministic
    failures (404, bad flag, auth) are never retried -- they fail fast.
    ``retries=0`` (the default) is exactly the historical single-shot behaviour.
    """
    found = check_gh_version(gh_bin, min_version)
    binary = shutil.which(gh_bin)
    if binary is None:  # pragma: no cover -- check_gh_version already guards this
        raise GhNotInstalledError("gh disappeared after version check", details={"op": op})

    argv = build_read_argv(op, repo, number=number, limit=limit, gh_bin=binary)

    attempts = max(1, retries + 1)
    completed = None
    for attempt in range(1, attempts + 1):
        try:
            completed = subprocess.run(  # noqa: S603 -- argv list, absolute binary, no shell
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            audit_log({"event": "agentic_read_timeout", "op": op, "repo": repo, "attempt": attempt})
            if attempt < attempts:
                time.sleep(retry_backoff_sec * (2 ** (attempt - 1)))
                continue
            raise AgenticError(
                f"gh {op} timed out after {timeout}s",
                details={"op": op, "repo": repo},
            ) from exc

        # Retry a transient non-zero exit while attempts remain; break otherwise.
        if (
            completed.returncode != 0
            and attempt < attempts
            and _is_transient_gh_error(completed.stderr or "")
        ):
            audit_log({
                "event": "agentic_read_retry",
                "op": op,
                "repo": repo,
                "attempt": attempt,
                "exit_code": completed.returncode,
            })
            time.sleep(retry_backoff_sec * (2 ** (attempt - 1)))
            continue
        break

    if completed is None:  # pragma: no cover -- attempts >= 1 guarantees one run
        raise AgenticError(f"gh {op} never executed", details={"op": op, "repo": repo})

    audit_log({
        "event": "agentic_read",
        "op": op,
        "repo": repo,
        "number": number,
        "gh_version": ".".join(map(str, found)),
        "exit_code": completed.returncode,
    })

    if completed.returncode != 0:
        # stderr can echo back the (non-secret) request; truncate defensively.
        raise AgenticError(
            f"gh {op} failed with exit code {completed.returncode}",
            details={"op": op, "repo": repo, "stderr": (completed.stderr or "")[:500]},
        )

    if op == "pr_diff":
        diff = completed.stdout or ""
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + f"\n... [diff truncated at {MAX_DIFF_CHARS} chars]"
        return {"op": op, "repo": repo, "diff": diff}

    try:
        data = json.loads(completed.stdout or "null")
    except json.JSONDecodeError as exc:
        raise AgenticError(
            f"gh {op} returned non-JSON output",
            details={"op": op, "repo": repo, "output": (completed.stdout or "")[:500]},
        ) from exc
    return {"op": op, "repo": repo, "data": data}
