"""Shared helpers for operator-facing self-test modules."""

from __future__ import annotations

SelfTestResult = tuple[bool, str]


def ok(name: str) -> SelfTestResult:
    return True, f"  [OK  ] {name}"


def fail(name: str, reason: str) -> SelfTestResult:
    return False, f"  [FAIL] {name}: {reason}"


def skip(name: str, reason: str) -> SelfTestResult:
    return True, f"  [SKIP] {name}: {reason}"


def finalize(results: list[SelfTestResult]) -> tuple[int, int, list[str]]:
    lines = [text for _, text in results]
    passed = sum(1 for passed, _ in results if passed)
    return passed, len(results), lines
