"""Governed skills registry for the agentic layer.

A JSON-backed catalog of "skills" (name + description + body) that reuses the
SAME governance pattern as soul evolution (utils/personality.py):

  - ``propose_skill`` NEVER writes. It returns a diff plus *advisory* injection
    flags so a human can review the change.
  - ``apply_skill`` ENFORCES the injection gate at the write boundary, requires a
    non-empty human ``reason``, writes atomically (tmp + os.replace), and records
    a sha256-versioned history entry.

The injection scanner is the SAME union the soul scanner uses -- the curated
``policy.prompt_filter.banned_patterns`` from config.yaml unioned with the OWASP
baseline -- so a skill body can never smuggle in instructions that the query path
would reject. This is the soul-governance invariant applied to a new surface.

Never imported by gate.py / graph.py / mcp_hybrid_server.py.

Wired with governance_score in feature/CyClaw-Agent for agentic visibility.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from agentic.config import AgenticConfig
from guardrails.rails import build_injection_pattern_sources, compile_injection_patterns
from utils.errors import PromptInjectionError, SkillRegistryError
from utils.logger import audit_log

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


# An apply completes in milliseconds, so a lock directory older than this is from
# a crashed run and is safe to reclaim. Mirrors sync/runner's atomic-mkdir lock.
_LOCK_STALE_SEC = 60


def _acquire_registry_lock(lock_dir: Path) -> None:
    """Acquire a cross-process write lock, or raise ``SkillRegistryError``.

    The in-process ``threading.Lock`` only serializes threads inside ONE process;
    two separate ``agentic.cli apply-skill`` processes each hold their own and
    would not exclude each other. ``Path.mkdir`` is atomic on every platform, so
    an atomically-created lock directory doubles as a zero-dependency,
    cross-platform mutex with no fcntl/msvcrt branching. A lock left by a crashed
    run is reclaimed after ``_LOCK_STALE_SEC``.
    """
    try:
        lock_dir.mkdir()
        return
    except FileExistsError:
        # Lock is already held; fall through to the stale-age check below.
        pass
    try:
        age = time.time() - lock_dir.stat().st_mtime
    except OSError:
        age = 0.0
    if age > _LOCK_STALE_SEC:
        try:
            lock_dir.rmdir()
            lock_dir.mkdir()
            return
        except OSError:
            # Another process won the reclaim race; fall through and refuse below.
            pass
    raise SkillRegistryError(
        "another skills-registry apply is in progress",
        details={
            "lock_dir": str(lock_dir),
            "hint": "Retry shortly, or remove the lock dir if it is stale.",
        },
    )


def _release_registry_lock(lock_dir: Path) -> None:
    """Release the cross-process write lock; tolerant if it is already gone."""
    try:
        lock_dir.rmdir()
    except OSError:
        # Best-effort: the lock dir may already be gone (or never created).
        pass


class SkillRegistry:
    """File-as-truth skills catalog with propose/apply governance.

    governance_score(name) added in feature/CyClaw-Agent to give the agentic
    layer a 0-100 signal of how well-governed a skill is (low injection flags,
    good structure, etc.). Used by verification-specialist and registry tools.
    """

    def __init__(self, cfg: dict, agentic_cfg: AgenticConfig | None = None):
        self.cfg = cfg
        ac = agentic_cfg or AgenticConfig()
        self.agentic_cfg = ac
        self.registry_path = Path(ac.registry_path)
        self._lock = threading.Lock()
        self._injection_patterns = self._build_injection_patterns()
        self._data = self._load()

    # --- persistence ------------------------------------------------------

    def _empty(self) -> dict:
        return {"version": 0, "updated": None, "skills": {}, "history": []}

    def _load(self) -> dict:
        if not self.registry_path.exists():
            return self._empty()
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillRegistryError(
                f"Could not read skills registry: {exc}",
                details={"path": str(self.registry_path)},
            ) from exc
        if not isinstance(data, dict) or "skills" not in data:
            raise SkillRegistryError(
                "Skills registry is malformed (missing 'skills')",
                details={"path": str(self.registry_path)},
            )
        return data

    def _atomic_write(self, data: dict) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.registry_path.with_suffix(self.registry_path.suffix + ".tmp")
        try:
            tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp_path, self.registry_path)
        except (OSError, ValueError) as exc:
            tmp_path.unlink(missing_ok=True)
            raise SkillRegistryError(
                f"Failed to write skills registry: {exc}",
                details={"path": str(self.registry_path)},
            ) from exc

    # --- scanning (mirrors PersonalityManager) ----------------------------

    def _build_injection_patterns(self) -> list[tuple]:
        # Pattern construction is shared with agentic.fsconnect.client and
        # agentic.harness_optimizer.governance via guardrails.rails so the three
        # scanners cannot drift. The ENFORCEMENT below (drop logging, audit, and
        # the fail-closed refusal) stays here: guardrails owns how the set is
        # built, this registry owns what an empty or shrunken set means.
        sources = build_injection_pattern_sources(self.cfg)
        compiled: list[tuple] = list(compile_injection_patterns(tuple(sources)))
        # Surface uncompilable patterns. _compile_injection_patterns silently drops
        # an invalid regex (re.error), which quietly SHRINKS the enforced injection
        # gate: a malformed banned_patterns entry in config would weaken
        # skill-poisoning defense with no signal at all. Log + audit the dropped
        # patterns so an operator can fix the typo. (The gate still operates on
        # whatever DID compile; the total-failure case is fail-closed just below.)
        compiled_srcs = {src for src, _pat in compiled}
        dropped = [s for s in sources if s not in compiled_srcs]
        if dropped:
            logger.warning(
                "skills-registry injection scanner dropped %d uncompilable pattern(s); "
                "the enforced gate is smaller than configured. First few: %r",
                len(dropped), dropped[:3],
            )
            audit_log({
                "event": "agentic_skill_pattern_compile_failed",
                "dropped_count": len(dropped),
                "patterns": dropped[:10],
            }, cfg=self.cfg)
        # Fail closed. Unlike the soul scanner (advisory at propose time), this
        # scanner is ENFORCED at apply_skill: an empty pattern set would make
        # _scan_injection a silent no-op, so every skill would pass the injection
        # gate — reopening the skill-poisoning vector the registry exists to
        # close, with no test to catch the regression. If the OWASP baseline is
        # ever emptied/refactored away or every pattern fails to compile, refuse
        # to construct the registry rather than operate with a defeated gate.
        if not compiled:
            raise SkillRegistryError(
                "injection pattern set is empty; refusing to operate with a "
                "defeated skill-injection gate (fail-closed)",
                # Report what the SHARED builder actually saw rather than a
                # separately-imported baseline length: the two could disagree
                # (different module bindings of the same constant), and the
                # source count is the useful diagnostic anyway -- it says
                # whether the union came back empty or everything failed to
                # compile.
                details={"pattern_source_count": len(sources)},
            )
        return compiled

    def _scan_injection(self, text: str) -> list[str]:
        return [src for src, pat in self._injection_patterns if pat.search(text)]

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _canonical(spec: dict) -> str:
        """Stable string for hashing/scanning a skill spec."""
        return f"{spec.get('name', '')}\n{spec.get('description', '')}\n{spec.get('body', '')}"

    @staticmethod
    def _validate_spec(spec: dict) -> None:
        for key in ("name", "description", "body"):
            if not isinstance(spec.get(key), str) or not spec.get(key, "").strip():
                raise SkillRegistryError(
                    f"skill spec field {key!r} must be a non-empty string",
                    details={"field": key},
                )
        # Name must START with an alphanumeric. The previous ^[A-Za-z0-9_.-]+$
        # allowed a leading '-' or '.', so a name like "-foo" or "..evil" passed:
        # the dash form is an argument-injection shape when the name is later
        # composed into a subprocess argv (utils/ops_runner.py), and a leading-dot
        # form is a path-traversal shape if the name is ever used as a file key.
        # Anchoring the first character to [A-Za-z0-9] closes both without
        # restricting the rest of the slug.
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", spec["name"]):
            raise SkillRegistryError(
                f"skill name must match ^[A-Za-z0-9][A-Za-z0-9_.-]*$ "
                f"(must start with a letter or digit), got {spec['name']!r}",
                details={"name": spec["name"]},
            )

    # --- read -------------------------------------------------------------

    def list_skills(self) -> list[str]:
        return sorted(self._data.get("skills", {}).keys())

    def get_skill(self, name: str) -> dict | None:
        return self._data.get("skills", {}).get(name)

    def version(self) -> int:
        return int(self._data.get("version", 0))

    # NEW: governance_score for agentic visibility and verification-specialist
    def governance_score(self, name: str) -> int:
        """Return 0-100 governance score for a *registered* skill.

        Higher = better governed (low injection risk, good structure).
        Used by agentic tools and verification-specialist skill.
        """
        skill = self.get_skill(name)
        if not skill:
            return 0
        return self._score_spec(skill)

    def _score_spec(self, spec: dict) -> int:
        """Score an arbitrary skill spec (stored OR proposed) on the 0-100 scale.

        Factored out of :meth:`governance_score` so :meth:`propose_skill` can
        score the *proposed* body a human is about to apply, rather than the
        version already on disk — which is 0 for a brand-new skill and the
        stale body for an update.
        """
        canonical = self._canonical(spec)
        flags = self._scan_injection(canonical)
        # Heavy penalty for injection patterns (core invariant)
        penalty = min(len(flags) * 25, 80)
        score = 100 - penalty
        # Any flagged skill is refused by the apply gate (safe_to_apply is
        # False). Cap visibly low so the governance_score preview cannot mislead
        # an operator into thinking a refused skill is near-passing (e.g. 1 flag
        # -> 75 would look like a near-miss, but the apply gate will hard-reject
        # it regardless of score). Structure bonuses are never awarded for flagged
        # specs — there is nothing to bonus a skill that cannot be applied.
        if flags:
            return max(0, min(20, int(score)))
        if not flags:
            # Bonus for decent description (helps human review)
            if spec.get("description") and len(spec.get("description", "")) > 30:
                score += 8
            # Bonus for non-trivial body
            if spec.get("body") and len(spec.get("body", "")) > 100:
                score += 5
        return max(0, min(100, int(score)))

    # --- propose / apply (mirrors personality) ----------------------------

    def propose_skill(self, spec: dict, reason: str) -> dict:
        """Preview a skill add/update. NEVER writes; flags are advisory only.

        Enforcement lives at the write boundary in :meth:`apply_skill`.
        """
        self._validate_spec(spec)
        canonical = self._canonical(spec)
        flags = self._scan_injection(canonical)
        existing = self.get_skill(spec["name"]) or {}
        diff = list(difflib.unified_diff(
            (existing.get("body", "") or "").splitlines(keepends=True),
            spec["body"].splitlines(keepends=True),
            fromfile=f"{spec['name']} (current)",
            tofile=f"{spec['name']} (proposed)",
        ))
        return {
            "status": "proposed",
            "name": spec["name"],
            "diff": "".join(diff),
            "injection_flags": flags,
            "injection_flag_count": len(flags),
            "safe_to_apply": len(flags) == 0,
            "reason": reason,
            "proposed_sha": self._sha256(canonical),
            "is_update": bool(existing),
            # Score the PROPOSED spec (what the human is about to apply), not the
            # version on disk. The old form returned the stored skill's score for
            # an update (stale body) and a hardcoded 0 for a brand-new skill,
            # making the preview's governance signal misleading.
            "governance_score": self._score_spec(spec),
        }

    def apply_skill(self, spec: dict, reason: str, *, scan: bool = True) -> dict:
        """Atomically add/update a skill, enforcing the injection gate.

        Requires a non-empty human ``reason``. With ``scan=True`` (default) a skill
        whose canonical text contains injection patterns raises
        ``PromptInjectionError`` before any write -- closing the skill-poisoning
        vector. The write is atomic (tmp + os.replace).
        """
        self._validate_spec(spec)
        if not (isinstance(reason, str) and reason.strip()):
            raise SkillRegistryError(
                "apply_skill requires a non-empty human reason",
                details={"name": spec.get("name")},
            )
        if not (self.agentic_cfg.is_write_mode and self.agentic_cfg.writes_enabled):
            raise SkillRegistryError(
                "apply_skill requires agentic.mode='write' and agentic.writes_enabled=true",
                details={
                    "name": spec.get("name"),
                    "mode": self.agentic_cfg.mode,
                    "writes_enabled": self.agentic_cfg.writes_enabled,
                },
            )

        canonical = self._canonical(spec)
        if scan:
            flags = self._scan_injection(canonical)
            if flags:
                audit_log({
                    "event": "agentic_skill_injection_blocked",
                    "name": spec["name"],
                    "reason": reason,
                    "injection_flag_count": len(flags),
                })
                raise PromptInjectionError(
                    "Proposed skill contains injection patterns; refusing to apply",
                    details={"injection_flags": flags, "name": spec["name"]},
                )

        new_sha = self._sha256(canonical)
        ts = _utcnow()
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        lock_dir = self.registry_path.with_suffix(self.registry_path.suffix + ".lock.d")
        with self._lock:  # serialize threads within this process
            _acquire_registry_lock(lock_dir)  # serialize other processes too
            try:
                # Rebase on the LATEST committed state, not stale self._data. A
                # concurrent process may have applied a skill since we loaded at
                # construction; re-reading from disk here carries its skill and
                # version forward instead of overwriting them with a colliding
                # version. (self._data is loaded once in __init__ and the in-process
                # lock can't see another process's write -- only a re-read can.)
                data = dict(self._load())
                skills = dict(data.get("skills", {}))
                history = list(data.get("history", []))
                skills[spec["name"]] = {
                    "name": spec["name"],
                    "description": spec["description"],
                    "body": spec["body"],
                    "sha256": new_sha,
                    "reason": reason,
                    "updated": ts,
                }
                new_version = int(data.get("version", 0)) + 1
                history.append({
                    "version": new_version,
                    "name": spec["name"],
                    "sha256": new_sha,
                    "reason": reason,
                    "timestamp": ts,
                })
                data.update({"version": new_version, "updated": ts,
                             "skills": skills, "history": history})
                self._atomic_write(data)
                self._data = data
            finally:
                _release_registry_lock(lock_dir)

        audit_log({
            "event": "agentic_skill_applied",
            "name": spec["name"],
            "reason": reason,
            "version": new_version,
            "sha256": new_sha,
        })
        # Score the spec we just wrote directly, mirroring propose_skill. The
        # canonical text is identical to what landed in the registry, so this
        # avoids a redundant get_skill() lookup + re-scan and stays correct even
        # if a concurrent writer mutates the registry after our atomic write.
        return {"status": "applied", "name": spec["name"],
                "version": new_version, "sha256": new_sha, "governance_score": self._score_spec(spec)}


__all__ = ["SkillRegistry"]
