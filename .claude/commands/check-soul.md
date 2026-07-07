---
description: Validate that soul.md exists at the required path and check its SHA-256 integrity against the latest stored version in the soul DB. Reports drift if detected.
---

Validate the CyClaw soul file at `data/personality/soul.md`.

## Steps

1. Check the file exists:
   ```bash
   test -f data/personality/soul.md && echo "EXISTS" || echo "MISSING"
   ```
   If missing: the server does NOT fail to start — `PersonalityManager._load_soul`
   (`utils/personality.py`) self-heals by writing a minimal default soul and
   recording it as a new version. Report MISSING so the operator knows the
   previous soul content is gone, but do not claim the server is down.

2. Compute the current SHA-256:
   ```bash
   sha256sum data/personality/soul.md
   ```

3. Read the stored baseline from the soul DB (there is no hardcoded hash
   constant in the code — the baseline is the newest `soul_versions` row):
   ```bash
   sqlite3 data/personality/cyclaw_soul.db \
     "SELECT id, sha256, reason, timestamp FROM soul_versions ORDER BY id DESC LIMIT 1"
   ```
   If the Postgres backend is active (`CYCLAW_DB_URL` or
   `personality.database_url` set), run the same SELECT against that DSN
   instead. If the DB or table doesn't exist yet, there is no baseline —
   the first server start will establish one.

4. Compare:
   - **Match** → Soul file is intact. Report the hash and file size.
   - **Mismatch** → Report drift detected. Show current hash vs baseline. Do NOT
     auto-correct — surface to user for decision. (On its next start the server
     also detects this and records a `DRIFT_RECOVERY` version automatically.)
   - **No baseline found** → Report that no stored version exists and recommend
     running the server once to establish a baseline.

5. Additionally check:
   ```bash
   # File is non-empty
   wc -c data/personality/soul.md
   # File is valid UTF-8
   python3 -c "open('data/personality/soul.md').read()" && echo "UTF-8 OK"
   ```

## Output

```
Soul file: data/personality/soul.md
Status:    EXISTS / MISSING (self-heals on next boot)
Size:      <bytes>
SHA-256:   <hash>
Baseline:  soul_versions row id <id> (<timestamp>)
Integrity: PASS / DRIFT DETECTED / NO BASELINE
```

If drift is detected, list the last-modified timestamp and ask the user whether
to accept the new hash as the baseline (a soul write requires an explicit human
`reason` — see the Soul Governance invariant).
