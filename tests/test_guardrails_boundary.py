"""Tests for guardrails.boundary -- Phase 1 typed decisions only."""

from __future__ import annotations

import pytest

from guardrails.boundary import (
    ArtifactManifest,
    GuardrailDecision,
    GuardrailStage,
    GuardrailVerdict,
    SafetyEnvelope,
    SourceProvenance,
    ToolIntent,
    ToolObservation,
    TrustLevel,
    guardrail_decision,
)
from guardrails.errors import GuardrailsConfigError


def _minimal_decision_kwargs() -> dict:
    return {
        "stage": GuardrailStage.INPUT,
        "verdict": GuardrailVerdict.ALLOW,
        "reason_codes": ("ok",),
        "rail_ids": ("check_injection",),
        "policy_hash": "a" * 64,
        "config_hash": "b" * 64,
        "model": "qwen3.8:27b-mlx",
        "provider": "ollama",
        "model_digest": "c" * 64,
        "content_hash": "d" * 64,
        "provenance_ids": ("src-1",),
        "latency_ms": 1.5,
        "degraded": False,
    }


def test_guardrail_decision_factory_builds():
    decision = guardrail_decision(**_minimal_decision_kwargs())
    assert isinstance(decision, GuardrailDecision)
    assert decision.stage is GuardrailStage.INPUT
    assert decision.verdict is GuardrailVerdict.ALLOW
    assert decision.reason_codes == ("ok",)
    assert decision.degraded is False
    assert decision.content_hash == "d" * 64
    assert decision.argument_hash == ""


def test_guardrail_decision_has_no_raw_prompt_or_response_attrs():
    decision = guardrail_decision(**_minimal_decision_kwargs())
    for name in ("prompt", "response", "query", "tool_arguments", "tool_result"):
        assert not hasattr(decision, name)


@pytest.mark.parametrize(
    "forbidden",
    [
        "prompt",
        "response",
        "query",
        "tool_arguments",
        "tool_result",
        "api_key",
        "authorization",
        "password",
        "token",
    ],
)
def test_guardrail_decision_rejects_forbidden_keys(forbidden: str):
    kwargs = _minimal_decision_kwargs()
    kwargs[forbidden] = "secret-value"
    with pytest.raises(GuardrailsConfigError) as exc_info:
        guardrail_decision(**kwargs)
    assert forbidden in str(exc_info.value)


def test_guardrail_decision_hash_keys_are_not_forbidden():
    # query_hash is not a decision field (TypeError), but must not trip the
    # sensitive-key GuardrailsConfigError -- digests are the allowed form.
    kwargs = _minimal_decision_kwargs()
    kwargs["query_hash"] = "e" * 64
    with pytest.raises(TypeError):
        guardrail_decision(**kwargs)


def test_guardrail_decision_direct_ctor_rejects_unknown_field():
    kwargs = _minimal_decision_kwargs()
    kwargs["prompt"] = "should-not-stick"
    with pytest.raises(TypeError):
        GuardrailDecision(**kwargs)


def test_all_guardrail_stage_members():
    expected = {
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
    assert {m.value for m in GuardrailStage} == expected


def test_all_guardrail_verdict_members():
    expected = {
        "allow",
        "block",
        "redact",
        "quarantine",
        "require_approval",
        "degraded",
    }
    assert {m.value for m in GuardrailVerdict} == expected


def test_phase3_stubs_importable():
    assert SafetyEnvelope() is not None
    assert ToolIntent() is not None
    assert ToolObservation() is not None
    assert ArtifactManifest() is not None


def test_source_provenance_hash_only_fields():
    prov = SourceProvenance(
        source_id="chunk-1",
        trust=TrustLevel.UNTRUSTED,
        content_hash="f" * 64,
        mime_type="text/plain",
        size=12,
    )
    assert prov.content_hash == "f" * 64
    assert not hasattr(prov, "content")
    assert not hasattr(prov, "bytes")
    assert prov.trust is TrustLevel.UNTRUSTED
