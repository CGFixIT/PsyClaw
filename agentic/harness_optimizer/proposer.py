"""Proposer workspace builder for the harness optimizer scaffold.

Creates local artifact directories only. It does not expose file tools, run
commands, call models, or apply proposals to repository files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from agentic.harness_optimizer.core import Experiment
from utils.errors import AgenticError
from utils.logger import audit_log

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class ProposerWorkspace:
    """Filesystem locations for one candidate proposal."""

    root: Path
    current_dir: Path
    history_dir: Path
    train_visible_dir: Path
    holdout_hidden_dir: Path
    proposal_path: Path
    manifest_path: Path

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "current_dir": str(self.current_dir),
            "history_dir": str(self.history_dir),
            "train_visible_dir": str(self.train_visible_dir),
            "holdout_hidden_dir": str(self.holdout_hidden_dir),
            "proposal_path": str(self.proposal_path),
            "manifest_path": str(self.manifest_path),
        }


def _validate_slug(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _SLUG_RE.match(value):
        raise AgenticError(
            f"{field_name} must match {_SLUG_RE.pattern}",
            details={"field": field_name, "received": value},
        )


def _resolve_child(root: Path, *parts: str) -> Path:
    resolved_root = root.resolve()
    resolved = resolved_root.joinpath(*parts).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise AgenticError(
            "proposer workspace path escaped root",
            details={"root": str(resolved_root), "path": str(resolved)},
        )
    return resolved


def build_proposer_workspace(
    root: str | Path,
    experiment: Experiment,
    variant_id: str,
    *,
    config_path: str = "config.yaml",
    cfg: dict | None = None,
    audit: bool = True,
) -> ProposerWorkspace:
    """Create a local workspace tree for one candidate variant.

    Layout:

    - ``current/`` for allowed surface snapshots and candidate edits
    - ``history/`` for visible prior attempts
    - ``train_visible/`` for visible train failures/cases
    - ``holdout_hidden/`` for hidden holdout artifacts controlled by the runner
    - ``proposal.md`` as the human-readable proposal stub
    - ``surface_manifest.json`` as the local source of truth for surfaces
    """
    _validate_slug(experiment.experiment_id, "experiment.experiment_id")
    _validate_slug(variant_id, "variant_id")

    root_path = Path(root)
    if not root_path.is_absolute():
        root_path = Path.cwd() / root_path
    workspace_root = _resolve_child(root_path, experiment.experiment_id, variant_id)

    current_dir = _resolve_child(workspace_root, "current")
    history_dir = _resolve_child(workspace_root, "history")
    train_visible_dir = _resolve_child(workspace_root, "train_visible")
    holdout_hidden_dir = _resolve_child(workspace_root, "holdout_hidden")
    proposal_path = _resolve_child(workspace_root, "proposal.md")
    manifest_path = _resolve_child(workspace_root, "surface_manifest.json")

    for directory in (current_dir, history_dir, train_visible_dir, holdout_hidden_dir):
        directory.mkdir(parents=True, exist_ok=True)

    if not proposal_path.exists():
        proposal_path.write_text("# Proposal\n\n", encoding="utf-8")
    manifest = {
        "experiment_id": experiment.experiment_id,
        "variant_id": variant_id,
        "target_workspace": experiment.target_workspace,
        "surfaces": [surface.to_dict() for surface in experiment.surfaces],
        "train_visible": list(experiment.train_visible),
        "holdout_hidden": list(experiment.holdout_hidden),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    workspace = ProposerWorkspace(
        root=workspace_root,
        current_dir=current_dir,
        history_dir=history_dir,
        train_visible_dir=train_visible_dir,
        holdout_hidden_dir=holdout_hidden_dir,
        proposal_path=proposal_path,
        manifest_path=manifest_path,
    )
    if audit:
        audit_log(
            {
                "event": "agentic_harness_proposer_workspace_created",
                "experiment_id": experiment.experiment_id,
                "variant_id": variant_id,
                "workspace_root": str(workspace.root),
            },
            config_path=config_path,
            cfg=cfg,
        )
    return workspace
