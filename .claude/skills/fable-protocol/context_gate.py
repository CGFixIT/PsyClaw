#!/usr/bin/env python3
"""UserPromptSubmit gate: inject fable-protocol when Sonnet 5 is active and
the last turn's context usage passed the halfway mark.

Hooks get no direct signal for "current model" or "% context remaining" --
neither field is in the stdin payload. This reads the transcript's last
assistant entry (message.model, message.usage) as a best-effort proxy.
CONTEXT_WINDOW assumes the standard 200k window; hooks can't see whether the
1M-context beta is active for a given session, so this may fire earlier than
strictly necessary under that beta -- accepted, since erring toward loading
the reasoning-discipline layer is the safe direction.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CONTEXT_WINDOW = 200_000
TARGET_MODEL = "claude-sonnet-5"
SKILL_PATH = Path(__file__).parent / "SKILL.md"


def _last_assistant_turn(transcript_path: str) -> tuple[str | None, dict | None]:
    last_model: str | None = None
    last_usage: dict | None = None
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            msg = entry.get("message", {})
            if msg.get("model"):
                last_model = msg["model"]
                last_usage = msg.get("usage")
    return last_model, last_usage


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path or not SKILL_PATH.exists():
        return 0

    try:
        last_model, last_usage = _last_assistant_turn(transcript_path)
    except OSError:
        return 0

    if last_model != TARGET_MODEL or not last_usage:
        return 0

    used = (
        last_usage.get("input_tokens", 0)
        + last_usage.get("cache_read_input_tokens", 0)
        + last_usage.get("cache_creation_input_tokens", 0)
    )
    if used <= CONTEXT_WINDOW // 2:
        return 0

    context = SKILL_PATH.read_text(encoding="utf-8")
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": context,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
