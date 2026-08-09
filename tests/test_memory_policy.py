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
