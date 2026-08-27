"""Load and validate the machine-readable guardrail profile matrix.

Truth source: ``guardrails/profiles.yaml``. Stage names match
``GuardrailStage`` in ``guardrails/boundary.py``. Rejects profiles that
claim ``mode: enforced`` for a rail outside :data:`IMPLEMENTED_RAILS`.

Never imported by ``gate.py``, ``graph.py``, or ``mcp_hybrid_server.py`` (I6).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from guardrails.errors import GuardrailsConfigError

IMPLEMENTED_RAILS: frozenset[str] = frozenset(
    {"check_injection", "check_soul_mutation", "check_grounding", "check_soul_leak"}
)
CONFIGURED_UNIMPLEMENTED: frozenset[str] = frozenset({"check_jailbreak"})

# Known rail names that may appear in a profile (status may still be unknown).
KNOWN_RAILS: frozenset[str] = (
    IMPLEMENTED_RAILS
    | CONFIGURED_UNIMPLEMENTED
    | frozenset({"self_check_facts"})
)

ALLOWED_PROFILE_NAMES: frozenset[str] = frozenset(
    {"off", "deterministic", "nemo_local", "strict_agentic"}
)

# Match GuardrailStage values in guardrails/boundary.py (do not import it here).
KNOWN_STAGES: frozenset[str] = frozenset(
    {
        "input",
        "retrieval",
        "egress",
        "output",
        "reasoning",
        "tool_intent",
        "tool_result",
        "artifact",
        "external_write",
    }
)

STAGE_POSTURES: frozenset[str] = frozenset({"enforced", "audit-only", "out-of-scope"})
RAIL_STATUSES: frozenset[str] = frozenset(
    {"implemented", "configured-unimplemented", "unknown"}
)
RAIL_MODES: frozenset[str] = frozenset({"enforced", "audit-only", "out-of-scope"})

_DEFAULT_PATH = Path(__file__).resolve().parent / "profiles.yaml"


def load_profiles(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Load profiles from YAML; return ``{name: profile_dict}``.

    Default path is ``profiles.yaml`` beside this module. Raises
    :class:`GuardrailsConfigError` on structural or truth-contract violations.
    """
    target = Path(path) if path is not None else _DEFAULT_PATH
    try:
        raw_text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise GuardrailsConfigError(
            f"could not read guardrail profiles: {target}",
            details={"path": str(target), "error": str(exc)},
        ) from exc

    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise GuardrailsConfigError(
            f"invalid YAML in guardrail profiles: {target}",
            details={"path": str(target), "error": str(exc)},
        ) from exc

    if not isinstance(raw, dict) or "profiles" not in raw:
        raise GuardrailsConfigError(
            "profiles.yaml must be a mapping with a top-level 'profiles' key",
            details={"path": str(target)},
        )

    entries = raw["profiles"]
    if not isinstance(entries, list):
        raise GuardrailsConfigError(
            "profiles must be a list of profile objects",
            details={"received_type": type(entries).__name__},
        )

    out: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise GuardrailsConfigError(
                "each profile must be a mapping",
                details={"received_type": type(entry).__name__},
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise GuardrailsConfigError(
                "profile name must be a non-empty string",
                details={"received": name},
            )
        if name not in ALLOWED_PROFILE_NAMES:
            raise GuardrailsConfigError(
                f"unknown profile name: {name!r}",
                details={"received": name, "allowed": sorted(ALLOWED_PROFILE_NAMES)},
            )
        if name in seen:
            raise GuardrailsConfigError(
                f"duplicate profile name: {name!r}",
                details={"name": name},
            )
        seen.add(name)

        stages = entry.get("stages")
        if not isinstance(stages, dict):
            raise GuardrailsConfigError(
                f"profile {name!r} stages must be a mapping",
                details={"profile": name},
            )
        _validate_stages(name, stages)

        rails = entry.get("rails", [])
        if not isinstance(rails, list):
            raise GuardrailsConfigError(
                f"profile {name!r} rails must be a list",
                details={"profile": name},
            )
        validated_rails = _validate_rails(name, rails)

        out[name] = {
            "name": name,
            "description": entry.get("description", ""),
            "stages": dict(stages),
            "rails": validated_rails,
        }

    return out


def _validate_stages(profile: str, stages: dict[str, Any]) -> None:
    for stage_name, posture in stages.items():
        if stage_name not in KNOWN_STAGES:
            raise GuardrailsConfigError(
                f"unknown stage name: {stage_name!r}",
                details={"profile": profile, "stage": stage_name},
            )
        if posture not in STAGE_POSTURES:
            raise GuardrailsConfigError(
                f"unknown stage posture: {posture!r}",
                details={
                    "profile": profile,
                    "stage": stage_name,
                    "allowed": sorted(STAGE_POSTURES),
                },
            )


def _validate_rails(profile: str, rails: list[Any]) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for rail in rails:
        if not isinstance(rail, dict):
            raise GuardrailsConfigError(
                f"profile {profile!r} each rail must be a mapping",
                details={"profile": profile},
            )
        rail_name = rail.get("name")
        if not isinstance(rail_name, str) or not rail_name.strip():
            raise GuardrailsConfigError(
                "rail name must be a non-empty string",
                details={"profile": profile, "received": rail_name},
            )
        if rail_name not in KNOWN_RAILS:
            raise GuardrailsConfigError(
                f"unknown rail name: {rail_name!r}",
                details={"profile": profile, "rail": rail_name},
            )
        if rail_name in seen_names:
            raise GuardrailsConfigError(
                f"duplicate rail name: {rail_name!r}",
                details={"profile": profile, "rail": rail_name},
            )
        seen_names.add(rail_name)

        status = rail.get("status")
        if status not in RAIL_STATUSES:
            raise GuardrailsConfigError(
                f"unknown rail status: {status!r}",
                details={
                    "profile": profile,
                    "rail": rail_name,
                    "allowed": sorted(RAIL_STATUSES),
                },
            )

        mode = rail.get("mode", "out-of-scope")
        if mode not in RAIL_MODES:
            raise GuardrailsConfigError(
                f"unknown rail mode: {mode!r}",
                details={
                    "profile": profile,
                    "rail": rail_name,
                    "allowed": sorted(RAIL_MODES),
                },
            )

        # Fail closed: only IMPLEMENTED_RAILS may claim mode=enforced.
        if mode == "enforced" and rail_name not in IMPLEMENTED_RAILS:
            raise GuardrailsConfigError(
                f"rail {rail_name!r} cannot be enforced (not in IMPLEMENTED_RAILS)",
                details={
                    "profile": profile,
                    "rail": rail_name,
                    "implemented": sorted(IMPLEMENTED_RAILS),
                },
            )

        stage = rail.get("stage")
        if stage is not None and stage not in KNOWN_STAGES:
            raise GuardrailsConfigError(
                f"unknown stage name: {stage!r}",
                details={"profile": profile, "rail": rail_name, "stage": stage},
            )

        validated.append(
            {
                "name": rail_name,
                "status": status,
                "mode": mode,
                "stage": stage,
            }
        )
    return validated
