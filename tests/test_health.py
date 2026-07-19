"""Unit tests for utils/health.py — external-dependency health probes.

health.py was previously only ever *mocked out* (``patch("gate.check_all")``
in test_gate.py); none of its own branches were exercised directly:
  - ``_ping`` success vs. failure (and the URL-redaction on failure)
  - ``check_all`` offline (LM Studio only) vs. hybrid (Grok) paths
  - the hybrid Grok key-set / key-missing split
  - the ``_health_cfg`` per-path parse cache

All HTTP is mocked; no live service is required.
"""

import httpx
import pytest
import yaml

from utils import health

# Inert test fixtures mirroring the real loopback Ollama endpoint
# (http://127.0.0.1:11434, the value shipped in config.yaml) so the probes are
# exercised against realistic inputs. No socket is ever opened -- httpx is
# mocked in every test. DevSkim flags the http scheme + localhost on URL
# literals; suppressed here per the repo convention (cf. the inline
# `# DevSkim: ignore` markers in retrieval/hybrid_search.py and
# utils/personality.py).
_OLLAMA_BASE = "http://127.0.0.1:11434/v1"  # DevSkim: ignore DS137138,DS162092
_OLLAMA_MODELS = "http://127.0.0.1:11434/models"  # DevSkim: ignore DS137138,DS162092
_HOST_MODELS = "http://host/models"  # DevSkim: ignore DS137138


def _write_cfg(tmp_path, *, mode="offline", grok_enabled=False, grok_model=None,
               claude_enabled=False, claude_model=None):
    grok_cfg = {"enabled": grok_enabled, "base_url": "https://api.x.ai/v1"}
    if grok_model is not None:
        grok_cfg["model"] = grok_model
    claude_cfg = {"enabled": claude_enabled, "base_url": "https://api.anthropic.com/v1",
                  "anthropic_version": "2023-06-01"}
    if claude_model is not None:
        claude_cfg["model"] = claude_model
    cfg = {
        "app": {"mode": mode},
        "models": {
            "local_llm": {"base_url": _OLLAMA_BASE},
            "grok": grok_cfg,
            "claude": claude_cfg,
        },
    }
    p = tmp_path / "config.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f)
    return str(p)


class _OKResp:
    def raise_for_status(self):
        return None

    def json(self):
        # Default probe body: raising here exercises the tolerant parse path
        # (an unparseable body must never fail an up-endpoint availability probe).
        raise ValueError("no JSON body configured on this fake")


class _ModelsResp(_OKResp):
    """OpenAI-style /models envelope with a configurable model-id list."""

    def __init__(self, model_ids):
        self._ids = model_ids

    def json(self):
        return {"object": "list", "data": [{"id": mid} for mid in self._ids]}


@pytest.fixture(autouse=True)
def _clear_health_cfg_cache():
    # health.py uses module-level TTL caches; isolate every test from cached
    # parses/probes by emptying them before and after each test.
    health._cfg_cache.clear()
    health._status_cache.clear()
    yield
    health._cfg_cache.clear()
    health._status_cache.clear()


class TestPing:
    def test_ping_healthy(self, monkeypatch):
        monkeypatch.setattr(health, "_http_get", lambda url, **kw: _OKResp())
        status = health._ping(_HOST_MODELS, "ollama")
        assert status.healthy is True
        assert status.name == "ollama"
        assert status.latency_ms is not None
        assert status.error is None

    def test_ping_unreachable_redacts_url(self, monkeypatch):
        def boom(url, **kw):
            raise httpx.ConnectError(f"cannot connect to {_OLLAMA_MODELS}")

        monkeypatch.setattr(health, "_http_get", boom)
        status = health._ping(_OLLAMA_MODELS, "ollama")
        assert status.healthy is False
        # The URL (possible creds / internal hostnames) must not leak into /health.
        assert "127.0.0.1" not in status.error
        assert "[URL REDACTED]" in status.error

    def test_ping_non_2xx_is_unhealthy(self, monkeypatch):
        request = httpx.Request("GET", _HOST_MODELS)
        response = httpx.Response(503, request=request)

        class _Resp:
            def raise_for_status(self):
                raise httpx.HTTPStatusError("503", request=request, response=response)

        monkeypatch.setattr(health, "_http_get", lambda url, **kw: _Resp())
        status = health._ping(_HOST_MODELS, "ollama")
        assert status.healthy is False


