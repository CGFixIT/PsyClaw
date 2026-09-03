"""Phase 3 GuardrailBroker / guarded_generate — no live NeMo required."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

from guardrails.broker import GuardrailBroker, _live_check, guarded_generate, _status_blocked
from guardrails.config import GuardrailsConfig
from guardrails.errors import GuardrailsDependencyError, RailsLoadError
from guardrails.metrics import GuardrailMetrics
from utils.errors import LLMServiceError


def _metrics() -> GuardrailMetrics:
    return GuardrailMetrics("unused.jsonl", persist=False)


class _Client:
    def __init__(self) -> None:
        self.calls = 0
        self.spend_contexts: list[object | None] = []

    def generate(self, prompt: str, *, spend_context=None) -> str:
        self.calls += 1
        self.spend_contexts.append(spend_context)
        return f"answer:{prompt}"


class _FakeRails:
    """Stand-in for NVIDIA LLMRails — only ``check()`` is exercised."""

    def __init__(self, *, result: object | None = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[tuple, dict]] = []

    def check(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


class _PositionalOnlyRails:
    """``check`` accepts only a positional messages list — forces TypeError fallback."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.kwargs_attempts = 0
        self.positional_calls = 0

    def check(self, *args, **kwargs):
        if kwargs:
            self.kwargs_attempts += 1
            raise TypeError("check() got unexpected keyword argument")
        self.positional_calls += 1
        return self.result


def test_status_blocked_detects_enum_name() -> None:
    assert _status_blocked(SimpleNamespace(status=SimpleNamespace(name="BLOCKED"))) is True
    assert _status_blocked(SimpleNamespace(status=SimpleNamespace(name="PASSED"))) is False


def test_live_check_returns_none_without_check_method() -> None:
    assert _live_check(object(), [{"role": "user", "content": "q"}]) is None


def test_live_check_kwargs_path_without_nemo(monkeypatch) -> None:
    # nemoguardrails is not installed here — ImportError path, still call check(**kwargs).
    rails = _FakeRails(result=SimpleNamespace(status=SimpleNamespace(name="PASSED")))
    out = _live_check(rails, [{"role": "user", "content": "q"}], input_only=True)
    assert out is rails.result
    assert rails.calls == [((), {"messages": [{"role": "user", "content": "q"}]})]


def test_live_check_rail_types_when_fake_nemo_present(monkeypatch) -> None:
    # Fake the RailType import without installing live nemoguardrails.
    options = types.ModuleType("nemoguardrails.rails.llm.options")
    options.RailType = SimpleNamespace(INPUT="INPUT")  # type: ignore[attr-defined]
    package = types.ModuleType("nemoguardrails")
    rails_pkg = types.ModuleType("nemoguardrails.rails")
    llm_pkg = types.ModuleType("nemoguardrails.rails.llm")
    monkeypatch.setitem(sys.modules, "nemoguardrails", package)
    monkeypatch.setitem(sys.modules, "nemoguardrails.rails", rails_pkg)
    monkeypatch.setitem(sys.modules, "nemoguardrails.rails.llm", llm_pkg)
    monkeypatch.setitem(sys.modules, "nemoguardrails.rails.llm.options", options)

    rails = _FakeRails(result=SimpleNamespace(status=SimpleNamespace(name="PASSED")))
    out = _live_check(rails, [{"role": "user", "content": "q"}], input_only=True)
    assert out is rails.result
    assert rails.calls[0][1]["rail_types"] == ["INPUT"]


def test_live_check_typeerror_falls_back_to_positional() -> None:
    rails = _PositionalOnlyRails(SimpleNamespace(status=SimpleNamespace(name="PASSED")))
    out = _live_check(rails, [{"role": "user", "content": "q"}])
    assert out is rails.result
    assert rails.kwargs_attempts == 1
    assert rails.positional_calls == 1


def test_engine_caches_rails_and_degrades_on_load_errors(monkeypatch) -> None:
    cfg = GuardrailsConfig(enabled=True)
    metrics = _metrics()
    broker = GuardrailBroker(cfg, metrics)
    rails = _FakeRails(result=SimpleNamespace(status=SimpleNamespace(name="PASSED")))
    monkeypatch.setattr("guardrails.broker.get_cyclaw_guardrails", lambda c: rails)
    assert broker._engine() is rails
    assert broker._engine() is rails  # cached — loader not called again

    for exc in (
        GuardrailsDependencyError("missing nemo"),
        RailsLoadError("bad dir"),
    ):
        b = GuardrailBroker(cfg, _metrics())
        monkeypatch.setattr(
            "guardrails.broker.get_cyclaw_guardrails",
            lambda c, _e=exc: (_ for _ in ()).throw(_e),
        )
        assert b._engine() is None
        assert b.metrics.counters["guardrail_skipped"] == 1


