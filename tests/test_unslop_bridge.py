"""Tests for agentic.unslop_bridge."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentic.unslop_bridge import build_unslop_probe

_REPO_ROOT = Path(__file__).resolve().parent.parent


class TestBuildUnslopProbeDisabled:
    def test_absent_block_returns_none(self):
        assert build_unslop_probe({}) is None

    def test_explicit_enabled_false_returns_none(self):
        assert build_unslop_probe({"unslop": {"enabled": False}}) is None

    def test_string_true_does_not_enable(self):
        assert build_unslop_probe({"unslop": {"enabled": "true"}}) is None

    def test_string_false_does_not_enable(self):
        assert build_unslop_probe({"unslop": {"enabled": "false"}}) is None

    def test_disabled_path_never_imports_vendor_package(self):
        script = (
            "import sys; "
            "from agentic.unslop_bridge import build_unslop_probe; "
            "build_unslop_probe({'unslop': {'enabled': False}}); "
            "leaked = [m for m in sys.modules if m.startswith('agentic.vendor.unslop')]; "
            "assert not leaked, f'disabled probe imported {leaked}'; "
            "print('OK')"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout


class TestBuildUnslopProbeEnabled:
    def test_enabled_returns_callable(self, tmp_path):
        probe = build_unslop_probe({"unslop": {"enabled": True, "metrics_path": str(tmp_path / "unslop.jsonl")}})
        assert callable(probe)

    def test_flags_banned_phrase_in_response_prose(self, tmp_path):
        metrics = tmp_path / "unslop.jsonl"
        probe = build_unslop_probe({"unslop": {"enabled": True, "metrics_path": str(metrics)}})
        result = probe("This is a treasure trove of ideas.", {}, 1)
        assert "nudge" in result
        assert "ai_vocabulary" in result["nudge"]
        lines = metrics.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["surface"] == "response_prose"
        assert record["path"] is None
        assert any(f.get("phrase") == "treasure trove" for f in record["findings"])
        assert all("phrase_sha256" not in f for f in record["findings"] if f.get("phrase") == "treasure trove")

    def test_structural_span_is_hashed_not_copied_into_jsonl(self, tmp_path):
        """Structural regexes copy operator prose into match.group(); JSONL
        must store a hash, not the span. Banned-phrase keys stay as phrase.
        """
        metrics = tmp_path / "unslop.jsonl"
        probe = build_unslop_probe({"unslop": {"enabled": True, "metrics_path": str(metrics)}})
        span = "the real problem is"
        result = probe("The real problem is that we shipped late.", {}, 1)
        assert "nudge" in result
        lines = metrics.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        structural = [f for f in record["findings"] if "phrase_sha256" in f]
        assert structural
        assert all("phrase" not in f for f in structural)
        assert all(isinstance(f.get("phrase_chars"), int) and f["phrase_chars"] > 0 for f in structural)
        blob = metrics.read_text(encoding="utf-8").casefold()
        assert span not in blob
        assert "that we shipped late" not in blob

    def test_no_nudge_for_clean_prose(self, tmp_path):
        metrics = tmp_path / "unslop.jsonl"
        probe = build_unslop_probe({"unslop": {"enabled": True, "metrics_path": str(metrics)}})
        result = probe("Please implement the requested change carefully.", {}, 1)
        assert result == {}

    def test_scans_markdown_proposed_file(self, tmp_path):
        metrics = tmp_path / "unslop.jsonl"
        probe = build_unslop_probe({"unslop": {"enabled": True, "metrics_path": str(metrics)}})
        result = probe("", {"docs/guide.md": "This is a treasure trove of details."}, 1)
        assert "nudge" in result
        lines = metrics.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["surface"] == "proposed_file"
        assert record["path"] == "docs/guide.md"

    def test_does_not_scan_python_bodies(self, tmp_path):
        metrics = tmp_path / "unslop.jsonl"
        probe = build_unslop_probe({"unslop": {"enabled": True, "metrics_path": str(metrics)}})
        result = probe("", {"main.py": "# This is a treasure trove of functions\ndef foo(): pass"}, 1)
        assert result == {}
        assert not metrics.exists()

    def test_non_english_is_skipped(self, tmp_path):
        metrics = tmp_path / "unslop.jsonl"
        probe = build_unslop_probe({"unslop": {"enabled": True, "metrics_path": str(metrics)}})
        # Long enough non-English text that fails the English function-word heuristic.
        text = (
            "Le chat noir dort sur le tapis près de la fenêtre ouverte "
            "sous le soleil d'été dans la maison tranquille."
        )
        result = probe(text, {}, 1)
        assert result == {}
        lines = metrics.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["skipped"] == "non_english"

    def test_log_never_contains_context_or_suggested_replacement(self, tmp_path):
        metrics = tmp_path / "unslop.jsonl"
        probe = build_unslop_probe({"unslop": {"enabled": True, "metrics_path": str(metrics)}})
        probe("This is a treasure trove of ideas.", {}, 1)
        text = metrics.read_text(encoding="utf-8")
        assert "context" not in text
        assert "suggested_replacement" not in text

    def test_exception_in_probe_returns_empty_dict(self, tmp_path, monkeypatch):
        metrics = tmp_path / "unslop.jsonl"

        def _boom(*args, **kwargs):
            raise RuntimeError("scanner exploded")

        monkeypatch.setattr("agentic.vendor.unslop.suggest.build_suggestions", _boom)
        probe = build_unslop_probe({"unslop": {"enabled": True, "metrics_path": str(metrics)}})
        assert probe is not None
        result = probe("Any text.", {}, 1)
        assert result == {}


class TestUnslopCoverageLeftovers:
    def test_logged_phrase_fields_empty_and_structural(self):
        from agentic.unslop_bridge import _logged_phrase_fields

        assert _logged_phrase_fields(None) == {}
        assert _logged_phrase_fields("") == {}
        # Structural span text is hashed, not echoed.
        out = _logged_phrase_fields("this is a long structural span not in banned phrases")
        assert "phrase_sha256" in out
        assert "phrase" not in out

    def test_extract_response_prose_strips_file_blocks(self):
        from agentic.unslop_bridge import _extract_response_prose

        text = "intro\n=== FILE a.md ===\nbody\n=== END FILE ===\noutro"
        prose = _extract_response_prose(text)
        assert "intro" in prose
        assert "outro" in prose
        assert "body" not in prose

    def test_append_record_failure_is_soft(self, tmp_path, monkeypatch):
        from agentic.unslop_bridge import _append_record

        path = tmp_path / "nested" / "unslop.jsonl"

        def _boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "open", _boom)
        _append_record(path, {"event": "unslop_scan"})  # must not raise

    def test_structure_flags_and_span_fallback(self, tmp_path):
        from agentic.unslop_bridge import _run_scan

        metrics = tmp_path / "unslop.jsonl"

        def _fake_suggest(_text: str):
            return {
                "suggestions": [
                    {"category": "banned", "severity": "hard", "span": "treasure trove"},
                ],
                "counts": {
                    "total": 1,
                    "hard": 1,
                    "soft": 0,
                    "by_category": {},
                    "structure_flags": ["list_heavy"],
                },
            }

        result = _run_scan(
            _fake_suggest,
            "treasure trove of ideas.",
            surface="response",
            path=None,
            step=1,
            metrics_path=metrics,
        )
        assert result["counts"]["structure_flags"] == ["list_heavy"]
        record = json.loads(metrics.read_text(encoding="utf-8").splitlines()[0])
        assert record["structure_flags"] == ["list_heavy"]
        assert record["findings"][0]["phrase"] == "treasure trove"

    def test_vendor_import_failure_returns_none(self, monkeypatch):
        import builtins
        import sys

        for key in [k for k in list(sys.modules) if k.startswith("agentic.vendor.unslop")]:
            monkeypatch.delitem(sys.modules, key, raising=False)
        real_import = builtins.__import__

        def _import(name, g=None, loc=None, fromlist=(), level=0):
            if name.startswith("agentic.vendor.unslop") or (
                name == "agentic.vendor" and fromlist and "unslop" in fromlist
            ):
                raise ImportError("no vendor")
            return real_import(name, g, loc, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", _import)
        assert build_unslop_probe({"unslop": {"enabled": True}}) is None
