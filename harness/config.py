"""HarnessConfig: home-directory layout and read-only view of CyClaw config.

The harness keeps ALL of its mutable state under a per-user home directory —
``%USERPROFILE%\\.CyClaw`` on Windows 10/11 and Server 2019-2022 (``~/.CyClaw``
elsewhere), overridable with the ``CYCLAW_HOME`` env var. The repo checkout
itself is never written to.

Layout created on first run::

    ~/.CyClaw/
      config.json        harness settings (selected model, soul on/off)
      sessions/          one JSON file per chat session (messages + tokens)
      skills/            copy of .claude/skills for user browsing/custom edits
      tools/             user-local tool notes / connector state
      memory/            harness memory log (audit JSONL, NOT the soul)
      registry.json      cached merged registry snapshot

Config values that belong to CyClaw proper (model names, URLs, timeouts) are
READ from the repo's ``config.yaml`` via the shared cached loader; this module
never writes them.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from utils.errors import AgenticError

logger = logging.getLogger("cyclaw.harness.config")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _REPO_ROOT / "config.yaml"
_SKILLS_SRC = _REPO_ROOT / ".claude" / "skills"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790
_HOME_ENV = "CYCLAW_HOME"

_HOME_SUBDIRS = ("sessions", "skills", "tools", "memory")


class HarnessConfigError(AgenticError):
    """Harness-local configuration failure (bad home, bad config.json)."""

    def __init__(self, message: str, code: str = "HARNESS_CONFIG_ERROR", details: dict | None = None):
        super().__init__(message, code=code, details=details)


def default_home() -> Path:
    """Per-user harness home. ``CYCLAW_HOME`` wins; then the OS profile dir."""
    override = os.environ.get(_HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    profile = os.environ.get("USERPROFILE", "").strip()  # Windows 10/11 + Server 2019-2022
    if profile:
        return Path(profile) / ".CyClaw"
    return Path.home() / ".CyClaw"


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HarnessConfigError(f"{path.name} is not valid JSON", details={"path": str(path)}) from exc
    if not isinstance(data, dict):
        raise HarnessConfigError(f"{path.name} must contain a JSON object", details={"path": str(path)})
    return data


@dataclass
class HarnessConfig:
    """Resolved harness settings. Mutable fields persist to ``config.json``."""

    home: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    soul_enabled: bool = True
    selected_model: str = ""
    repo_root: Path = field(default=_REPO_ROOT)

    # -- paths ---------------------------------------------------------
    @property
    def config_path(self) -> Path:
        return self.home / "config.json"

    @property
    def sessions_dir(self) -> Path:
        return self.home / "sessions"

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def tools_dir(self) -> Path:
        return self.home / "tools"

    @property
    def memory_dir(self) -> Path:
        return self.home / "memory"

    @property
    def skills_src(self) -> Path:
        return self.repo_root / ".claude" / "skills"

    # -- lifecycle -----------------------------------------------------
    @classmethod
    def load(cls, home: Path | None = None) -> HarnessConfig:
        """Create the home layout and load (or seed) ``config.json``."""
        resolved = (home or default_home()).expanduser().resolve()
        cfg = cls(home=resolved)
        cfg._ensure_layout()
        if cfg.config_path.exists():
            stored = _load_json(cfg.config_path)
            if isinstance(stored.get("soul_enabled"), bool):
                cfg.soul_enabled = stored["soul_enabled"]
            if isinstance(stored.get("selected_model"), str):
                cfg.selected_model = stored["selected_model"]
            port = stored.get("port")
            if isinstance(port, int) and 1024 <= port <= 65535:
                cfg.port = port
        else:
            cfg.save()
        cfg._seed_skills()
        return cfg

    def save(self) -> None:
        """Atomic write (tmp + os.replace), matching the soul/registry pattern."""
        payload = {
            "soul_enabled": self.soul_enabled,
            "selected_model": self.selected_model,
            "port": self.port,
        }
        fd, tmp = tempfile.mkstemp(dir=str(self.home), prefix=".config.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, self.config_path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _ensure_layout(self) -> None:
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            for sub in _HOME_SUBDIRS:
                (self.home / sub).mkdir(exist_ok=True)
        except OSError as exc:
            raise HarnessConfigError(
                "cannot create harness home directory",
                details={"home": str(self.home), "error": str(exc)},
            ) from exc

    def _seed_skills(self) -> None:
        """Copy repo ``.claude/skills`` SKILL.md files into the home once.

        The home copy is the user-facing catalog (browse/edit); the repo copy
        stays the governed source. Existing home skills are never overwritten.
        """
        if not self.skills_src.is_dir():
            return
        for skill_md in sorted(self.skills_src.glob("*/SKILL.md")):
            dest_dir = self.skills_dir / skill_md.parent.name
            dest = dest_dir / "SKILL.md"
            if dest.exists():
                continue
            try:
                dest_dir.mkdir(exist_ok=True)
                dest.write_text(skill_md.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                logger.warning("harness: could not seed skill %s", skill_md.parent.name)
