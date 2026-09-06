> **Status update — 2026-09-06 (docs review, Claude Code):** MOSTLY_COMPLETE.
> The `opentweet/` package exists in full (`__init__.py`, `cli.py`,
> `client.py`, `config.py`, `runner.py`, `selftest.py`, `README.md`), and
> `config.yaml:1146` carries the `opentweet:` block described here. I6
> isolation (never imported by the core six) matches the design. Live
> operator validation (a real posted draft against `opentweet.io`) was not
> re-verified this pass — this doc does not itself claim that was done.
>
> **What's left:**
> - Confirm live end-to-end posting was ever operator-validated (this doc
>   doesn't record a validation log the way `TELEGRAM_DESIGN.md` does); if
>   not, that's the one remaining gap before calling this COMPLETE.

# OpenTweet X channel (v1)

Out-of-band CyClaw channel. Telegram analog. Default-off.

## Goal

A weekly generated scheduler (macOS LaunchAgent + Windows Task Scheduler)
calls loopback `POST /query` with an operator topic, validates the answer,
and creates an OpenTweet **draft** (opt-in `scheduled_date`). Never a graph
node. Never X/Tweepy. Never hosted OpenTweet MCP (`mcp.opentweet.io`).

## Topology

```
weekly LaunchAgent / Windows task   (generate-don't-load, shipped off)
  → keychain-env / CredMan-env  OPENTWEET_API_KEY
  → keychain-env / CredMan-env  CYCLAW_API_KEY (optional)
  → python -m opentweet.cli post --topic-file <path>
       POST 127.0.0.1:8787/query  user_confirmed_online=false
       validate answer
       GET  https://opentweet.io/api/v1/me
       POST https://opentweet.io/api/v1/posts   draft | scheduled_date
       audit hashed event only
```

I6: `gate.py` / `gate_ops.py` / `gate_auth.py` / `gate_memory.py` /
`graph.py` / `mcp_hybrid_server.py` never import `opentweet`; `opentweet`
never imports them or sibling OOB packages.

## Operator surface

```bash
python -m opentweet.cli status
python -m opentweet.cli test
python -m opentweet.cli post --dry-run --topic "soul governance"
python -m opentweet.cli schedule-plist   # Darwin; does not bootstrap
python -m opentweet.cli schedule-task    # Windows; does not register
```

Keychain/CredMan service: `com.cgfixit.cyclaw.opentweet-api-key`.
Launchd label: `com.cgfixit.cyclaw.opentweet`.
Task name: `CyClaw opentweet`.
Topic file (documented default): `~/.CyClaw/opentweet-topic.txt`.

Shipped `config.yaml` has `opentweet.enabled: false`. Generators no-op
until enabled. `post` refuses (exit 3) while disabled.

## Fail-closed (no OpenTweet write)

Disabled channel; missing/empty/oversized topic; missing `OPENTWEET_API_KEY`;
`/query` error, `hit_count==0`, `needs_confirm`, online `model_used`; empty
or `[`-prefixed or >280-char answer; unauthenticated `/me`; scheduling
without subscription/`can_post`; past `scheduled_date`; `--dry-run`.

Audit events: `opentweet_query`, `opentweet_draft`, `opentweet_scheduled`,
`opentweet_dry_run`, `opentweet_refused`. Fields: hashes, lengths, ids,
mode. Never raw text, never Bearer tokens.

## Threat notes

This is a **public-write** egress — SECURITY.md class 3 (intentional, policy-gated feature traffic from a first-party httpx client; no vendor SDK, no SDK telemetry key; never labeled telemetry, never blocked by the kill map). The human gate is the OpenTweet
dashboard (draft default; opt-in `scheduled_date` still leaves a review
window before the slot). Corpus-derived text must not appear in launchd
or Task Scheduler logs. Loopback `/query` keeps I1–I5 on the generation
path. Do not set `user_confirmed_online`. Do not add this package to the
core import set.
