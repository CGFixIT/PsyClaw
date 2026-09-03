"""Subprocess import of shipped gate.py for fail-closed boot arms.

The CYCLAW_API_KEY-unset warning and auth.enabled AuthManager construction
run at module import. In-process tests always set the key and leave auth off,
so a fresh interpreter is the only way to drive those arms on the real file.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(snippet: str, *, extra_env: dict[str, str] | None = None, drop_keys: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["GROK_API_KEY"] = env.get("GROK_API_KEY") or "dummy"
    for key in drop_keys:
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", snippet],
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_import_warns_when_cyclaw_api_key_unset() -> None:
    snippet = r"""
import logging
seen = []
_orig = logging.Logger.warning

def _warning(self, msg, *args, **kwargs):
    text = str(msg) if not args else (msg % args)
    seen.append(text)
    return _orig(self, msg, *args, **kwargs)

logging.Logger.warning = _warning
import os
assert not os.environ.get("CYCLAW_API_KEY", "")
import gate
assert any("CYCLAW_API_KEY is not set" in m for m in seen), seen
assert gate.auth_manager is None
print("UNSET_KEY_BOOT_OK")
"""
    result = _run(snippet, drop_keys=("CYCLAW_API_KEY",))
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "UNSET_KEY_BOOT_OK" in result.stdout


def test_import_constructs_auth_manager_when_enabled() -> None:
    snippet = r"""
import io, os, tempfile, yaml
from pathlib import Path
from unittest.mock import patch

repo = Path(os.environ["CYCLAW_REPO_ROOT"])
raw = (repo / "config.yaml").read_text(encoding="utf-8")
cfg = yaml.safe_load(raw)
db = Path(tempfile.mkdtemp()) / "cyclaw_auth.db"
auth = dict(cfg.get("auth") or {})
auth["enabled"] = True
auth["db_path"] = str(db)
cfg["auth"] = auth
blob = yaml.safe_dump(cfg)
target = (repo / "config.yaml").resolve()

real_open = open

def fake_open(path, *args, **kwargs):
    p = Path(path)
    try:
        if p.resolve() == target:
            return io.StringIO(blob)
    except OSError:
        pass
    return real_open(path, *args, **kwargs)

import builtins
builtins.open = fake_open
os.environ["CYCLAW_API_KEY"] = "boot-auth-key-32chars-minimum!!"
import gate
assert gate.auth_manager is not None
assert gate.auth_manager.db_path == db
assert gate._request_path_enforcement_active() is True
print("AUTH_BOOT_OK")
"""
    result = _run(
        snippet,
        extra_env={
            "CYCLAW_REPO_ROOT": str(REPO_ROOT),
            "CYCLAW_API_KEY": "boot-auth-key-32chars-minimum!!",
        },
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "AUTH_BOOT_OK" in result.stdout
    # bootstrap_if_empty prints the operator banner on a fresh db
    assert "username: admin" in combined
    assert "cyclaw-user passwd" in combined
