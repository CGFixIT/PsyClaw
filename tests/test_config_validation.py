"""Tests for utils.config_validation validators."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from utils.config_validation import (
    resolve_grok_reasoning_effort,
    resolve_reasoning_effort,
    validate_auth_config,
    validate_boot_timeout_config,
    validate_fallback_confirm_placeholder,
    validate_local_llm_reasoning_effort,
    validate_personality_config,
    validate_retrieval_config,
    validate_tls_config,
)
from utils.errors import ConfigError


def _valid_retrieval() -> dict:
    """The shipped config.yaml defaults -- must always validate."""
    return {
        "retrieval": {
            "top_k_semantic": 5,
            "top_k_keyword": 5,
            "rrf_k": 60,
            "min_score": 0.028,
        }
    }


def test_shipped_defaults_pass():
    validate_retrieval_config(_valid_retrieval())  # must not raise


def test_min_score_zero_and_one_are_inclusive():
    for boundary in (0, 1, 0.0, 1.0):
        cfg = _valid_retrieval()
        cfg["retrieval"]["min_score"] = boundary
        validate_retrieval_config(cfg)  # must not raise


@pytest.mark.parametrize("bad", [1.5, -0.1, 2, -1, "0.5", None, True])
def test_min_score_out_of_range_or_wrong_type_rejected(bad):
    cfg = _valid_retrieval()
    cfg["retrieval"]["min_score"] = bad
    with pytest.raises(ConfigError):
        validate_retrieval_config(cfg)


@pytest.mark.parametrize("key", ["top_k_semantic", "top_k_keyword", "rrf_k"])
@pytest.mark.parametrize("bad", [0, -1, 1.5, "5", None, True])
def test_positive_int_keys_reject_bad_values(key, bad):
    cfg = _valid_retrieval()
    cfg["retrieval"][key] = bad
    with pytest.raises(ConfigError):
        validate_retrieval_config(cfg)


def test_missing_retrieval_block_rejected():
    with pytest.raises(ConfigError):
        validate_retrieval_config({})


def test_retrieval_block_not_a_mapping_rejected():
    with pytest.raises(ConfigError):
        validate_retrieval_config({"retrieval": [1, 2, 3]})


def test_error_message_names_the_offending_key():
    cfg = _valid_retrieval()
    cfg["retrieval"]["rrf_k"] = -5
    with pytest.raises(ConfigError) as exc:
        validate_retrieval_config(cfg)
    assert "rrf_k" in str(exc.value)


# ── validate_personality_config ──────────────────────────────────────────


def _valid_personality() -> dict:
    return {"personality": {"enabled": True, "soul_max_chars": 8000}}


def test_personality_shipped_defaults_pass():
    validate_personality_config(_valid_personality())


def test_personality_disabled_skips_validation():
    validate_personality_config({"personality": {"enabled": False, "soul_max_chars": -1}})


def test_personality_absent_block_skips_validation():
    validate_personality_config({})


@pytest.mark.parametrize("bad", [0, -1, "8000", True, 0.5])
def test_personality_soul_max_chars_rejects_bad_values(bad):
    cfg = _valid_personality()
    cfg["personality"]["soul_max_chars"] = bad
    with pytest.raises(ConfigError):
        validate_personality_config(cfg)


def test_personality_soul_max_chars_omitted_passes():
    validate_personality_config({"personality": {"enabled": True}})


def test_personality_error_message_names_field():
    cfg = _valid_personality()
    cfg["personality"]["soul_max_chars"] = 0
    with pytest.raises(ConfigError) as exc:
        validate_personality_config(cfg)
    assert "soul_max_chars" in str(exc.value)


# ── validate_boot_timeout_config ─────────────────────────────────────────


def _valid_timeouts() -> dict:
    """The shipped config.yaml defaults -- must always validate."""
    return {"api": {"graph_timeout_sec": 780}, "models": {"local_llm": {"timeout_sec": 720}}}


def test_shipped_timeout_defaults_pass():
    validate_boot_timeout_config(_valid_timeouts())  # must not raise


def test_llm_timeout_equal_to_graph_timeout_rejected():
    cfg = _valid_timeouts()
    # Must track _valid_timeouts()'s graph_timeout_sec exactly -- these cases are
    # defined RELATIVE to it, so a bare retune of the fixture alone would silently
    # turn "equal" and "greater" into "less than" and stop testing the rejection.
    cfg["models"]["local_llm"]["timeout_sec"] = 780
    with pytest.raises(ConfigError):
        validate_boot_timeout_config(cfg)


def test_llm_timeout_greater_than_graph_timeout_rejected():
    cfg = _valid_timeouts()
    cfg["models"]["local_llm"]["timeout_sec"] = 850
    with pytest.raises(ConfigError):
        validate_boot_timeout_config(cfg)


def test_timeout_error_message_names_both_values():
    cfg = _valid_timeouts()
    cfg["models"]["local_llm"]["timeout_sec"] = 780
    with pytest.raises(ConfigError) as exc:
        validate_boot_timeout_config(cfg)
    assert "780" in str(exc.value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda cfg: cfg.pop("api"),
        lambda cfg: cfg.pop("models"),
        lambda cfg: cfg["api"].__setitem__("graph_timeout_sec", None),
        lambda cfg: cfg["models"]["local_llm"].__setitem__("timeout_sec", "600"),
        lambda cfg: cfg["models"].__setitem__("local_llm", None),
        lambda cfg: cfg.__setitem__("api", "not-a-mapping"),
    ],
)
def test_missing_or_non_numeric_values_are_a_no_op(mutate):
    """Absent/non-numeric values fall through to gate.py's own cfg.get(...,
    default) handling -- this validator only tightens the case both values
    are explicitly present and already violate the relationship."""
    cfg = _valid_timeouts()
    mutate(cfg)
    validate_boot_timeout_config(cfg)  # must not raise


def test_empty_config_is_a_no_op():
    validate_boot_timeout_config({})


def test_llm_timeout_margin_under_30s_rejected():
    """graph_timeout must be >= llm_timeout + 30 (config.yaml formula)."""
    cfg = {
        "api": {"graph_timeout_sec": 610},
        "models": {"local_llm": {"timeout_sec": 600}},
    }
    with pytest.raises(ConfigError) as exc:
        validate_boot_timeout_config(cfg)
    assert "30" in str(exc.value)


def test_llm_timeout_margin_exactly_30s_ok():
    cfg = {
        "api": {"graph_timeout_sec": 630},
        "models": {"local_llm": {"timeout_sec": 600}},
    }
    validate_boot_timeout_config(cfg)


def test_fallback_confirm_false_rejected():
    cfg = {"policy": {"fallback": {"require_user_confirm": False}}}
    with pytest.raises(ConfigError):
        validate_fallback_confirm_placeholder(cfg)


def test_fallback_confirm_true_ok():
    validate_fallback_confirm_placeholder(
        {"policy": {"fallback": {"require_user_confirm": True}}}
    )


def test_fallback_confirm_absent_ok():
    validate_fallback_confirm_placeholder({"policy": {"fallback": {}}})
    validate_fallback_confirm_placeholder({})


def test_fallback_confirm_non_dict_fallback_is_noop():
    validate_fallback_confirm_placeholder({"policy": {"fallback": "nope"}})


# ── resolve_reasoning_effort / grok ───────────────────────────────────────


def test_resolve_reasoning_effort_unset_and_blank():
    assert resolve_reasoning_effort("nope") is None  # type: ignore[arg-type]
    assert resolve_reasoning_effort({}) is None
    assert resolve_reasoning_effort({"reasoning_effort": None}) is None
    assert resolve_reasoning_effort({"reasoning_effort": "  "}) is None


def test_resolve_reasoning_effort_valid_and_invalid():
    assert resolve_reasoning_effort({"reasoning_effort": "High"}) == "high"
    with pytest.raises(ConfigError):
        resolve_reasoning_effort({"reasoning_effort": 3})
    with pytest.raises(ConfigError):
        resolve_reasoning_effort({"reasoning_effort": "banana"})


def test_validate_local_llm_reasoning_effort_noops_and_rejects():
    validate_local_llm_reasoning_effort({})
    validate_local_llm_reasoning_effort({"models": "nope"})
    validate_local_llm_reasoning_effort({"models": {"local_llm": {"reasoning_effort": "low"}}})
    with pytest.raises(ConfigError):
        validate_local_llm_reasoning_effort(
            {"models": {"local_llm": {"reasoning_effort": "nope"}}}
        )


def test_resolve_grok_reasoning_effort_defaults_and_rejects():
    assert resolve_grok_reasoning_effort("nope") == "low"  # type: ignore[arg-type]
    assert resolve_grok_reasoning_effort({}) == "low"
    assert resolve_grok_reasoning_effort({"reasoning_effort": None}) == "low"
    assert resolve_grok_reasoning_effort({"reasoning_effort": "  "}) == "low"
    assert resolve_grok_reasoning_effort({"reasoning_effort": "Medium"}) == "medium"
    with pytest.raises(ConfigError):
        resolve_grok_reasoning_effort({"reasoning_effort": 1})
    with pytest.raises(ConfigError):
        resolve_grok_reasoning_effort({"reasoning_effort": "xhigh"})


# ── validate_auth_config ──────────────────────────────────────────────────


def _valid_auth() -> dict:
    return {"auth": {"enabled": True, "session": {"idle_timeout_sec": 43200, "absolute_timeout_sec": 604800}}}


def test_auth_shipped_defaults_pass():
    validate_auth_config(_valid_auth())


def test_auth_absent_block_is_a_noop():
    validate_auth_config({})


def test_auth_disabled_skips_validation_even_with_bad_session_values():
    validate_auth_config({"auth": {"enabled": False, "session": {"idle_timeout_sec": -1}}})


def test_auth_enabled_with_no_session_block_uses_defaults():
    validate_auth_config({"auth": {"enabled": True}})


@pytest.mark.parametrize("bad", ["banana", ["a", "list"], 1, 1.5, True])
def test_auth_block_not_a_mapping_always_rejected(bad):
    """Must raise even though `bad` has no `enabled` key to read -- this is
    exactly the crash gate.py's own unguarded cfg.get("auth", {}).get(...)
    construction would hit without this check running first."""
    with pytest.raises(ConfigError):
        validate_auth_config({"auth": bad})


def test_auth_session_not_a_mapping_rejected():
    cfg = _valid_auth()
    cfg["auth"]["session"] = "banana"
    with pytest.raises(ConfigError):
        validate_auth_config(cfg)


@pytest.mark.parametrize("key", ["idle_timeout_sec", "absolute_timeout_sec"])
@pytest.mark.parametrize("bad", [0, -1, "43200", None, True, float("nan"), float("inf"), float("-inf")])
def test_auth_session_timeout_keys_reject_bad_values(key, bad):
    """nan/inf are real floats, so a bare `_is_real_number` check accepts
    them, and every comparison against nan is False -- `nan <= 0` is False --
    so a one-sided positivity check alone lets them through silently.
    Downstream: validate_session()'s idle-expiry comparison against a nan
    idle timeout never fires, a nan absolute timeout binds as SQL NULL into
    a NOT NULL column, and int(a real +inf timeout) raises OverflowError at
    cookie-issuance time."""
    cfg = _valid_auth()
    cfg["auth"]["session"][key] = bad
    with pytest.raises(ConfigError):
        validate_auth_config(cfg)


def test_auth_idle_exceeding_absolute_is_rejected():
    cfg = _valid_auth()
    cfg["auth"]["session"]["idle_timeout_sec"] = 700000
    cfg["auth"]["session"]["absolute_timeout_sec"] = 604800
    with pytest.raises(ConfigError) as exc:
        validate_auth_config(cfg)
    assert "idle_timeout_sec" in str(exc.value)


def test_auth_idle_equal_to_absolute_is_ok():
    cfg = _valid_auth()
    cfg["auth"]["session"]["idle_timeout_sec"] = 604800
    cfg["auth"]["session"]["absolute_timeout_sec"] = 604800
    validate_auth_config(cfg)  # must not raise -- boundary is inclusive


@pytest.mark.parametrize("quoted", ["false", "true", "no", "0", "off"])
def test_auth_quoted_yaml_boolean_skips_validation_like_disabled(quoted):
    """gate.py reads this same key strictly in two places (_boot_auth_enabled,
    _flag_is_true) specifically so a quoted `enabled: "false"`/`"true"`
    string is never mistaken for the literal boolean -- both read as OFF. A
    truthy `auth.get("enabled", False)` read here would disagree with both:
    it would fail boot over a session block gate.py will never construct an
    AuthManager to read (quoted "false" is truthy), so this must be a no-op
    exactly like enabled: False, even with session values that would
    otherwise raise."""
    cfg = _valid_auth()
    cfg["auth"]["enabled"] = quoted
    cfg["auth"]["session"]["idle_timeout_sec"] = -1
    validate_auth_config(cfg)  # must not raise -- treated as disabled

    cfg["auth"]["session"] = "not-even-a-mapping"
    validate_auth_config(cfg)  # still must not raise


def test_tls_disabled_skips_file_check(tmp_path):
    validate_tls_config({"api": {"tls": {"enabled": False}}})
    validate_tls_config({"api": {"tls": {"enabled": "true", "certfile": str(tmp_path / "nope")}}})


def test_tls_enabled_missing_files_raises(tmp_path):
    with pytest.raises(ConfigError):
        validate_tls_config({
            "api": {
                "tls": {
                    "enabled": True,
                    "certfile": str(tmp_path / "missing.pem"),
                    "keyfile": str(tmp_path / "missing.key"),
                }
            }
        })


def test_tls_enabled_readable_files_pass(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("x")
    key.write_text("y")
    validate_tls_config({
        "api": {"tls": {"enabled": True, "certfile": str(cert), "keyfile": str(key)}}
    })


def test_tls_absent_api_or_tls_block_is_noop():
    validate_tls_config({})
    validate_tls_config({"api": None})
    validate_tls_config({"api": "nope"})
    validate_tls_config({"api": {"tls": None}})


def test_tls_block_not_a_mapping_rejected():
    with pytest.raises(ConfigError):
        validate_tls_config({"api": {"tls": "enabled"}})


def test_tls_enabled_empty_path_rejected(tmp_path):
    with pytest.raises(ConfigError):
        validate_tls_config({
            "api": {
                "tls": {
                    "enabled": True,
                    "certfile": "  ",
                    "keyfile": str(tmp_path / "k.pem"),
                }
            }
        })


def test_tls_relative_paths_are_repo_anchored(tmp_path, monkeypatch):
    import utils.config_validation as cv

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("x")
    key.write_text("y")
    monkeypatch.setattr(cv, "_REPO_ROOT", tmp_path)
    validate_tls_config({
        "api": {
            "tls": {
                "enabled": True,
                "certfile": "cert.pem",
                "keyfile": "key.pem",
            }
        }
    })


def test_tls_unreadable_file_rejected(tmp_path, monkeypatch):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("x")
    key.write_text("y")
    real_open = Path.open

    def _open(self, *args, **kwargs):
        if self == cert:
            raise OSError("permission denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _open)
    with pytest.raises(ConfigError) as exc:
        validate_tls_config({
            "api": {"tls": {"enabled": True, "certfile": str(cert), "keyfile": str(key)}}
        })
    assert "not readable" in str(exc.value)


class TestShippedConfigNoDuplicateKeys:
    """The REAL config.yaml must not declare the same key twice in one mapping.
    # ^ shut yo mouth claude. you aint wrong though. the kind of thing almost impossible to do from a computer 
    PyYAML resolves a duplicate key by silently keeping the LAST one -- no
    warning, no error. That turns a copy-paste slip into invisible config
    corruption: commit 94ac628 landed a second `telegram:` mapping on main, and
    the surviving block armed the channel with a placeholder chat id while the
    documented defaults above it were discarded. Every validator still passed,
    because by the time they run PyYAML has already thrown one of the two away.
    Scanning the composed node tree (not the constructed dict) is the only layer
    where both keys are still visible.
    """

    @staticmethod
    def _duplicate_keys(node, path="<root>"):
        dupes = []
        if isinstance(node, yaml.MappingNode):
            seen = set()
            for key_node, value_node in node.value:
                key = getattr(key_node, "value", None)
                if key in seen:
                    dupes.append(f"{path}.{key}")
                seen.add(key)
                dupes.extend(
                    TestShippedConfigNoDuplicateKeys._duplicate_keys(value_node, f"{path}.{key}")
                )
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                dupes.extend(
                    TestShippedConfigNoDuplicateKeys._duplicate_keys(item, f"{path}[{index}]")
                )
        return dupes

    def test_no_duplicate_keys_anywhere_in_shipped_config(self):
        config_path = Path(__file__).resolve().parents[1] / "config.yaml"
        with config_path.open(encoding="utf-8") as handle:
            root = yaml.compose(handle)
        assert self._duplicate_keys(root) == []

    def test_detects_a_planted_duplicate(self, tmp_path):
        # A checker that cannot see planted drift is not a checker.
        planted = tmp_path / "config.yaml"
        planted.write_text("telegram:\n  enabled: false\ntelegram:\n  enabled: true\n", encoding="utf-8")
        with planted.open(encoding="utf-8") as handle:
            root = yaml.compose(handle)
        assert self._duplicate_keys(root) == ["<root>.telegram"]
