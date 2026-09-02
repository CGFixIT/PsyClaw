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