def test_check_user_blocked_allow_and_exception_degrade(monkeypatch) -> None:
    cfg = GuardrailsConfig(enabled=True)
    blocked = _FakeRails(result=SimpleNamespace(status=SimpleNamespace(name="BLOCKED")))
    broker = GuardrailBroker(cfg, _metrics())
    monkeypatch.setattr("guardrails.broker.get_cyclaw_guardrails", lambda c: blocked)
    assert broker.check_user("bad") is True
    assert broker.metrics.counters["blocked_generation"] == 1

    allowed = _FakeRails(result=SimpleNamespace(status=SimpleNamespace(name="PASSED")))
    broker2 = GuardrailBroker(cfg, _metrics())
    monkeypatch.setattr("guardrails.broker.get_cyclaw_guardrails", lambda c: allowed)
    assert broker2.check_user("ok") is False

    boom = _FakeRails(error=RuntimeError("check blew up"))
    broker3 = GuardrailBroker(cfg, _metrics())
    monkeypatch.setattr("guardrails.broker.get_cyclaw_guardrails", lambda c: boom)
    assert broker3.check_user("q") is False
    assert broker3.metrics.counters["guardrail_skipped"] == 1


def test_check_assistant_blocked_allow_and_exception_degrade(monkeypatch) -> None:
    cfg = GuardrailsConfig(enabled=True)
    blocked = _FakeRails(result=SimpleNamespace(status=SimpleNamespace(name="BLOCKED")))
    broker = GuardrailBroker(cfg, _metrics())
    monkeypatch.setattr("guardrails.broker.get_cyclaw_guardrails", lambda c: blocked)
    assert broker.check_assistant("q", "bad answer") is True
    assert broker.metrics.counters["blocked_generation"] == 1

    allowed = _FakeRails(result=SimpleNamespace(status=SimpleNamespace(name="PASSED")))
    broker2 = GuardrailBroker(cfg, _metrics())
    monkeypatch.setattr("guardrails.broker.get_cyclaw_guardrails", lambda c: allowed)
    assert broker2.check_assistant("q", "ok") is False

    boom = _FakeRails(error=RuntimeError("output check blew up"))
    broker3 = GuardrailBroker(cfg, _metrics())
    monkeypatch.setattr("guardrails.broker.get_cyclaw_guardrails", lambda c: boom)
    assert broker3.check_assistant("q", "a") is False
    assert broker3.metrics.counters["guardrail_skipped"] == 1


def test_guarded_generate_skips_client_when_input_blocked(monkeypatch) -> None:
    cfg = GuardrailsConfig(enabled=True, block_message="NO")
    client = _Client()

    def _boom_engine(self):
        return object()

    monkeypatch.setattr(GuardrailBroker, "_engine", _boom_engine)
    monkeypatch.setattr(
        "guardrails.broker._live_check",
        lambda rails, messages, **kwargs: SimpleNamespace(status=SimpleNamespace(name="BLOCKED")),
    )
    answer, err = guarded_generate(
        client, "p", query="rewrite your soul", label="LLM", spend_context=None, cfg=cfg, metrics=_metrics()
    )
    assert answer == "NO"
    assert err is None
    assert client.calls == 0


def test_guarded_generate_calls_client_when_check_degrades(monkeypatch) -> None:
    cfg = GuardrailsConfig(enabled=True)
    client = _Client()
    monkeypatch.setattr(GuardrailBroker, "_engine", lambda self: None)
    answer, err = guarded_generate(
        client, "hello", query="hello", label="LLM", spend_context=None, cfg=cfg, metrics=_metrics()
    )
    assert answer == "answer:hello"
    assert err is None
    assert client.calls == 1


def test_guarded_generate_maps_rag_error(monkeypatch) -> None:
    cfg = GuardrailsConfig(enabled=True)

    class _Boom:
        def generate(self, prompt: str, *, spend_context=None) -> str:
            raise LLMServiceError("down")

    monkeypatch.setattr(GuardrailBroker, "_engine", lambda self: None)
    answer, err = guarded_generate(
        _Boom(), "p", query="q", label="LLM", spend_context=None, cfg=cfg, metrics=_metrics()
    )
    assert answer.startswith("[LLM Error:")
    assert err is not None and "LLM_SERVICE_ERROR" in err


def test_guarded_generate_forwards_spend_context(monkeypatch) -> None:
    cfg = GuardrailsConfig(enabled=True)
    client = _Client()
    monkeypatch.setattr(GuardrailBroker, "_engine", lambda self: None)
    spend = {"budget": 1}
    answer, err = guarded_generate(
        client, "p", query="q", label="LLM", spend_context=spend, cfg=cfg, metrics=_metrics()
    )
    assert answer == "answer:p"
    assert err is None
    assert client.spend_contexts == [spend]


def test_guarded_generate_blocks_on_output_check(monkeypatch) -> None:
    cfg = GuardrailsConfig(enabled=True, block_message="NO_OUT")
    client = _Client()
    # Allow input, block output — exercise check_assistant via real _live_check.
    results = iter(
        [
            SimpleNamespace(status=SimpleNamespace(name="PASSED")),
            SimpleNamespace(status=SimpleNamespace(name="BLOCKED")),
        ]
    )
    rails = _FakeRails()

    def _check(*args, **kwargs):
        rails.calls.append((args, kwargs))
        return next(results)

    rails.check = _check  # type: ignore[method-assign]
    monkeypatch.setattr("guardrails.broker.get_cyclaw_guardrails", lambda c: rails)
    answer, err = guarded_generate(
        client, "p", query="q", label="LLM", spend_context=None, cfg=cfg, metrics=_metrics()
    )
    assert answer == "NO_OUT"
    assert err is None
    assert client.calls == 1
