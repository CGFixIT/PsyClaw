#!/usr/bin/env python3
"""Opt-in live Grok/Claude spend probe. Not collected by pytest (not test_*.py).

Fails closed unless CYCLAW_SPEND_LIVE=1. CI must never set that.

Usage:
  CYCLAW_SPEND_LIVE=1 python tests/spend_live_probe.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm.client import ClaudeClient, GrokClient  # noqa: E402 - path insert above
from utils import spend  # noqa: E402 - path insert above
from utils.errors import ClaudeServiceError, GrokServiceError  # noqa: E402 - path insert above

LIVE_ENV = "CYCLAW_SPEND_LIVE"
PROMPT = "Reply with the single word ok."
FORBIDDEN = frozenset({"query", "prompt", "content", "messages", "api_key", "authorization"})
_PROBE_MAX_TOKENS = 2048


def _client_cfg() -> dict:
    """Shipped models.* from config.yaml; cap tokens and disable retries."""
    path = ROOT / "config.yaml"
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise SystemExit("config.yaml is not a mapping")
    models = raw.get("models")
    if not isinstance(models, dict):
        raise SystemExit("config.yaml missing models")
    cfg: dict = {"models": {}}
    for name in ("grok", "claude"):
        block = models.get(name)
        if not isinstance(block, dict):
            raise SystemExit(f"config.yaml missing models.{name}")
        copied = dict(block)
        max_tokens = copied.get("max_tokens")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens > _PROBE_MAX_TOKENS:
            copied["max_tokens"] = _PROBE_MAX_TOKENS
        retry = dict(copied.get("retry") or {}) if isinstance(copied.get("retry"), dict) else {}
        retry["max_retries"] = 0
        copied["retry"] = retry
        cfg["models"][name] = copied
    return cfg


def _refuse_unpriced(compared: dict, provider: str) -> None:
    if compared.get("rate_unknown") or compared.get("table_usd") is None:
        raise SystemExit(f"{provider}: rate table cannot price this model (rate_unknown)")


def _read_new_line(ledger: Path, before: int) -> dict:
    lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) != before + 1:
        raise SystemExit(f"expected one new spend line, have {len(lines)} (was {before})")
    record = json.loads(lines[-1])
    if not isinstance(record, dict):
        raise SystemExit("spend line is not an object")
    bad = FORBIDDEN.intersection(record)
    if bad:
        raise SystemExit(f"forbidden keys on spend line: {sorted(bad)}")
    return record


def _probe_grok(ledger: Path) -> dict:
    before = len(ledger.read_text(encoding="utf-8").splitlines()) if ledger.exists() else 0
    client = GrokClient(cfg=_client_cfg())
    print(f"grok probe model={client.model}")
    try:
        if not client.is_available():
            raise SystemExit("GROK_API_KEY is not set")
        try:
            answer = client.generate(PROMPT)
        except GrokServiceError as exc:
            status = (exc.details or {}).get("status")
            raise SystemExit(f"Grok generate failed: {type(exc).__name__} status={status}") from None
    finally:
        client.close()
    if not isinstance(answer, str) or not answer.strip():
        raise SystemExit("Grok generate returned empty")
    record = _read_new_line(ledger, before)
    if record.get("provider") != "grok":
        raise SystemExit(f"unexpected provider {record.get('provider')!r}")
    ticks = record.get("vendor_cost_ticks")
    if not isinstance(ticks, int) or isinstance(ticks, bool) or ticks < 0:
        raise SystemExit("Grok spend line missing vendor_cost_ticks (API billed amount)")
    compared = spend.compare_vendor_cost(str(record.get("model") or ""), record)
    _refuse_unpriced(compared, "grok")
    print(
        "grok: model={model} input={inp} output={out} reasoning={reason} "
        "ticks={ticks} table_usd={table} vendor_usd={vendor} delta_usd={delta}".format(
            model=record.get("model"),
            inp=record.get("input_tokens"),
            out=record.get("output_tokens"),
            reason=record.get("reasoning_tokens"),
            ticks=ticks,
            table=compared["table_usd"],
            vendor=compared["vendor_usd"],
            delta=compared["delta_usd"],
        )
    )
    if spend.ticks_mismatch(compared["delta_usd"], compared["vendor_usd"]):
        raise SystemExit(
            f"Grok table vs ticks mismatch delta={compared['delta_usd']} vendor={compared['vendor_usd']}"
        )
    return record


def _probe_claude(ledger: Path) -> dict:
    before = len(ledger.read_text(encoding="utf-8").splitlines()) if ledger.exists() else 0
    client = ClaudeClient(cfg=_client_cfg())
    print(f"claude probe model={client.model}")
    try:
        if not client.is_available():
            raise SystemExit("ANTHROPIC_API_KEY is not set")
        try:
            answer = client.generate(PROMPT)
        except ClaudeServiceError as exc:
            status = (exc.details or {}).get("status")
            raise SystemExit(f"Claude generate failed: {type(exc).__name__} status={status}") from None
    finally:
        client.close()
    if not isinstance(answer, str) or not answer.strip():
        raise SystemExit("Claude generate returned empty")
    record = _read_new_line(ledger, before)
    if record.get("provider") != "claude":
        raise SystemExit(f"unexpected provider {record.get('provider')!r}")
    if record.get("input_tokens") is None and record.get("output_tokens") is None:
        raise SystemExit("Claude spend line missing token counts")
    compared = spend.compare_vendor_cost(str(record.get("model") or ""), record)
    _refuse_unpriced(compared, "claude")
    print(
        "claude: model={model} input={inp} output={out} cache_5m={c5} cache_1h={c1} "
        "table_usd={table} vendor_usd={vendor} (Claude usage has no dollar field)".format(
            model=record.get("model"),
            inp=record.get("input_tokens"),
            out=record.get("output_tokens"),
            c5=record.get("cache_creation_5m_tokens"),
            c1=record.get("cache_creation_1h_tokens"),
            table=compared["table_usd"],
            vendor=compared["vendor_usd"],
        )
    )
    return record


def main(argv: list[str] | None = None) -> int:
    del argv
    if os.environ.get(LIVE_ENV) != "1":
        print(f"refusing: set {LIVE_ENV}=1 to spend real Grok/Claude credits", file=sys.stderr)
        return 2

    tmp = tempfile.TemporaryDirectory(prefix="cyclaw-spend-live-")
    ledger = Path(tmp.name) / "spend.jsonl"
    spend._get_config = lambda config_path="config.yaml": {"logging": {"spend_file": str(ledger)}}  # noqa: ARG005

    grok_key = bool((os.environ.get("GROK_API_KEY") or "").strip())
    claude_key = bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())
    if not grok_key and not claude_key:
        print("refusing: neither GROK_API_KEY nor ANTHROPIC_API_KEY is set", file=sys.stderr)
        tmp.cleanup()
        return 2

    try:
        if grok_key:
            _probe_grok(ledger)
        else:
            print("skip grok: GROK_API_KEY unset")
        if claude_key:
            _probe_claude(ledger)
        else:
            print("skip claude: ANTHROPIC_API_KEY unset")
    finally:
        tmp.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
