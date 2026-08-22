# `llm/` — model clients

One module, `client.py`, holding the three HTTP clients the graph's answer
nodes use. No routing or policy lives here — which client gets called is
decided entirely by `graph.py`'s edges and `gate.py`'s construction gates
(invariants I2/I3); these classes only speak the wire protocols.

## Clients

| Class | Backend | Protocol |
|---|---|---|
| `LocalLLMClient` | Ollama (default) or LM Studio | OpenAI-compatible `/chat/completions` on loopback; ignores ambient `HTTP(S)_PROXY` (`trust_env=False`) so localhost traffic can't be redirected off-box |
| `GrokClient` | x.ai | OpenAI-compatible `/chat/completions`; ignores ambient `HTTP(S)_PROXY` (`trust_env=False`) so `GROK_API_KEY` cannot transit an operator proxy |
| `ClaudeClient` | Anthropic | Messages API; ignores ambient `HTTP(S)_PROXY` (`trust_env=False`) so `ANTHROPIC_API_KEY` cannot transit an operator proxy |

The two external clients are only ever **constructed** when
`mode == "hybrid"` and the provider's `enabled` flag is true, and only ever
**called** after per-request user confirmation — the triple gate (I3) is
enforced in `gate.py` + `graph.py`, not here.

Read that conditional against the shipped config before assuming it means
"off": `app.mode` ships `"hybrid"` and both `models.grok.enabled` and
`models.claude.enabled` ship `true` (armed 2026-08-07), so on a fresh clone
**both external clients are constructed at boot** and two of the three gates
are already open. The only remaining gate is the per-request
`user_confirmed_online`, which cannot be pre-set in config. Billed calls are
recorded to `logs/spend.jsonl` — see [`docs/spend/README.md`](../docs/spend/README.md).

## Shared behavior

- **Bounded retry** (`_post_with_retry`): timeouts, transport errors, 5xx and
  429 retry with exponential backoff; other 4xx fail fast (retrying a 400/401
  wastes time and, for Grok, credits). Config-driven via each model's `retry`
  block; absent block = single attempt.
- **Local failover** (`models.local_llm.fallback`): optional boot-time probe
  that prefers the primary backend (Ollama) and falls back to the secondary
  (LM Studio) if unreachable; selection cached per process. Ships disabled so
  single-backend installs stay fail-closed.
- Timeouts and model names come from `config.yaml` (`local_llm.*`, `grok.*`,
  `claude.*`) — see `CLAUDE.md` §2 "Load-bearing numbers" for the ones that
  interact (e.g. `api.graph_timeout_sec` must exceed `local_llm.timeout_sec`).

## Related

- Provider gating and per-query selection (`online_provider`): `graph.py`,
  repo-root `INVARIANTS.md`
- Ollama context-length footgun on "CyClaw hangs" reports:
  [`setup-guide.md`](../setup-guide.md#troubleshooting) (the `num_ctx`
  headroom formula lives in the same doc)
