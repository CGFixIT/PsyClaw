r"""HarnessConfig: home-directory layout and read-only view of CyClaw config.

The harness keeps ALL of its mutable state under a per-user home directory —
``%USERPROFILE%\.CyClaw`` on Windows 10/11 and Server 2019-2022 (``~/.CyClaw``
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
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from utils.errors import AgenticError

logger = logging.getLogger("cyclaw.harness.config")

_REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790
_HOME_ENV = "CYCLAW_HOME"
_UTF8 = "utf-8"
_SKILLS_DIRNAME = "skills"
_MIN_USER_PORT = 1024
_MAX_PORT = 65535

_HOME_SUBDIRS = ("sessions", _SKILLS_DIRNAME, "tools", "memory")


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
        parsed = json.loads(path.read_text(encoding=_UTF8))
    except json.JSONDecodeError as exc:
        raise HarnessConfigError(f"{path.name} is not valid JSON", details={"path": str(path)}) from exc
    if not isinstance(parsed, dict):
        raise HarnessConfigError(f"{path.name} must contain a JSON object", details={"path": str(path)})
    return parsed


def _write_and_replace(fd: int, staged: str, path: Path, payload: dict) -> None:
    with os.fdopen(fd, "w", encoding=_UTF8) as stream:
        json.dump(payload, stream, indent=2)
    os.replace(staged, path)


def _discard_staged(staged: str) -> None:
    """Best-effort cleanup of a staged temp file after a failed replace."""
    try:
        os.unlink(staged)
    except OSError:
        ...  # the replace either happened or it did not; nothing to enforce here


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Atomic JSON write (staged file + os.replace), the soul/registry pattern."""
    staged_dir = str(path.parent)
    fd, staged = tempfile.mkstemp(dir=staged_dir, prefix=".staged.", suffix=".tmp")
    try:
        _write_and_replace(fd, staged, path, payload)
    except OSError:
        _discard_staged(staged)
        raise


@dataclass
class HarnessConfig:
    """Resolved harness settings. Mutable fields persist to ``config.json``.

    Path fields are derived in ``__post_init__`` from ``home`` (fields, not
    properties, so callers get plain ``Path`` attributes).
    """

    home: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    soul_enabled: bool = True
    selected_model: str = ""
    repo_root: Path = field(default=_REPO_ROOT)
    config_path: Path = field(init=False)
    sessions_dir: Path = field(init=False)
    skills_dir: Path = field(init=False)
    tools_dir: Path = field(init=False)
    memory_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.config_path = self.home / "config.json"
        self.sessions_dir = self.home / "sessions"
        self.skills_dir = self.home / _SKILLS_DIRNAME
        self.tools_dir = self.home / "tools"
        self.memory_dir = self.home / "memory"

    @classmethod
    def load(cls, home: Path | None = None) -> HarnessConfig:
        """Create the home layout and load (or seed) ``config.json``."""
        cfg = cls(home=(home or default_home()).expanduser().resolve())
        cfg._ensure_layout()
        if cfg.config_path.exists():
            cfg._apply_stored(_load_json(cfg.config_path))
        else:
            cfg.save()
        cfg._seed_skills()
        return cfg

    def save(self) -> None:
        """Persist the mutable fields via an atomic staged write."""
        _atomic_write_json(self.config_path, {
            "soul_enabled": self.soul_enabled,
            "selected_model": self.selected_model,
            "port": self.port,
        })

    def _apply_stored(self, stored: dict) -> None:
        if isinstance(stored.get("soul_enabled"), bool):
            self.soul_enabled = stored["soul_enabled"]
        if isinstance(stored.get("selected_model"), str):
            self.selected_model = stored["selected_model"]
        port = stored.get("port")
        if isinstance(port, int) and _MIN_USER_PORT <= port <= _MAX_PORT:
            self.port = port

    def _ensure_layout(self) -> None:
        try:
            self.home.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HarnessConfigError(
                "cannot create harness home directory",
                details={"home": str(self.home), "error": str(exc)},
            ) from exc
        for sub in _HOME_SUBDIRS:
            (self.home / sub).mkdir(exist_ok=True)

    def _seed_skills(self) -> None:
        """Copy repo ``.claude/skills`` SKILL.md files into the home once.

        The home copy is the user-facing catalog (browse/edit); the repo copy
        stays the governed source. Existing home skills are never overwritten.
        """
        skills_src = self.repo_root / ".claude" / _SKILLS_DIRNAME
        if not skills_src.is_dir():
            return
        for skill_md in sorted(skills_src.glob("*/SKILL.md")):
            self._seed_one_skill(skill_md)

    def _seed_one_skill(self, skill_md: Path) -> None:
        dest = self.skills_dir / skill_md.parent.name / "SKILL.md"
        if dest.exists():
            return
        with suppress(OSError):
            dest.parent.mkdir(exist_ok=True)
        try:
            dest.write_text(skill_md.read_text(encoding=_UTF8), encoding=_UTF8)
        except OSError:
            logger.warning("harness: could not seed skill %s", skill_md.parent.name)
