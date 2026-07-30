"""Failure-path tests for the harness's atomic JSON write helper.

The happy path is already covered by tests/test_harness.py (config roundtrip,
session persistence). These pin the cleanup contract instead: whatever goes
wrong between mkstemp and os.replace, no ``.staged.*.tmp`` file is left behind
and no file descriptor is leaked.

No live services, no harness app -- these call harness.config helpers directly
against tmp_path, so the module imports nothing that needs FastAPI.
"""

from __future__ import annotations

import json
import os

import pytest

from harness.config import _atomic_write_json
from harness.sessions import SessionStoreError


def _staged(directory) -> list[str]:
    return [p.name for p in directory.iterdir() if p.name.startswith(".staged.")]


def test_atomic_write_leaves_no_staged_file_on_success(tmp_path):
    target = tmp_path / "config.json"
    _atomic_write_json(target, {"soul_enabled": True, "port": 8790})
    assert json.loads(target.read_text(encoding="utf-8")) == {"soul_enabled": True, "port": 8790}
    assert _staged(tmp_path) == []


def test_atomic_write_discards_staged_file_on_type_error(tmp_path):
    """A non-serializable payload used to orphan the staged file.

    json.dump raises TypeError here, which is not an OSError, so the old
    ``except OSError`` handler never ran and a .staged.*.tmp accumulated in
    ~/.CyClaw on every occurrence.
    """
    target = tmp_path / "config.json"
    with pytest.raises(TypeError):
        _atomic_write_json(target, {"home": object()})
    assert _staged(tmp_path) == []
    assert not target.exists()


def test_atomic_write_discards_staged_file_on_circular_reference(tmp_path):
    """json.dump raises ValueError on a circular reference -- also not OSError."""
    target = tmp_path / "config.json"
    payload: dict = {}
    payload["self"] = payload
    with pytest.raises(ValueError):
        _atomic_write_json(target, payload)
    assert _staged(tmp_path) == []


def test_atomic_write_closes_fd_when_fdopen_fails(tmp_path, monkeypatch):
    """mkstemp's raw fd must be closed when os.fdopen never takes ownership.

    Asserted by closing the descriptor a second time: if _write_and_replace
    already closed it, the fd is free and the second close raises OSError
    (EBADF). If it leaked, the second close succeeds -- which is the bug.
    """
    captured: list[int] = []
    real_fdopen = os.fdopen

    def _boom(fd, *args, **kwargs):
        captured.append(fd)
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(os, "fdopen", _boom)
    with pytest.raises(OSError):
        _atomic_write_json(tmp_path / "config.json", {"ok": True})
    monkeypatch.setattr(os, "fdopen", real_fdopen)

    assert captured, "os.fdopen was never reached"
    with pytest.raises(OSError):
        os.close(captured[0])
    assert _staged(tmp_path) == []


def test_session_write_maps_serialization_failure_to_typed_error(tmp_path, monkeypatch):
    """SessionStore._write must surface a persist failure as SessionStoreError.

    Without TypeError/ValueError in the handler a non-JSON-safe field would
    escape the store's typed-error contract and reach the client as an
    unhandled 500 rather than a HARNESS_SESSION_ERROR.
    """
    from harness import sessions as sessions_mod

    store = sessions_mod.SessionStore(tmp_path)
    session = store.create(model="qwen2.5:7b", title="t")

    def _boom(*_args, **_kwargs):
        raise TypeError("Object of type object is not JSON serializable")

    monkeypatch.setattr(sessions_mod, "_atomic_write_json", _boom)
    with pytest.raises(SessionStoreError):
        store.rename(session.session_id, "renamed")
