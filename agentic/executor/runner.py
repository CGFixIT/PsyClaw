"""Run a fixed set of verification commands over a worktree, sandboxed.

``run_verification(worktree, checks)`` runs each ``Check`` through a
:class:`~agentic.executor.hard_sandbox.HardSandbox` backend -- ``cwd`` pinned
to the worktree, environment scrubbed to a minimal allowlist, a hard
per-check wall-clock timeout, ``check=False`` (the exit code is data, not an
exception). Production selects :func:`production_sandbox` (Windows Job
Object). Missing backends raise ``HardSandboxUnavailable``; there is no
silent fallback to unconstrained ``subprocess.run``.

**Live caller:** ``agentic/real_repo_loop.py`` calls :func:`run_verification`
as the verify step of its plan/patch/verify loop.

**Containment (issue #1134 Phase 4, this slice):** Windows Job Object with
``KILL_ON_JOB_CLOSE`` plus an active-process cap. That is a process-tree
kill boundary, not a network namespace -- ordinary sockets still work.
Linux/Darwin fail closed until a later Seatbelt/netns PR. Env scrub still
drops proxy variables and API keys. Child ``HOME`` / ``USERPROFILE`` is a
disposable directory, not the operator's.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agentic.executor.hard_sandbox import (
    MAX_OUTPUT_CHARS,
    HardSandbox,
    HardSandboxUnavailable,
    production_sandbox,
)
from utils.logger import audit_log
from utils.numbat_emitter import emit_numbat_command, redact_argv_for_numbat
from utils.telemetry_kill import build_telemetry_safe_env

DEFAULT_CHECK_TIMEOUT_SEC = 120

# Minimal env allowlist: only what's needed to locate and run an interpreter
# and its tools. Deliberately an ALLOWLIST, not a denylist of "things to
# strip". HOME / USERPROFILE are NOT copied -- run_verification assigns a
# disposable directory so the child cannot read the operator's git creds.
_ALLOWED_ENV_VARS = ("PATH", "LANG", "LC_ALL", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONIOENCODING")


def _scrubbed_env() -> dict[str, str]:
    """Build the child environment: allowlisted subset + canonical telemetry
    overlay + explicit proxy hostility.

    The overlay (``build_telemetry_safe_env``) matters precisely BECAUSE this
    is a minimal allowlist: the child inherits nothing, so without it a check
    like ``pytest`` over a CyClaw worktree would import chromadb/langgraph/
    huggingface_hub with every telemetry default back in play. The overlay
    also carries the ancillary update-check opt-outs (including
    PIP_DISABLE_PIP_VERSION_CHECK) and removes the scrubbed credential/config
    names -- which the allowlist already excludes, so secrets stay out either
    way.

    NO_PROXY/no_proxy are set to "*" and HTTPS_PROXY/HTTP_PROXY/ALL_PROXY are
    simply never copied (the allowlist above doesn't include them). Stated
    precisely: NO_PROXY="*" means "bypass any proxy for every host" -- it
    directs an HTTP library to connect DIRECTLY, and blocks nothing by
    itself. Its value here is only to stop a check's traffic silently
    transiting whatever proxy this parent process uses; it is a best-effort
    software control, not a network namespace or firewall, and it does not
    stop a direct TCP/UDP socket, which never consults HTTPS_PROXY (see
    docs/THREAT_MODEL.md section 4 and the module docstring).
    PIP_NO_INDEX stops an accidental `pip install` inside a check from
    reaching PyPI.
    """
    env = {name: os.environ[name] for name in _ALLOWED_ENV_VARS if name in os.environ}
    env = build_telemetry_safe_env(env)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    env["PIP_NO_INDEX"] = "1"
    return env


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


def run_verification(
    worktree: Path,
    checks: Sequence[Check],
    *,
    config_path: str = "config.yaml",
    cfg: dict | None = None,
    sandbox: HardSandbox | None = None,
) -> VerificationReport:
    """Run every check against ``worktree`` inside a hard sandbox.

    Runs checks sequentially so a hung check's timeout is attributable to
    exactly one process. ``worktree`` must already exist. ``sandbox`` is for
    tests; production callers omit it and get :func:`production_sandbox`.
    An empty check list is vacuously ok and does not require a backend.
    """
    backend = sandbox
    if checks and backend is None:
        backend = production_sandbox()
    env = _scrubbed_env()
    results = []
    with tempfile.TemporaryDirectory(prefix="cyclaw-exec-home-") as home:
        env["HOME"] = home
        env["USERPROFILE"] = home
        for check in checks:
            if backend is None:
                raise HardSandboxUnavailable("nonempty checks require a sandbox backend")
            outcome = backend.run(
                check.argv, cwd=worktree, env=env, timeout_sec=check.timeout_sec,
            )
            result = CheckResult(
                name=check.name,
                exit_code=outcome.exit_code,
                ok=outcome.exit_code == 0 and not outcome.timed_out,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                timed_out=outcome.timed_out,
            )
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
            emit_numbat_command(
                redact_argv_for_numbat(list(check.argv)),
                exit_code=result.exit_code,
                tool_name="executor",
                actor="system",
                tags=["executor", result.name],
                artifact_type="executor",
                config_path=config_path,
                cfg=cfg,
            )
            results.append(result)
    return VerificationReport(ok=all(r.ok for r in results), results=tuple(results))


def default_checks(repo_root: Path | None = None) -> tuple[Check, ...]:
    """CyClaw's own three checks: pytest, ruff, and the invariant guard.

    These are CyClaw-specific defaults, not something ``run_verification``
    itself assumes -- the shipped ``agentic.repo`` default IS
    ``"cgfixit/CyClaw"`` (config.yaml), so the harness's own first target is
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
