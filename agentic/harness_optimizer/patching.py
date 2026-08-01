"""Governed local persistence for accepted harness candidate artifacts.

This module deliberately persists a versioned proposal artifact, not a source
tree change, soul mutation, or GitHub write. Those surfaces keep their existing
human-operated governance paths.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from agentic.config import AgenticConfig
from agentic.harness_optimizer.core import CandidateDecision, Variant
from agentic.harness_optimizer.governance import inspect_candidate_text
from agentic.harness_optimizer.proposer import ProposerWorkspace
from utils.errors import AgenticError, AgenticWriteRefused
from utils.logger import audit_log

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# An apply completes in milliseconds, so a lock directory older than this is from
# a crashed run and is safe to reclaim. Mirrors agentic.registry's identical
# atomic-mkdir mutex, which exists for the same reason: apply_candidate_artifact's
# read-version-increment-write below is a plain read-modify-write with no
# cross-process exclusion otherwise, so two concurrent accept calls for the same
# variant_id could interleave and lose an update.
_LOCK_STALE_SEC = 60
_write_lock = threading.Lock()


def _claim_stale_lock(lock_dir: Path) -> bool:
    # Reclaim a stale lock without ever breaking mutual exclusion.
    #
    # rmdir()+mkdir() is NOT safe here: two processes that both observe the same
    # stale directory can both rmdir it and both mkdir it, because the second
    # rmdir removes the fresh lock the first one just created. Both then believe
    # they hold the lock, and apply_candidate_artifact's
    # read-version-increment-write interleaves -- precisely the lost update this
    # mutex exists to prevent.
    #
    # os.replace() of a directory onto a non-existent path is atomic, so at most
    # ONE racer moves the stale directory aside; every other racer's rename fails
    # with ENOENT and refuses. The winner then re-creates the lock, and even that
    # mkdir is allowed to lose to a process that slipped into the gap -- in which
    # case it refuses too. Worst case is a spurious refusal, never a double grant.
    # Kept byte-for-byte in step with agentic.registry._claim_stale_lock.
    aside = lock_dir.with_name(f"{lock_dir.name}.stale.{os.getpid()}.{time.time_ns()}")
    try:
        os.replace(lock_dir, aside)
    except OSError:
        # Another process moved it first, or it vanished. Do not claim.
        return False
    try:
        aside.rmdir()
    except OSError:
        # Leftover debris is harmless; never fail the reclaim over cleanup.
        pass
    try:
        lock_dir.mkdir(parents=True)
    except OSError:
        return False
    return True


def _acquire_artifact_lock(lock_dir: Path) -> None:
    """Acquire a cross-process write lock, or raise ``AgenticError``.

    ``Path.mkdir`` is atomic on every platform, so an atomically-created lock
    directory doubles as a zero-dependency, cross-platform mutex -- the same
    pattern ``agentic.registry._acquire_registry_lock`` uses. A lock left by a
    crashed run is reclaimed after ``_LOCK_STALE_SEC``.
    """
    try:
        lock_dir.mkdir(parents=True)
        return
    except FileExistsError:
        # Lock is already held; fall through to the stale-age check below.
        pass
    try:
        age = time.time() - lock_dir.stat().st_mtime
    except OSError:
        age = 0.0
    if age > _LOCK_STALE_SEC and _claim_stale_lock(lock_dir):
        return
    raise AgenticError(
        "another harness-optimizer accept is in progress for this candidate",
        details={"lock_dir": str(lock_dir)},
    )


def _release_artifact_lock(lock_dir: Path) -> None:
    """Release the cross-process write lock; tolerant if it is already gone."""
    try:
        lock_dir.rmdir()
    except OSError:
        # Best-effort: the lock dir may already be gone (or never created).
        pass


@dataclass(frozen=True)
class HarnessApplicationProposal:
    """An accepted candidate ready for an explicit human-persisted record."""

    variant_id: str
    changed_surfaces: tuple[str, ...]
    proposal_text: str
    proposal_sha256: str


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp_path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp_path, path)
    except OSError:
        # A failure between write_text() and os.replace() would otherwise leave
        # a stray .{name}.{pid}.tmp file with nothing to clean it up.
        temp_path.unlink(missing_ok=True)
        raise


def propose_candidate_application(
    decision: CandidateDecision,
    variant: Variant,
    workspace: ProposerWorkspace,
    *,
    config_path: str = "config.yaml",
    cfg: dict | None = None,
) -> HarnessApplicationProposal:
    """Create a no-write persisted-artifact proposal after all hard gates pass."""

    if not decision.accepted:
        raise AgenticWriteRefused("cannot propose application for a rejected candidate")
    if not _SLUG_RE.match(variant.variant_id):
        raise AgenticError("variant_id must be a safe artifact slug")
    proposal_text = workspace.proposal_path.read_text(encoding="utf-8")
    if not proposal_text.strip():
        raise AgenticError("candidate proposal.md must be non-empty")
    findings = inspect_candidate_text(proposal_text, cfg)
    if findings:
        raise AgenticWriteRefused("candidate proposal failed the injection gate")
    proposal = HarnessApplicationProposal(
        variant_id=variant.variant_id,
        changed_surfaces=variant.changed_surfaces,
        proposal_text=proposal_text,
        proposal_sha256=hashlib.sha256(proposal_text.encode("utf-8")).hexdigest(),
    )
    audit_log(
        {
            "event": "agentic_harness_apply_proposed",
            "variant_id": proposal.variant_id,
            "proposal_sha256": proposal.proposal_sha256,
            "changed_surfaces": list(proposal.changed_surfaces),
        },
        config_path=config_path,
        cfg=cfg,
    )
    return proposal


def apply_candidate_artifact(
    proposal: HarnessApplicationProposal,
    agentic_config: AgenticConfig,
    *,
    reason: str,
    confirm: bool,
    config_path: str = "config.yaml",
    cfg: dict | None = None,
) -> dict:
    """Atomically record a human-approved candidate; never apply it to source."""

    harness_cfg = agentic_config.harness_optimizer
    refusal: str | None = None
    expected_sha256 = hashlib.sha256(proposal.proposal_text.encode("utf-8")).hexdigest()
    if not _SLUG_RE.match(proposal.variant_id):
        refusal = "candidate artifact requires a safe variant_id"
    elif not proposal.proposal_text.strip():
        refusal = "candidate artifact requires a non-empty proposal"
    elif not hmac.compare_digest(proposal.proposal_sha256, expected_sha256):
        refusal = "candidate artifact proposal hash does not match its text"
    elif inspect_candidate_text(proposal.proposal_text, cfg):
        refusal = "candidate proposal failed the injection gate"
    elif not getattr(agentic_config, "enabled", False) or not harness_cfg.enabled:
        refusal = "agentic and harness optimizer must be enabled before apply"
    elif not (agentic_config.is_write_mode and agentic_config.writes_enabled):
        refusal = "agentic write mode and writes_enabled are required before apply"
    elif not harness_cfg.require_human_confirm_for_accept:
        refusal = "harness optimizer confirmation cannot be disabled for persistent apply"
    elif not isinstance(reason, str) or not reason.strip():
        refusal = "persistent apply requires a non-empty human reason"
    elif not confirm:
        refusal = "persistent apply requires explicit confirmation"
    if refusal:
        audit_log(
            {"event": "agentic_harness_apply_refused", "variant_id": proposal.variant_id, "reason": refusal},
            config_path=config_path,
            cfg=cfg,
        )
        raise AgenticWriteRefused(refusal)

    artifact_path = Path(harness_cfg.output_dir) / "accepted" / f"{proposal.variant_id}.json"
    lock_dir = artifact_path.with_suffix(artifact_path.suffix + ".lock.d")
    with _write_lock:  # serialize threads within this process
        _acquire_artifact_lock(lock_dir)  # serialize other processes too
        try:
            previous = {}
            if artifact_path.exists():
                try:
                    previous = json.loads(artifact_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise AgenticError(
                        "existing harness artifact is malformed", details={"path": str(artifact_path)}
                    ) from exc
            version = int(previous.get("version", 0)) + 1
            record = {
                "version": version,
                "variant_id": proposal.variant_id,
                "changed_surfaces": list(proposal.changed_surfaces),
                "proposal_sha256": proposal.proposal_sha256,
                "reason": reason,
                "proposal_text": proposal.proposal_text,
            }
            _atomic_json(artifact_path, record)
            memory_path = Path(harness_cfg.memory_dir) / f"{proposal.variant_id}-{proposal.proposal_sha256[:12]}.json"
            _atomic_json(memory_path, {key: value for key, value in record.items() if key != "proposal_text"})
        finally:
            _release_artifact_lock(lock_dir)
    audit_log(
        {
            "event": "agentic_harness_candidate_accepted",
            "variant_id": proposal.variant_id,
            "version": version,
            "proposal_sha256": proposal.proposal_sha256,
        },
        config_path=config_path,
        cfg=cfg,
    )
    audit_log(
        {"event": "agentic_harness_memory_recorded", "variant_id": proposal.variant_id, "version": version},
        config_path=config_path,
        cfg=cfg,
    )
    return {
        "status": "applied_artifact",
        "path": str(artifact_path),
        "version": version,
        "sha256": proposal.proposal_sha256,
    }
