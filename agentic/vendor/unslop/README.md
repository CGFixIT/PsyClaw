# Vendored unslop scanners

Upstream: https://github.com/theclaymethod/unslop
Commit: d81f5196167ded24f46fced04958c0c12d681798
Vendor date: 2026-08-21
License: MIT (see LICENSE)

## Files vendored

- `banned_phrase_scan.py` — regex/heuristic phrase scanner
- `structure_scan.py` — structural-pattern scanner
- `suggest.py` — combined scanner wrapper
- `_lang.py` — shared English-detection / prose-tokenization helpers
- `readability_metrics.py` — sentence splitter used by `structure_scan.py`

## Files deliberately excluded

- `check_suggestions.py`, `validate_preservation.py` — only needed for auto-applied rewrites, which this integration does not perform.
- `wiki_sync.py` — makes network calls to Wikipedia, incompatible with CyClaw's offline-first posture.
- `voice_profile.py`, `voice_card.py`, `voice_score.py`, `calibrate_pairs.py`, `harvest_samples.py` — stylometric voice-mimicry feature set, unrelated to slop detection.

## Import rewrite

Upstream scripts use flat same-directory imports. They have been rewritten to relative imports so the package is importable as `agentic.vendor.unslop` without `sys.path` mutation.
