# `telegram/` — out-of-band notify/chat channel

Optional Telegram Bot API adapter, shipped `enabled: false`. Runs strictly as
a separate process (`python -m telegram.cli`); `gate.py`, `graph.py`, and
`mcp_hybrid_server.py` never import it (invariant I6). Inbound chat text only
ever becomes an answer through HTTP `POST /query` on loopback — never a
direct call into `graph.py` — so every core gate (sanitizer, rate limit,
graph topology, audit) applies to Telegram traffic unchanged.

Architecture, phase gates (T1–T4), and threat-model obligations live in
[`docs/channels/TELEGRAM_DESIGN.md`](../docs/channels/TELEGRAM_DESIGN.md) —
that document is the authority; this file is the in-tree map.

## Modules

| Module | Role |
|---|---|
| `cli.py` | Entry point: `status` / `test` / `send` (T1) / `poll` (T2) plus the launchd generators `poll-plist` / `health-plist` (Darwin-only; chain the Keychain wrapper so tokens never land in a plist). |
| `config.py` | Loads the `telegram:` block; `bot_token_env` names the env var holding the token — the token itself never appears in config. |
| `client.py` | Bot API HTTP client (outbound `sendMessage`, long-poll `getUpdates`); ignores ambient `HTTP(S)_PROXY`. |
| `runner.py` | `send_notify` (T1) and `poll_forever` (T2) orchestration, allowlist enforcement. |
| `ratelimit.py` | Channel-side op budget, separate from the gate's per-IP limiter. |
| `media.py` | T4 media staging (default off); writes only through the existing `agentic/fsconnect` write path. |
| `state.py` | Long-poll offset persistence. |
| `selftest.py` | Pre-flight checks behind `telegram test`. |

## Consent boundaries that matter

- Only allowlisted `chat_id`s are ever answered (`allowed_chat_ids`).
- T3 hybrid-confirm (`allow_hybrid_confirm`, default off) is the **only** way
  chat text can set `user_confirmed_online`, and only via the exact
  `/online on <grok|claude>` command — the core triple gate (I3) remains the
  final authority.
- Exit codes match `sync`/`agentic`: `0` ok · `2` failed · `3` env/config.

## Related

- macOS token storage (Keychain wrapper): [`macos/README.md`](../macos/README.md)
- Threat-model amendment covering this channel: `docs/THREAT_MODEL.md`
