"""Offline contract tests for the optional live groundedness evaluator."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml

from tests import judge_eval


def _judge_result(**overrides: object) -> judge_eval.JudgeResult:
    values: dict[str, object] = {
        "groundedness": 0.9,
        "supported_claim_ids": ("E1",),
        "contradicted_claim_ids": (),
        "forbidden_claim_ids": (),
        "reason_codes": ("fully_grounded",),
    }
    values.update(overrides)
    return judge_eval.JudgeResult(**values)  # type: ignore[arg-type]


def test_fixture_has_approved_case_shape_and_distribution() -> None:
    cases = judge_eval.load_cases()

    assert len(cases) == 24
    assert {case.category for case in cases} == {
        "direct_factual",
        "paraphrase",
        "two_source_synthesis",
        "false_premise",
        "out_of_corpus",
    }
    assert all(case.forbidden_claims for case in cases)
    assert all(case.expected_claims and case.expected_source_ids for case in cases if not case.expect_abstention)
    assert all(not case.expected_claims and not case.expected_source_ids for case in cases if case.expect_abstention)


def test_public_safe_corpus_is_fixed_and_symlink_free() -> None:
    manifest = judge_eval._corpus_manifest()

    assert len(manifest) == 6
    assert {entry["source_id"] for entry in manifest} == judge_eval._SOURCE_IDS
    assert all(len(entry["sha256"]) == 64 for entry in manifest)
    assert all(not path.is_symlink() for path in judge_eval.CORPUS_DIR.glob("*.md"))


@pytest.mark.parametrize(
    ("live", "key"),
    [(None, "key"), ("0", "key"), ("1", None), ("1", "   ")],
)
def test_main_refuses_without_both_live_gates(monkeypatch, live: str | None, key: str | None) -> None:
    if live is None:
        monkeypatch.delenv(judge_eval.LIVE_ENV, raising=False)
    else:
        monkeypatch.setenv(judge_eval.LIVE_ENV, live)
    if key is None:
        monkeypatch.delenv(judge_eval.KEY_ENV, raising=False)
    else:
        monkeypatch.setenv(judge_eval.KEY_ENV, key)
    monkeypatch.setattr(judge_eval, "run_suite", lambda: pytest.fail("live suite must not start"))

    assert judge_eval.main([]) == 2


def test_main_refuses_path_or_endpoint_overrides(monkeypatch) -> None:
    monkeypatch.setenv(judge_eval.LIVE_ENV, "1")
    monkeypatch.setenv(judge_eval.KEY_ENV, "key")
    monkeypatch.setattr(judge_eval, "run_suite", lambda: pytest.fail("live suite must not start"))

    assert judge_eval.main(["--corpus", "data/corpus"]) == 2


def test_run_suite_cannot_bypass_live_gate_when_imported(monkeypatch) -> None:
    monkeypatch.delenv(judge_eval.LIVE_ENV, raising=False)
    monkeypatch.setenv(judge_eval.KEY_ENV, "key")
    monkeypatch.setattr(judge_eval, "load_cases", lambda: pytest.fail("index setup must not start"))

    with pytest.raises(judge_eval.EvalError, match=judge_eval.LIVE_ENV):
        judge_eval.run_suite()


def test_main_redacts_unexpected_runtime_failure(monkeypatch, capsys) -> None:
    monkeypatch.setenv(judge_eval.LIVE_ENV, "1")
    monkeypatch.setenv(judge_eval.KEY_ENV, "key")

    def fail() -> dict[str, object]:
        raise RuntimeError("raw provider response must remain private")

    monkeypatch.setattr(judge_eval, "run_suite", fail)

    assert judge_eval.main([]) == 2
    stderr = capsys.readouterr().err
    assert "RuntimeError" in stderr
    assert "raw provider response" not in stderr


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.anthropic.com/v1",
        "https://api.anthropic.com.evil.example/v1",
        "https://user:pass@api.anthropic.com/v1",
        "https://api.anthropic.com:8443/v1",
        "https://api.anthropic.com/v2",
        "https://api.anthropic.com/v1?target=other",
    ],
)
def test_claude_endpoint_is_exactly_pinned(endpoint: str) -> None:
    with pytest.raises(judge_eval.EvalError):
        judge_eval.validate_claude_endpoint(endpoint)


def test_claude_endpoint_accepts_only_official_origin() -> None:
    assert judge_eval.validate_claude_endpoint("https://api.anthropic.com/v1/") == (
        "https://api.anthropic.com/v1"
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com/v1",
        "http://10.0.0.5:11434/v1",
        "http://127.0.0.1:11434/other",
        "http://user:pass@127.0.0.1:11434/v1",
    ],
)
def test_contestant_endpoint_must_be_loopback(endpoint: str) -> None:
    with pytest.raises(judge_eval.EvalError):
        judge_eval.validate_local_endpoint(endpoint)


def test_eval_client_configs_cap_calls_and_disable_fallback() -> None:
    local_cfg, claude_cfg = judge_eval._client_configs(judge_eval._load_root_config())
    local = local_cfg["models"]["local_llm"]  # type: ignore[index]
    claude = claude_cfg["models"]["claude"]  # type: ignore[index]

    assert local["max_tokens"] == 512
    assert local["temperature"] == 0.0
    assert local["retry"]["max_retries"] == 0
    assert local["fallback"]["enabled"] is False
    assert claude["max_tokens"] == 512
    assert claude["retry"]["max_retries"] == 0


def test_judge_parser_accepts_claim_ids_and_bounded_reason_codes() -> None:
    case = judge_eval.load_cases()[0]
    result = judge_eval.parse_judge_result(
        json.dumps({
            "groundedness": 0.95,
            "supported_claim_ids": ["E1"],
            "contradicted_claim_ids": [],
            "forbidden_claim_ids": [],
            "reason_codes": ["fully_grounded"],
        }),
        case,
    )

    assert result.groundedness == 0.95
    assert result.supported_claim_ids == ("E1",)


@pytest.mark.parametrize(
    "payload",
    [
        "```json\n{}\n```",
        '{"groundedness": 1.0, "supported_claim_ids": ["E99"], "contradicted_claim_ids": [], '
        '"forbidden_claim_ids": [], "reason_codes": ["fully_grounded"]}',
        '{"groundedness": 1.0, "supported_claim_ids": ["E1"], "contradicted_claim_ids": [], '
        '"forbidden_claim_ids": [], "reason_codes": ["free form evidence quote"]}',
    ],
)
def test_judge_parser_fails_closed_on_non_schema_output(payload: str) -> None:
    with pytest.raises(judge_eval.EvalError):
        judge_eval.parse_judge_result(payload, judge_eval.load_cases()[0])


def test_case_pass_requires_scores_sources_abstention_and_zero_bad_claims() -> None:
    case = judge_eval.load_cases()[0]
    evidence = (judge_eval.Evidence("aurora_harbor", "maximum daily cargo capacity is 18,000 metric tons"),)

    passed = judge_eval.score_case(case, "18,000 metric tons.", evidence, _judge_result())
    contradicted = judge_eval.score_case(
        case,
        "25,000 metric tons.",
        evidence,
        _judge_result(contradicted_claim_ids=("E1",), supported_claim_ids=()),
    )

    assert passed["pass"] is True
    assert contradicted["pass"] is False
    assert "query" not in passed
    assert "answer" not in passed
    assert "evidence" not in passed


def test_out_of_corpus_case_requires_a_real_abstention() -> None:
    case = next(case for case in judge_eval.load_cases() if case.expect_abstention)
    judge = _judge_result(supported_claim_ids=(), reason_codes=("correct_abstention",), groundedness=1.0)

    passed = judge_eval.score_case(case, "ABSTAIN", (), judge)
    failed = judge_eval.score_case(case, "The director is Morgan.", (), judge)

    assert passed["correct_abstention"] is True
    assert passed["pass"] is True
    assert failed["correct_abstention"] is False
    assert failed["pass"] is False


def test_report_contains_no_raw_queries_answers_evidence_or_claim_text() -> None:
    case = judge_eval.load_cases()[0]
    result = judge_eval.score_case(
        case,
        "18,000 metric tons.",
        (judge_eval.Evidence("aurora_harbor", "maximum daily cargo capacity is 18,000 metric tons"),),
        _judge_result(),
    )
    manifest = judge_eval._corpus_manifest()
    report = judge_eval.build_report(
        case_results=[result],
        index_fingerprint="a" * 64,
        manifest=manifest,
        contestant_provider="ollama",
        contestant_model="local-model",
        judge_model="claude-model",
    )
    serialized = json.dumps(report)

    assert case.query not in serialized
    assert case.expected_claims[0] not in serialized
    assert "18,000 metric tons." not in serialized
    assert all(key not in result for key in ("query", "answer", "evidence", "context"))


def test_write_report_is_local_and_append_only(tmp_path: Path) -> None:
    report = {
        "schema_version": 1,
        "status": "fail",
        "run_id": "run-1",
        "timestamp": "2026-08-21T00:00:00+00:00",
        "git_sha": None,
        "rubric": {},
        "index": {},
        "models": {},
        "aggregate": {},
        "cases": [],
    }

    judge_eval.write_report(report, tmp_path)
    judge_eval.write_report({**report, "run_id": "run-2"}, tmp_path)

    assert json.loads((tmp_path / "eval_report.json").read_text(encoding="utf-8"))["run_id"] == "run-2"
    runs = (tmp_path / "eval_runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["run_id"] for line in runs] == ["run-1", "run-2"]
    assert "cases" not in json.loads(runs[0])


def test_eval_script_is_isolated_from_core_and_required_ci() -> None:
    source = (judge_eval.ROOT / "tests" / "judge_eval.py").read_text(encoding="utf-8")
    imported_roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert imported_roots.isdisjoint({"gate", "graph", "mcp_hybrid_server", "harness"})

    for core_name in ("gate.py", "gate_ops.py", "gate_auth.py", "gate_memory.py", "graph.py", "mcp_hybrid_server.py"):
        assert "judge_eval" not in (judge_eval.ROOT / core_name).read_text(encoding="utf-8")
    workflows = "\n".join(
        path.read_text(encoding="utf-8") for path in (judge_eval.ROOT / ".github" / "workflows").glob("*.yml")
    )
    assert "CYCLAW_EVAL_LIVE" not in workflows
    assert "judge_eval.py" not in workflows


def test_real_chroma_bm25_index_uses_only_eval_corpus(tmp_path: Path, monkeypatch) -> None:
    import retrieval.hybrid_search
    import retrieval.indexer

    monkeypatch.setattr(judge_eval, "_lock_down_local_dependencies", lambda: None)
    monkeypatch.setattr(
        retrieval.indexer,
        "get_embeddings_batch",
        lambda texts, config_path="config.yaml": [[1.0, 0.0, 0.0] for _ in texts],
    )
    monkeypatch.setattr(
        retrieval.hybrid_search,
        "get_embedding",
        lambda text, config_path="config.yaml": [1.0, 0.0, 0.0],
    )

    config_path, fingerprint, manifest = judge_eval.build_eval_index(tmp_path)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    retriever = retrieval.hybrid_search.HybridRetriever(str(config_path))
    try:
        evidence = judge_eval._retrieve_evidence(
            retriever,
            "maximum daily cargo capacity Port Helios 18,000 metric tons",
        )
    finally:
        retriever.close()

    assert len(fingerprint) == 64
    assert len(manifest) == 6
    assert Path(config["corpus"]["path"]).resolve() == judge_eval.CORPUS_DIR.resolve()
    assert Path(config["indexing"]["chroma_path"]).is_relative_to(tmp_path)
    assert Path(config["indexing"]["bm25_path"]).is_relative_to(tmp_path)
    assert "aurora_harbor" in {item.source_id for item in evidence}