class TestCheckAll:
    def test_offline_mode_pings_only_ollama(self, tmp_path, monkeypatch):
        cfg_path = _write_cfg(tmp_path, mode="offline")
        monkeypatch.setattr(health, "_http_get", lambda url, **kw: _OKResp())
        statuses = health.check_all(cfg_path)
        names = {s.name for s in statuses}
        assert names == {"ollama", "embeddings_local"}
        assert all(s.healthy for s in statuses)

    def test_hybrid_without_key_reports_key_not_set(self, tmp_path, monkeypatch):
        cfg_path = _write_cfg(tmp_path, mode="hybrid", grok_enabled=True)
        monkeypatch.setattr(health, "_http_get", lambda url, **kw: _OKResp())
        monkeypatch.delenv("GROK_API_KEY", raising=False)
        statuses = health.check_all(cfg_path)
        grok = next(s for s in statuses if s.name == "grok_api")
        assert grok.healthy is False
        assert "GROK_API_KEY not set" in grok.error

    def test_hybrid_with_grok_block_absent_does_not_raise(self, tmp_path, monkeypatch):
        """A hybrid-mode config whose models.grok block was removed (the operator
        set up claude-only and deleted the unused grok block instead of setting
        enabled: false) must not KeyError -> 500 /health. The grok enabled-check
        must be as defensive as claude's cfg['models'].get('claude', {})."""
        cfg = {
            "app": {"mode": "hybrid"},
            "models": {
                "local_llm": {"base_url": _OLLAMA_BASE},
                # no "grok" key at all
                "claude": {"enabled": False, "base_url": "https://api.anthropic.com/v1",
                           "anthropic_version": "2023-06-01"},
            },
        }
        p = tmp_path / "config.yaml"
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f)
        monkeypatch.setattr(health, "_http_get", lambda url, **kw: _OKResp())
        statuses = health.check_all(str(p))  # must not raise KeyError('grok')
        names = {s.name for s in statuses}
        assert "grok_api" not in names          # grok disabled/absent -> not probed
        assert "ollama" in names

    def test_hybrid_claude_without_key_reports_key_not_set(self, tmp_path, monkeypatch):
        cfg_path = _write_cfg(tmp_path, mode="hybrid", claude_enabled=True)
        monkeypatch.setattr(health, "_http_get", lambda url, **kw: _OKResp())
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        statuses = health.check_all(cfg_path)
        claude = next(s for s in statuses if s.name == "claude_api")
        assert claude.healthy is False
        assert "ANTHROPIC_API_KEY not set" in claude.error

    def test_hybrid_with_key_pings_grok_with_bearer_auth(self, tmp_path, monkeypatch):
        cfg_path = _write_cfg(tmp_path, mode="hybrid", grok_enabled=True)
        seen = {}

        def fake_get(url, **kw):
            seen["url"] = url
            seen["headers"] = kw.get("headers")
            return _OKResp()

        monkeypatch.setattr(health, "_http_get", fake_get)
        monkeypatch.setenv("GROK_API_KEY", "test-key-123")
        statuses = health.check_all(cfg_path)
        grok = next(s for s in statuses if s.name == "grok_api")
        assert grok.healthy is True
        # Last ping is Grok; it must carry the Bearer token (else xAI 401s).
        assert seen["headers"]["Authorization"] == "Bearer test-key-123"
        assert seen["url"].endswith("/models")

    def test_hybrid_with_key_pings_claude_with_anthropic_headers(self, tmp_path, monkeypatch):
        cfg_path = _write_cfg(tmp_path, mode="hybrid", claude_enabled=True, claude_model="claude-sonnet-5")
        seen = {}

        def fake_get(url, **kw):
            if "anthropic" in url:
                seen["url"] = url
                seen["headers"] = kw.get("headers")
                return _ModelsResp(["claude-sonnet-5"])
            return _OKResp()

        monkeypatch.setattr(health, "_http_get", fake_get)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        statuses = health.check_all(cfg_path)
        claude = next(s for s in statuses if s.name == "claude_api")
        assert claude.healthy is True
        assert seen["headers"]["x-api-key"] == "test-key-123"
        assert seen["headers"]["anthropic-version"] == "2023-06-01"
        assert seen["url"] == "https://api.anthropic.com/v1/models"

    def test_hybrid_configured_model_present_is_healthy(self, tmp_path, monkeypatch):
        cfg_path = _write_cfg(tmp_path, mode="hybrid", grok_enabled=True, grok_model="grok-4.3")
        monkeypatch.setattr(
            health, "_http_get", lambda url, **kw: _ModelsResp(["grok-4.3", "grok-4.3-mini"])
        )
        monkeypatch.setenv("GROK_API_KEY", "test-key-123")
        statuses = health.check_all(cfg_path)
        grok = next(s for s in statuses if s.name == "grok_api")
        assert grok.healthy is True

    def test_hybrid_retired_model_pin_reports_unhealthy(self, tmp_path, monkeypatch):
        # The model-pin drift guard: config pins a model the provider no longer
        # lists (xAI retired grok-beta/grok-4). Without this, the rot surfaces
        # only as a runtime 4xx on the first live fallback — after the user
        # already confirmed the escalation.
        cfg_path = _write_cfg(tmp_path, mode="hybrid", grok_enabled=True, grok_model="grok-4")
        monkeypatch.setattr(
            health, "_http_get", lambda url, **kw: _ModelsResp(["grok-4.3", "grok-4.3-mini"])
        )
        monkeypatch.setenv("GROK_API_KEY", "test-key-123")
        statuses = health.check_all(cfg_path)
        grok = next(s for s in statuses if s.name == "grok_api")
        assert grok.healthy is False
        assert "grok-4" in grok.error
        assert "not in provider /models list" in grok.error

    def test_hybrid_unparseable_models_body_stays_healthy(self, tmp_path, monkeypatch):
        # An up endpoint with an odd/unparseable body must never fail the
        # availability probe — the model check is best-effort, not a new
        # failure mode. (_OKResp.json() raises by design.)
        cfg_path = _write_cfg(tmp_path, mode="hybrid", grok_enabled=True, grok_model="grok-4.3")
        monkeypatch.setattr(health, "_http_get", lambda url, **kw: _OKResp())
        monkeypatch.setenv("GROK_API_KEY", "test-key-123")
        statuses = health.check_all(cfg_path)
        grok = next(s for s in statuses if s.name == "grok_api")
        assert grok.healthy is True

    # The three tests above exercise the model-pin drift guard (_ping's
    # expect_model check, utils/health.py) only for Grok. check_all() runs the
    # identical check for Claude (same _ping call shape, just a different
    # provider block) — mirrored here so a stale Claude pin gets the same
    # protection a stale Grok pin already has.
    def test_hybrid_claude_configured_model_present_is_healthy(self, tmp_path, monkeypatch):
        cfg_path = _write_cfg(tmp_path, mode="hybrid", claude_enabled=True, claude_model="claude-sonnet-5")
        monkeypatch.setattr(
            health, "_http_get", lambda url, **kw: _ModelsResp(["claude-sonnet-5", "claude-opus-4-8"])
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        statuses = health.check_all(cfg_path)
        claude = next(s for s in statuses if s.name == "claude_api")
        assert claude.healthy is True

    def test_hybrid_retired_claude_model_pin_reports_unhealthy(self, tmp_path, monkeypatch):
        # Same drift guard as test_hybrid_retired_model_pin_reports_unhealthy,
        # for the Claude provider block: a superseded/renamed model pin the
        # provider no longer lists should surface here, not only as a runtime
        # 4xx on the first live fallback after the user already confirmed it.
        cfg_path = _write_cfg(tmp_path, mode="hybrid", claude_enabled=True, claude_model="claude-2.1")
        monkeypatch.setattr(
            health, "_http_get", lambda url, **kw: _ModelsResp(["claude-sonnet-5", "claude-opus-4-8"])
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        statuses = health.check_all(cfg_path)
        claude = next(s for s in statuses if s.name == "claude_api")
        assert claude.healthy is False
        assert "claude-2.1" in claude.error
        assert "not in provider /models list" in claude.error

    def test_hybrid_unparseable_models_body_stays_healthy_claude(self, tmp_path, monkeypatch):
        # Mirrors the Grok unparseable-body case: an odd/unparseable body on an
        # up Claude endpoint must never fail the availability probe.
        cfg_path = _write_cfg(tmp_path, mode="hybrid", claude_enabled=True, claude_model="claude-sonnet-5")
        monkeypatch.setattr(health, "_http_get", lambda url, **kw: _OKResp())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        statuses = health.check_all(cfg_path)
        claude = next(s for s in statuses if s.name == "claude_api")
        assert claude.healthy is True

    def test_immediate_repeat_uses_status_cache(self, tmp_path, monkeypatch):
        cfg_path = _write_cfg(tmp_path, mode="offline")
        calls = 0

        def fake_get(url, **kw):
            nonlocal calls
            calls += 1
            return _OKResp()

        monkeypatch.setattr(health, "_http_get", fake_get)
        assert health.check_all(cfg_path)[0].healthy is True
        assert health.check_all(cfg_path)[0].healthy is True
        assert calls == 1


class TestHealthCfgCache:
    def test_cfg_parsed_once_per_path(self, tmp_path):
        cfg_path = _write_cfg(tmp_path)
        first = health._health_cfg(cfg_path)
        second = health._health_cfg(cfg_path)
        # Same object identity -> the second call was served from the cache
        # (both calls fall inside the TTL window).
        assert first is second

    def test_cfg_reparsed_after_ttl_expiry(self, tmp_path, monkeypatch):
        # The TTL cache must serve a *fresh* parse once the entry ages past the
        # TTL, so an operator editing config.yaml post-startup is picked up
        # without a process restart (the foot-gun the unbounded lru_cache had).
        cfg_path = _write_cfg(tmp_path)
        clock = {"now": 1000.0}
        monkeypatch.setattr(health.time, "monotonic", lambda: clock["now"])

        first = health._health_cfg(cfg_path)
        # Advance the clock just past the TTL so the cached entry is stale.
        clock["now"] += health._cfg_ttl_sec + 1
        second = health._health_cfg(cfg_path)
        # Different object identity -> the file was re-read and re-parsed.
        assert first is not second
        assert second == first  # same content, freshly parsed
