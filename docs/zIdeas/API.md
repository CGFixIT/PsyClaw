# Ponytail Review — `GrokClient` API-key normalization

**Scope:** `llm/client.py` — `GrokClient.__init__` env-var handling (commit `c4189b6`, "llm: strip GROK_API_KEY before use").
**Mode:** ponytail seven-rules audit.

---

## Rule 1 — YAGNI
**PASS.** All added lines serve callers that exist now: `strip()` is used immediately on `self.api_key`, and the version strings are literal config values with no branching around them.

## Rule 2 — stdlib-first
**PASS.** `os.environ.get` + `.strip()` — pure stdlib. No new deps introduced.

## Rule 3 — Minimal abstraction
**PASS.** No new helpers, classes, or base types added.

## Rule 4 — No dead code
**Borderline (now resolved).** The original two-line comment explained *what and why* for a `.strip()` call. The auth-header whitespace-leak consequence is a genuine gotcha, so a comment is justified — but two lines was noise. Condensed to a single line.

## Rule 5 — No speculative generality
**Resolved.** The outer `str()` wrapper was harmless dead weight: `os.environ.get(...)` with a `str` default (or with the `or ""` guard) never returns `None`, so `str()` could never do anything. Dropped.

## Rule 6 — Correctness over cleverness
**PASS.** The simplified expression is more direct, not less. Equivalence verified across `None`, empty, whitespace-only, padded, and tab/newline inputs — all identical to the original.

## Rule 7 — No half-measures
**PASS.** The strip lands on the field that flows into the `Authorization` header; no caller awareness required.

---

## Change applied

```diff
-        # Normalize the env var the same way the local client normalizes config:
-        # whitespace-only should count as missing, and padded values should not
-        # leak spaces into the Authorization header.
-        self.api_key = str(os.environ.get("GROK_API_KEY", "") or "").strip()
+        # Strip so whitespace-only counts as missing and padded values don't leak into the auth header.
+        self.api_key = (os.environ.get("GROK_API_KEY") or "").strip()
```

## Verification

| Input | Original | Simplified |
|---|---|---|
| `None` (unset) | `''` | `''` |
| `''` | `''` | `''` |
| `'   '` | `''` | `''` |
| `'key123'` | `'key123'` | `'key123'` |
| `'  key123  '` | `'key123'` | `'key123'` |
| `'\tkey\n'` | `'key'` | `'key'` |

All outputs identical — behavior-preserving.

---

## Overall verdict: PASS
Redundant `str()` wrapper dropped and the borderline two-line comment condensed to one. No behavioral change; no remaining violations.
