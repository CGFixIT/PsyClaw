---
name: cyclaw-command-check-soul
description: >-
  Codex-native CyClaw soul integrity workflow. Use when working in CGFixIT/CyClaw and the user asks to check soul.md, validate data/personality/soul.md, compare soul integrity hashes, detect soul drift, or confirm the soul file is present and readable.
---

# CyClaw Command Check Soul

Use this skill to verify `data/personality/soul.md` presence, readability, and
integrity metadata. Soul content is governance-sensitive; inspect metadata by
default and avoid modifying the file.

Run commands only when the user asks to perform the check. Never update a stored
baseline hash or mutate `soul.md` without an explicit human reason.

## Workflow

1. Read `AGENTS.md` and preserve the soul governance invariant.
2. Check whether `data/personality/soul.md` exists.
3. If missing, report governance/identity drift: `PersonalityManager` will
   default-initialize the documented soul at startup, but do not silently
   create custom soul content during a read-only check.
4. Compute the current SHA-256 and file size.
5. Inspect `utils/personality.py` and related tests for any stored baseline hash
   or expected integrity value.
6. Validate that the file is non-empty and valid UTF-8.
7. Compare the current hash to the baseline when one exists.

Portable commands:

```bash
test -f data/personality/soul.md && echo "EXISTS" || echo "MISSING"
python -c "import hashlib, pathlib; p=pathlib.Path('data/personality/soul.md'); b=p.read_bytes(); print(len(b)); print(hashlib.sha256(b).hexdigest())"
python -c "open('data/personality/soul.md', encoding='utf-8').read(); print('UTF-8 OK')"
rg -n "soul_hash|sha256|baseline|soul.*hash" utils tests
```

On Windows PowerShell, use the equivalent file and hash checks:

```powershell
Test-Path data/personality/soul.md
Get-FileHash data/personality/soul.md -Algorithm SHA256
```

## Report

Use this shape:

```text
Soul file: data/personality/soul.md
Status:    EXISTS / MISSING
Size:      <bytes>
SHA-256:   <hash>
Integrity: PASS / DRIFT DETECTED / NO BASELINE
```

If drift is detected, include current hash, baseline hash, and last-modified
timestamp. Ask the user whether to accept the drift or investigate it; do not
auto-correct.

## Guardrails

- Do not print the full soul file content unless explicitly requested.
- Do not modify `data/personality/soul.md` without an explicit human reason.
- Do not commit generated soul backups, local hashes, or runtime data.
