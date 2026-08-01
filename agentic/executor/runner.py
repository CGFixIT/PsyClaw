"""Run a fixed set of verification commands over a worktree, sandboxed.

``run_verification(worktree, checks)`` runs each ``Check`` as an argv-list
subprocess -- ``cwd`` pinned to the worktree, environment scrubbed to a minimal
allowlist, a hard per-check wall-clock timeout, ``check=False`` (the exit code
is data, not an exception) -- mirroring the house pattern
``agentic/fsconnect/indexer.py::_run_reindex`` already established: ``[sys.executable,
"-m", <module>]`` + ``capture_output`` + explicit ``timeout`` + ``audit_log`` of the
exit code. This is that same shape, generalized to a list of checks instead of
one hardcoded reindex call, plus the environment scrub that call never needed
(it always ran against the repo's own config, not a model-authored diff).

**Not wired to anything in this change.** No CLI subcommand, no route, no call
from ``harness_optimizer``'s ``decide_candidate`` (which gains a new,
``False``-by-default keyword this module's report can eventually feed, but
nothing in this change passes it). There is no live caller yet: the planner
and git-write-in-a-jailed-clone phases that would produce something for this
to verify do not exist. Shipping the capability now, independently tested, and
deferring the wiring is the same call made for ``RepoWorkspaceTools`` (P5),
the injection scanner (P1), and the ``deepagent-plan`` CLI probe (P4) earlier
in this effort -- each landed before its consumer did.

**Containment is best-effort software, not a hard boundary -- say this plainly
to anyone who reads this module or the threat-model amendment it ships with.**
The environment scrub removes proxy variables and API keys from what the
child process inherits; it does not, and cannot, stop a sufficiently
determined test file from opening a raw socket, since sockets do not consult
``HTTPS_PROXY``. Real network isolation needs a Linux network namespace (or
equivalent), which needs elevated privileges this module does not have and
does not attempt to acquire. Whatever a docker-compose deployment's existing
container hardening provides (read-only rootfs, dropped capabilities,
non-root, resource ceilings -- see ``docker-compose.yml`` and
``docs/THREAT_MODEL.md`` section 3) applies on top of this, for free, when
deployed that way; nothing here assumes it.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - argv-list only, no shell, fixed interpreter + fixed argv
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from utils.logger import audit_log

# Same truncation discipline as agentic/gh_client.py's MAX_DIFF_CHARS: a
# runaway pytest failure dump must not blow up memory, the audit log, or a
# downstream consumer that renders this report.
MAX_OUTPUT_CHARS = 20_000

DEFAULT_CHECK_TIMEOUT_SEC = 120

# Minimal env allowlist: only what's needed to locate and run an interpreter
# and its tools. Deliberately an ALLOWLIST, not a denylist of "things to
# strip" -- a denylist misses whatever secret-shaped variable a future config
# change introduces; an allowlist is safe by construction against additions
# nobody thought to strip. Every one of these is copied ONLY if already
# present in the parent; none is invented.
_ALLOWED_ENV_VARS = ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONIOENCODING")


def _scrubbed_env() -> dict[str, str]:
    """Build the child environment: an allowlisted subset plus explicit network-hostility.

    NO_PROXY/no_proxy are set to "*" and HTTPS_PROXY/HTTP_PROXY/ALL_PROXY are
    simply never copied (the allowlist above doesn't include them) --
    together these make an HTTP-library-based request in the child fail
    closed rather than transiting whatever proxy this process itself uses.
    PIP_NO_INDEX stops an accidental `pip install` inside a check from
    reaching PyPI. None of this stops a raw socket connection; see the module
    docstring.
    """
    env = {name: os.environ[name] for name in _ALLOWED_ENV_VARS if name in os.environ}
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    env["PIP_NO_INDEX"] = "1"
    return env


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... [output truncated at {MAX_OUTPUT_CHARS} chars]"


def _stream_to_str(stream: str | bytes | None) -> str:
    """Normalize a captured stream that may be ``str``, ``bytes``, or ``None``.

    ``subprocess.run(..., text=True)`` populates ``TimeoutExpired.stdout``/
    ``.stderr`` differently by platform: CPython's non-Windows branch only
    calls ``process.wait()`` after ``kill()``, leaving whatever partial output
    ``_communicate`` already collected as raw ``bytes`` on the exception; the
    ``_mswindows`` branch instead calls ``process.communicate()`` AFTER kill,
    and in text mode that returns ``str``. Calling ``.decode()`` unconditionally
    (correct on POSIX) raised ``AttributeError: 'str' object has no attribute
    'decode'`` on Windows -- turning the exact case this module exists to
    contain (a hung check) into an uncaught crash instead of a timed-out
    ``CheckResult``, on the one platform ``harness/`` is a primary operator
    surface for.
    """
    if stream is None:
        return ""
    if isinstance(stream, str):
        return stream
    return stream.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class Check:
    """One verification command. ``argv`` is the command only -- no cwd/env/shell."""

    name: str
    argv: tuple[str, ...]
    timeout_sec: int = DEFAULT_CHECK_TIMEOUT_SEC

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Check.name must be non-empty")
        if not self.argv:
            raise ValueError(f"Check {self.name!r}: argv must be non-empty")


@dataclass(frozen=True)
class CheckResult:
    """The outcome of running one ``Check``."""

    name: str
    exit_code: int
    ok: bool
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


@dataclass(frozen=True)
class VerificationReport:
    """Aggregate outcome of a ``run_verification`` call."""

    ok: bool
    results: tuple[CheckResult, ...] = field(default_factory=tuple)

    def failed_names(self) -> tuple[str, ...]:
        return tuple(r.name for r in self.results if not r.ok)


def _run_one(check: Check, *, cwd: Path, env: dict[str, str]) -> CheckResult:
    try:
        completed = subprocess.run(  # noqa: S603 -- argv list, no shell, fixed interpreter
            list(check.argv),
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=check.timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # A hung check must not crash the whole verification run -- it counts
        # as a failed check, exactly like a non-zero exit does.
        return CheckResult(
            name=check.name,
            exit_code=-1,
            ok=False,
            stdout=_truncate(_stream_to_str(exc.stdout)),
            stderr=f"timed out after {check.timeout_sec}s",
            timed_out=True,
        )
    except OSError as exc:
        # A check whose argv[0] does not resolve on PATH (FileNotFoundError)
        # or is not executable (PermissionError) must not crash the run either
        # -- this module's own docstring promises "the exit code is data, not
        # an exception," and that promise did not hold for this class of
        # failure: it escaped run_verification, run_real_repo_loop, and
        # cmd_real_repo_run's AgenticError-only handler as a bare traceback,
        # leaving no run record and a leaked clone. A caller-authored checks
        # file naming a typo'd or missing tool is the realistic trigger, not
        # anything model-controlled -- checks are operator-supplied by design.
        return CheckResult(
            name=check.name,
            exit_code=-2,
            ok=False,
            stderr=f"could not execute {check.argv[0]!r}: {exc}",
        )
    return CheckResult(
        name=check.name,
        exit_code=completed.returncode,
        ok=completed.returncode == 0,
        stdout=_truncate(completed.stdout or ""),
        stderr=_truncate(completed.stderr or ""),
    )


def run_verification(
    worktree: Path,
    checks: Sequence[Check],
    *,
    config_path: str = "config.yaml",
    cfg: dict | None = None,
) -> VerificationReport:
    """Run every check against ``worktree``, sandboxed, and return the aggregate report.

    Runs checks sequentially (not in parallel) so each gets the audit log's
    full attention and so a hung check's timeout is attributable to exactly
    one process, not a pool. ``worktree`` must already exist; this function
    does not clone, jail, or create anything -- pair it with
    ``agentic.deepagent_github.repo_workspace`` or a future git-write surface
    for the directory itself.
    """
    env = _scrubbed_env()
    results = []
    for check in checks:
        result = _run_one(check, cwd=worktree, env=env)
        audit_log(
            {
                "event": "agentic_executor_check_result",
                "check": result.name,
                "exit_code": result.exit_code,
                "ok": result.ok,
                "timed_out": result.timed_out,
            },
            config_path=config_path,
            cfg=cfg,
        )
        results.append(result)
    return VerificationReport(ok=all(r.ok for r in results), results=tuple(results))


def default_checks(repo_root: Path | None = None) -> tuple[Check, ...]:
    """CyClaw's own three checks: pytest, ruff, and the invariant guard.

    These are CyClaw-specific defaults, not something ``run_verification``
    itself assumes -- the shipped ``agentic.repo`` default IS
    ``"CGFixIT/CyClaw"`` (config.yaml), so the harness's own first target is
    its own repository, and these are exactly the three commands
    ``CLAUDE.md``'s own quality bar names. ``repo_root`` locates
    ``invariant-guard``'s script inside the worktree being verified (it must
    be the SAME worktree ``run_verification`` runs against, not this
    process's own checkout, or the guard checks the wrong tree); defaults to
    this repo's own root for convenience when verifying CyClaw against
    itself.
    """
    root = repo_root or Path(__file__).resolve().parent.parent.parent
    return (
        Check("pytest", (sys.executable, "-m", "pytest", "-q", "--tb=short")),
        Check("ruff", (sys.executable, "-m", "ruff", "check", "--select", "E,F,I,B,C4,UP,S", ".")),
        Check(
            "invariant_guard",
            (sys.executable, str(root / ".claude" / "skills" / "invariant-guard" / "check_invariants.py")),
        ),
    )


__all__ = [
    "DEFAULT_CHECK_TIMEOUT_SEC",
    "MAX_OUTPUT_CHARS",
    "Check",
    "CheckResult",
    "VerificationReport",
    "default_checks",
    "run_verification",
]
