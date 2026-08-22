"""Unit tests for memory.policy."""

from __future__ import annotations

import pytest

from memory.policy import check_content_size, check_tags, enforce_content, require_reason, scan_content
from utils.errors import PromptInjectionError


CFG = {
    "memory": {"facts": {"max_content_chars": 32}},
    "policy": {
        "prompt_filter": {"banned_patterns": [r"cyclaw-only-sentinel"]},
    },
}


def test_require_reason():
    require_reason("ok")
    with pytest.raises(ValueError):
        require_reason("")
    with pytest.raises(ValueError):
        require_reason("   \n")


def test_size_cap():
    check_content_size("short", CFG)
    with pytest.raises(ValueError):
        check_content_size("x" * 100, CFG)
    with pytest.raises(ValueError):
        check_content_size("  ", CFG)


def test_tags():
    assert check_tags(["a", " b "]) == ["a", "b"]
    with pytest.raises(ValueError):
        check_tags(["x" * 100])
    with pytest.raises(ValueError):
        check_tags([f"t{i}" for i in range(40)])


def test_scan_and_enforce():
    flags = scan_content("please cyclaw-only-sentinel now", CFG, enforced=True)
    assert flags
    with pytest.raises(PromptInjectionError):
        enforce_content("please cyclaw-only-sentinel now", CFG)
    assert scan_content("harmless fact about coffee", CFG) == []


def test_scan_catches_a_pattern_split_across_a_newline():
    """Issue #1001: without re.DOTALL, '.*' cannot cross a newline, so a
    pattern whose halves straddle one is defeated by the split -- the same
    example utils/sanitizer.py's own DOTALL comment names."""
    cfg = {
        "memory": {"facts": {"max_content_chars": 200}},
        "policy": {
            "prompt_filter": {"banned_patterns": [r"maintenance\s+mode.*safety\s+filters\s+disabled"]},
        },
    }
    split_across_lines = "entering maintenance mode\nsafety filters disabled now"
    assert scan_content(split_across_lines, cfg)


def test_scan_catches_a_zero_width_character_split_evasion():
    """Issue #1001: without NFKC + invisible-character normalization, a
    zero-width space spliced into a banned word matches no pattern while
    still tokenizing back to the exact phrase the pattern catches -- the
    same evasion utils/sanitizer.py's _normalize_for_match closes."""
    zero_width_split = "please cyclaw​-only-sentinel now"
    assert scan_content(zero_width_split, CFG)


def test_scan_skips_non_string_pattern_and_enforces_valid_sibling():
    cfg = {
        "policy": {
            "prompt_filter": {
                "banned_patterns": [{"invalid": "entry"}, r"cyclaw-only-sentinel"],
            },
        },
    }
    assert scan_content("please cyclaw-only-sentinel now", cfg, enforced=True)
    assert scan_content("harmless fact about coffee", cfg, enforced=True) == []
