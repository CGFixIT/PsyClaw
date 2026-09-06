#!/usr/bin/env python3
"""Run the maintained doc-sync checker through the existing Codex entry point."""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    checker = Path(__file__).resolve().parents[3] / ".claude/skills/doc-sync/doc_sync.py"
    runpy.run_path(str(checker), run_name="__main__")
