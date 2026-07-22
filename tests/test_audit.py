# ============================================================================
# TEST STATUS (verified 2026-06-19 against HEAD f5934db):
# All 7 tests pass. audit_log(), hash_query(), redact_sensitive(), and
# reset_config_cache() signatures match utils/logger.py at HEAD exactly.
# ============================================================================
"""Unit tests for audit logging — hashing, redaction, JSONL format."""

import json
import threading
from pathlib import Path

import pytest
import yaml

from utils.logger import audit_log, hash_query, redact_sensitive, reset_config_cache


@pytest.fixture(autouse=True)
def clear_config_cache():
    """Reset config cache between tests."""
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def audit_config(tmp_path):
    """Config with audit logging to temp dir."""
    audit_file = str(tmp_path / "audit.jsonl")
    cfg = {
        "logging": {
            "audit_file": audit_file,
            "audit_fields": {"include_query_hash": True}
        },
        "policy": {
            "privacy": {
                "redact_emails": True,
                "redact_ips": True,
                "redact_secrets_like": ["AKIA[0-9A-Z]{16}"]
            }
        }
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(cfg, f)
    return str(config_path), audit_file


class TestHashQuery:
    def test_deterministic(self):
        h1 = hash_query("test query")
        h2 = hash_query("test query")
        assert h1 == h2

    def test_different_inputs_different_hashes(self):
        assert hash_query("query a") != hash_query("query b")

    def test_sha256_length(self):
        h = hash_query("test")
        assert len(h) == 64  # SHA256 hex digest


class TestRedactSensitive:
    def test_redacts_email(self):
        cfg = {"policy": {"privacy": {"redact_emails": True, "redact_ips": False, "redact_secrets_like": []}}}
        result = redact_sensitive("Contact admin@example.com for help", cfg)
        assert "[REDACTED_EMAIL]" in result
        assert "admin@example.com" not in result

    def test_redacts_ip(self):
        cfg = {"policy": {"privacy": {"redact_emails": False, "redact_ips": True, "redact_secrets_like": []}}}
        result = redact_sensitive("Server at 192.168.1.100", cfg)
        assert "[REDACTED_IP]" in result
        assert "192.168.1.100" not in result

    def test_redacts_aws_key(self):
        cfg = {"policy": {"privacy": {"redact_emails": False, "redact_ips": False,
                                       "redact_secrets_like": ["AKIA[0-9A-Z]{16}"]}}}
        result = redact_sensitive("Key: AKIAIOSFODNN7EXAMPLE", cfg)
        assert "[REDACTED_SECRET]" in result

    def test_redacts_anthropic_key(self):
        # Real Anthropic keys (sk-ant-api03-...) contain hyphens inside the
        # token body, so the pre-existing OpenAI-style "sk-" pattern (no
        # hyphens allowed) never matches them — this is a distinct pattern.
        cfg = {"policy": {"privacy": {"redact_emails": False, "redact_ips": False,
                                       "redact_secrets_like": ["sk-ant-[a-zA-Z0-9_-]{20,}"]}}}
        secret = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
        result = redact_sensitive(f"Key: {secret}", cfg)
        assert "[REDACTED_SECRET]" in result
        assert secret not in result

    # PR #99 #10: the audit-path secret list must also cover Bearer tokens and
    # api_key= assignment forms (previously only gate._sanitize_error did).
    _SECRET_CFG = {"policy": {"privacy": {"redact_emails": False, "redact_ips": False,
        "redact_secrets_like": [
            r"Bearer\s+[A-Za-z0-9\-_.]+",
            r'[Aa][Pp][Ii][_-]?[Kk][Ee][Yy]["\'\s]*[:=]["\'\s]*[\w\-.]{4,}',
        ]}}}

    def test_redacts_bearer_token(self):
        result = redact_sensitive("Authorization: Bearer abc.def-ghi_123", self._SECRET_CFG)
        assert "[REDACTED_SECRET]" in result
        assert "abc.def-ghi_123" not in result

    def test_invalid_secret_pattern_warns_and_keeps_valid_ones(self, caplog):
        """An unbalanced-bracket regex in redact_secrets_like must not silently
        disable redaction: the bad pattern is skipped WITH a warning (so a config
        typo is visible), and the other valid patterns keep working. A distinct
        pattern string is used so the redactor lru_cache misses and re-compiles."""
        import logging
        cfg = {"policy": {"privacy": {"redact_emails": False, "redact_ips": False,
            "redact_secrets_like": ["sk-[a-z", r"AKIA[0-9A-Z]{16}"]}}}  # first is invalid
        with caplog.at_level(logging.WARNING, logger="cyclaw.logger"):
            result = redact_sensitive("key AKIAIOSFODNN7EXAMPLE here", cfg)
        # The valid AWS-key pattern still redacts.
        assert "[REDACTED_SECRET]" in result
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        # The invalid pattern produced a visible warning rather than a silent drop.
        assert any("failed to compile" in r.message for r in caplog.records)

    def test_redacts_api_key_assignment(self):
        for s in ("api_key=SUPERSECRETVALUE", '"api-key": "XYZ12345"', "apikey = longsecret123"):
            result = redact_sensitive(s, self._SECRET_CFG)
            assert "[REDACTED_SECRET]" in result, s

    def test_api_key_prose_not_over_redacted(self):
        # Anchored to a : or = separator, so plain prose must survive untouched.
        for s in ("plain apikey word here", "see the api key documentation"):
            assert redact_sensitive(s, self._SECRET_CFG) == s


class TestAuditLog:
    def test_writes_jsonl(self, audit_config):
        config_path, audit_file = audit_config
        audit_log({"event": "test", "query": "hello"}, config_path)

        lines = Path(audit_file).read_text().strip().split("\n")
        assert len(lines) == 1

        event = json.loads(lines[0])
        assert event["event"] == "test"
        assert "timestamp" in event

    def test_query_hashed(self, audit_config):
        config_path, audit_file = audit_config
        audit_log({"event": "test", "query": "secret query"}, config_path)

        event = json.loads(Path(audit_file).read_text().strip())
        assert "query" not in event
        assert "query_hash" in event
        assert event["query_hash"] == hash_query("secret query")

    def test_multiple_events_appended(self, audit_config):
        config_path, audit_file = audit_config
        audit_log({"event": "first"}, config_path)
        audit_log({"event": "second"}, config_path)

        lines = Path(audit_file).read_text().strip().split("\n")
        assert len(lines) == 2

    def test_config_cache_is_keyed_by_path(self, tmp_path):
        cfg_a = tmp_path / "a.yaml"
        cfg_b = tmp_path / "b.yaml"
        audit_a = tmp_path / "a.jsonl"
        audit_b = tmp_path / "b.jsonl"
        base = {
            "logging": {"audit_fields": {"include_query_hash": True}},
            "policy": {"privacy": {"redact_emails": False, "redact_ips": False, "redact_secrets_like": []}},
        }
        cfg_a.write_text(
            yaml.safe_dump({**base, "logging": {**base["logging"], "audit_file": str(audit_a)}}),
            encoding="utf-8",
        )
        cfg_b.write_text(
            yaml.safe_dump({**base, "logging": {**base["logging"], "audit_file": str(audit_b)}}),
            encoding="utf-8",
        )

        audit_log({"event": "a"}, str(cfg_a))
        audit_log({"event": "b"}, str(cfg_b))

        assert json.loads(audit_a.read_text(encoding="utf-8"))["event"] == "a"
        assert json.loads(audit_b.read_text(encoding="utf-8"))["event"] == "b"

    def test_threaded_writes_are_complete_jsonl_lines(self, audit_config):
        config_path, audit_file = audit_config

        def write_one(i: int) -> None:
            audit_log({"event": "threaded", "idx": i}, config_path)

        threads = [threading.Thread(target=write_one, args=(i,)) for i in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        lines = Path(audit_file).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 40
        assert {json.loads(line)["idx"] for line in lines} == set(range(40))

    def test_retrieval_error_secret_redacted_in_audit(self, tmp_path):
        """PR #99 #10: a retrieval-degraded error string carrying a token must be
        written to the audit log with the secret redacted, not in cleartext."""
        audit_file = str(tmp_path / "audit.jsonl")
        cfg = {
            "logging": {"audit_file": audit_file, "audit_fields": {"include_query_hash": True}},
            "policy": {"privacy": {"redact_emails": False, "redact_ips": False,
                "redact_secrets_like": [
                    r"Bearer\s+[A-Za-z0-9\-_.]+",
                    r'[Aa][Pp][Ii][_-]?[Kk][Ee][Yy]["\'\s]*[:=]["\'\s]*[\w\-.]{4,}',
                ]}},
        }
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(cfg, f)

        audit_log({"event": "retrieval_degraded", "path": "semantic",
                   "error": "upstream 401: Authorization: Bearer leaktoken.123 api_key=ALSOLEAKED1"},
                  str(config_path))

        event = json.loads(Path(audit_file).read_text().strip())
        assert "leaktoken.123" not in event["error"]
        assert "ALSOLEAKED1" not in event["error"]
        assert "[REDACTED_SECRET]" in event["error"]

    def test_nested_dict_strings_recursively_redacted(self, audit_config):
        """Defense in depth: a string nested inside a dict value must be
        redacted too. Pre-fix only top-level string fields were redacted, so
        {"details": {"email": "u@example.com"}} landed in audit.jsonl with the
        email intact."""
        config_path, audit_file = audit_config
        audit_log({
            "event": "complex_event",
            "details": {
                "email": "user@example.com",
                "ip": "192.168.1.1",
                "secret": "AKIAIOSFODNN7EXAMPLE",
                "nested": {"more_email": "deeper@example.com"},
            },
        }, config_path)
        event = json.loads(Path(audit_file).read_text().strip())
        # Original payload values must NOT appear anywhere in the event JSON.
        raw = json.dumps(event)
        assert "user@example.com" not in raw
        assert "deeper@example.com" not in raw
        assert "192.168.1.1" not in raw
        assert "AKIAIOSFODNN7EXAMPLE" not in raw
        # And the redaction placeholders MUST be present.
        assert event["details"]["email"] == "[REDACTED_EMAIL]"
        assert event["details"]["ip"] == "[REDACTED_IP]"
        assert event["details"]["secret"] == "[REDACTED_SECRET]"
        assert event["details"]["nested"]["more_email"] == "[REDACTED_EMAIL]"

    def test_nested_list_strings_recursively_redacted(self, audit_config):
        """A string inside a list element must also be redacted."""
        config_path, audit_file = audit_config
        audit_log({
            "event": "batch_event",
            "errors": ["contact admin@example.com", "AKIAIOSFODNN7EXAMPLE"],
        }, config_path)
        event = json.loads(Path(audit_file).read_text().strip())
        raw = json.dumps(event)
        assert "admin@example.com" not in raw
        assert "AKIAIOSFODNN7EXAMPLE" not in raw
        assert event["errors"][0] == "contact [REDACTED_EMAIL]"
        assert event["errors"][1] == "[REDACTED_SECRET]"

    def test_non_string_scalars_pass_through(self, audit_config):
        """Recursive redaction must not coerce ints/floats/bools/None to str."""
        config_path, audit_file = audit_config
        audit_log({"event": "scalar", "count": 7, "ratio": 0.42,
                   "ok": True, "missing": None}, config_path)
        event = json.loads(Path(audit_file).read_text().strip())
        assert event["count"] == 7
        assert event["ratio"] == 0.42
        assert event["ok"] is True
        assert event["missing"] is None
