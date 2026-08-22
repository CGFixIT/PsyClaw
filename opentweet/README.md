# `opentweet/` — out-of-band X posting channel

Optional OpenTweet adapter, shipped `enabled: false`. Runs strictly as a
separate process (`python -m opentweet.cli`); `gate.py`, `graph.py`, and
`mcp_hybrid_server.py` never import it (invariant I6). Generation only
ever happens through HTTP `POST /query` on loopback with
`user_confirmed_online: false`. Default write is an OpenTweet **draft**;
`scheduled_date` is opt-in via `opentweet.schedule_enabled`. Schedulers
never send `publish_now`.

Architecture and threat notes: [`docs/channels/OPENTWEET_DESIGN.md`](../docs/channels/OPENTWEET_DESIGN.md).

## Modules

| Module | Role |
|---|---|
| `cli.py` | `status` / `test` / `post` / `schedule-plist` (Darwin) / `schedule-task` (Windows). Generators never load or register. |
| `config.py` | Loads the `opentweet:` block. The `ot_` key is named by `api_key_env`, never stored in YAML. |
| `client.py` | Loopback `/query` + OpenTweet REST. `trust_env=False`. |
| `runner.py` | Topic → query → validate → draft/schedule. |
| `selftest.py` | Pre-flight checks behind `opentweet test`. |

## Consent boundaries

- Topic comes from `--topic`, `--topic-file`, or `opentweet.topic_file`.
- Fail-closed on empty retrieval, `needs_confirm`, online `model_used`, oversize answer, missing key. The size limits are `opentweet.max_post_chars` (ships `280`) and `opentweet.max_topic_chars` (ships `500`).
- Logs/audit carry `hash_query(text)` + length + OpenTweet id — never the post body or Bearer token.
- Exit codes: `0` ok · `2` refused/HTTP · `3` env/config.

## Related

- Keychain wrapper: [`macos/README.md`](../macos/README.md)
- CredMan wrapper: [`powershell/README.md`](../powershell/README.md)
