"""Tests for the harness ``/api`` key panel: the writer and its two routes.

The properties worth pinning here are the ones a reviewer cannot verify by
reading a response body, because the whole point of the feature is that the
interesting values never appear in one:

* an arbitrary env name is refused (this is a dotenv writer -- a free-form name
  would be an environment-injection primitive),
* a value carrying a newline is refused (it would forge a second assignment),
* the file lands mode 0600 (POSIX; Windows cannot express it -- see
  ``env_keys._FILE_MODE``) and is written atomically,
* unrelated lines written by macos/setup-cyclaw-keys.sh survive a web write,
* and no route, log line, or response ever carries a secret back out.
"""

from __future__ import annotations

import os
import stat

import httpx
import pytest
from fastapi.testclient import TestClient

import harness.server as harness_server
from harness import env_keys
from harness.config import HarnessConfig
from harness.ollama import HarnessChatClient

_KEY = "harness-api-keys-test-key"
_AUTH = {"Authorization": f"Bearer {_KEY}"}
_SECRET = "sk-test-abcdefghijklmnop-TAIL"


def _chat() -> HarnessChatClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "model": "qwen3.8:27b-mlx",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    return HarnessChatClient(
        base_url="http://127.0.0.1:11434/v1", model="qwen3.8:27b-mlx",
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A throwaway CYCLAW_HOME so no test can touch a real ~/.CyClaw/.env."""
    target = tmp_path / ".CyClaw"
    monkeypatch.setenv("CYCLAW_HOME", str(target))
    return target


@pytest.fixture()
def client(home, monkeypatch):
    monkeypatch.setenv("CYCLAW_API_KEY", _KEY)
    cfg = HarnessConfig.load()
    return TestClient(
        harness_server.create_app(cfg, _chat()),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 51234),
    )


def _full_auth(client) -> dict:
    return {**_AUTH, "X-CyClaw-CSRF": client.app.state.csrf_token}


# --- the writer ------------------------------------------------------------


def test_arbitrary_env_names_are_refused(home):
    """The allowlist is the whole security model of a dotenv writer.

    PATH and LD_PRELOAD are the point: if a caller can name the variable, the
    file stops being a credential store and becomes a way to influence every
    later process that sources it.
    """
    for hostile in ("PATH", "LD_PRELOAD", "PYTHONPATH", "cyclaw_api_key", ""):
        with pytest.raises(env_keys.EnvKeyError):
            env_keys.write_keys({hostile: "x"})


@pytest.mark.parametrize("bad", ["a\nexport PATH=/tmp", "a\rb", "a\x00b", "", "   "])
def test_values_that_could_forge_a_line_are_refused(home, bad):
    with pytest.raises(env_keys.EnvKeyError):
        env_keys.write_keys({"GROK_API_KEY": bad})


def test_no_partial_write_when_one_key_in_the_batch_is_bad(home):
    """Validation runs over the whole batch before anything touches disk."""
    env_keys.write_keys({"GROK_API_KEY": "good-value-1234"})
    before = env_keys.env_file_path().read_text(encoding="utf-8")
    with pytest.raises(env_keys.EnvKeyError):
        env_keys.write_keys({"ANTHROPIC_API_KEY": "fine-value-5678", "PATH": "/tmp"})
    assert env_keys.env_file_path().read_text(encoding="utf-8") == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits do not apply on Windows")
def test_file_is_written_owner_only(home):
    env_keys.write_keys({"GROK_API_KEY": _SECRET})
    mode = stat.S_IMODE(env_keys.env_file_path().stat().st_mode)
    assert mode == 0o600, f"secrets file is {oct(mode)}, not 0600"


def test_shell_script_format_is_matched(home):
    """Both writers share one file, so the on-disk form has to agree.

    macos/setup-cyclaw-keys.sh emits `export KEY='value'` with POSIX
    single-quoting; a different form here would round-trip wrong through its
    _env_unquote (or ours).
    """
    env_keys.write_keys({"GROK_API_KEY": "va'lue"})
    text = env_keys.env_file_path().read_text(encoding="utf-8")
    assert "export GROK_API_KEY='va'\\''lue'" in text
    assert env_keys.read_env_file()["GROK_API_KEY"] == "va'lue"


def test_unrelated_lines_survive_a_write(home):
    """A key the panel does not manage (or an operator comment) must not be
    dropped just because the web UI saved something else."""
    path = env_keys.env_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# hand-written header\nexport SOMETHING_ELSE='keep me'\n"
        "export GROK_API_KEY='old-value-here'\n",
        encoding="utf-8",
    )
    env_keys.write_keys({"GROK_API_KEY": "new-value-here"})
    text = path.read_text(encoding="utf-8")
    assert "# hand-written header" in text
    assert "export SOMETHING_ELSE='keep me'" in text
    assert "old-value-here" not in text
    assert "new-value-here" in text


def test_mask_never_returns_the_secret(home):
    short, long_secret = "abc", "sk-ant-0123456789TAIL"
    assert env_keys.mask(short) == "•" * 8
    assert short not in env_keys.mask(short)
    masked = env_keys.mask(long_secret)
    assert masked.endswith("TAIL")
    assert long_secret not in masked
    assert "0123456789" not in masked


def test_write_result_carries_names_only(home):
    """write_keys' return value is audit-logged verbatim, so it must be safe."""
    result = env_keys.write_keys({"GROK_API_KEY": _SECRET})
    assert _SECRET not in repr(result)
    assert result["written"] == ["GROK_API_KEY"]
    assert result["restart_required"] is True


# --- the routes ------------------------------------------------------------


def test_get_and_post_are_guarded(client):
    assert client.get("/api/keys").status_code == 401
    assert client.post("/api/keys", json={"keys": {"GROK_API_KEY": "x"}}).status_code == 401


def test_status_reports_presence_without_the_value(client, home, monkeypatch):
    # The suite runs with GROK_API_KEY=dummy in the environment, and the panel
    # deliberately reports what the PROCESS has over what the file holds (see
    # the precedence test below). Clear it so this exercises the file path.
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    env_keys.write_keys({"GROK_API_KEY": _SECRET})
    resp = client.get("/api/keys", headers=_full_auth(client))
    assert resp.status_code == 200
    assert _SECRET not in resp.text
    row = next(k for k in resp.json()["keys"] if k["name"] == "GROK_API_KEY")
    assert row["configured"] is True
    assert row["masked"].endswith("TAIL")
    assert row["source"] == "file"
    # Written but not loaded: the state the panel exists to make visible.
    assert row["pending_restart"] is True


def test_live_env_wins_over_the_file_and_is_labelled(client, home, monkeypatch):
    """When the process already holds a DIFFERENT value from the file, the panel
    must report the process's -- that is what gate.py is actually using. Showing
    the file's value would tell the operator a restart already happened."""
    monkeypatch.setenv("GROK_API_KEY", "live-value-in-process")
    env_keys.write_keys({"GROK_API_KEY": "newer-value-on-disk"})
    resp = client.get("/api/keys", headers=_full_auth(client))
    row = next(k for k in resp.json()["keys"] if k["name"] == "GROK_API_KEY")
    assert row["source"] == "env"
    assert row["pending_restart"] is True
    assert "live-value-in-process" not in resp.text
    assert "newer-value-on-disk" not in resp.text


def test_post_stores_and_never_echoes_the_secret(client, home):
    resp = client.post(
        "/api/keys",
        json={"keys": {"GROK_API_KEY": _SECRET}},
        headers=_full_auth(client),
    )
    assert resp.status_code == 200
    assert _SECRET not in resp.text
    assert resp.json()["restart_required"] is True
    assert env_keys.read_env_file()["GROK_API_KEY"] == _SECRET


def test_post_rejects_an_unmanaged_key_with_400(client, home):
    resp = client.post(
        "/api/keys", json={"keys": {"PATH": "/tmp"}}, headers=_full_auth(client),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "ENV_KEY_REJECTED"


def test_self_auth_key_is_flagged_and_not_applied_live(client, home, monkeypatch):
    """CYCLAW_API_KEY is settable, but writing it must not change the key this
    very request authenticated with -- require_api_key reads os.environ per
    request, so a live write would lock the operator out mid-session."""
    resp = client.post(
        "/api/keys",
        json={"keys": {"CYCLAW_API_KEY": "a-brand-new-operator-key"}},
        headers=_full_auth(client),
    )
    assert resp.status_code == 200
    assert resp.json()["self_auth_written"] == ["CYCLAW_API_KEY"]
    # The process env is untouched, so the original key still works.
    assert client.get("/api/keys", headers=_full_auth(client)).status_code == 200


def test_secret_never_reaches_the_log(client, home, caplog):
    with caplog.at_level("DEBUG"):
        client.post(
            "/api/keys",
            json={"keys": {"GROK_API_KEY": _SECRET}},
            headers=_full_auth(client),
        )
    assert _SECRET not in caplog.text
    assert "GROK_API_KEY" in caplog.text


def test_post_emits_audit_event_with_names_only(client, home, monkeypatch):
    """The write must be recorded in the audit log, but only key names -- no values."""
    calls = []
    monkeypatch.setattr(harness_server, "audit_log", lambda event, **kw: calls.append(event))
    resp = client.post(
        "/api/keys",
        json={"keys": {"GROK_API_KEY": _SECRET}},
        headers=_full_auth(client),
    )
    assert resp.status_code == 200
    assert len(calls) == 1
    event = calls[0]
    assert event["event"] == "harness_api_keys_updated"
    assert event["keys"] == ["GROK_API_KEY"]
    assert _SECRET not in str(event)


def test_every_managed_key_is_read_somewhere_in_the_repo():
    """The panel must not offer a key nothing consumes.

    A stale entry is worse than a missing one: it invites an operator to paste
    a credential that will never be read, and to believe a feature is armed.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    sources = [
        text
        for path in root.rglob("*.py")
        if ".git" not in path.parts and "tests" not in path.parts
        for text in [path.read_text(encoding="utf-8", errors="ignore")]
    ]
    blob = "\n".join(sources)
    for spec in env_keys.MANAGED_KEYS:
        assert re.search(rf"\b{spec.name}\b", blob), f"{spec.name} is read nowhere"
