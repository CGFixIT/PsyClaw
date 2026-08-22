# Spend Tracking — Online LLM Token Ledger

CyClaw answers from the local vault by default. When a human explicitly confirms
an online send, the call reaches Grok (xAI) or Claude (Anthropic) and costs real
money. `utils/spend.py` records what each of those calls consumed, so an
operator can answer "what did this month cost?" without trusting a vendor
dashboard or re-reading the audit trail.

The ledger's core rule: **tokens are the ground truth; dollars are derived at
read time.** The file on disk never stores a price. `metrics.py` applies a dated
rate table when it prints, which means correcting a stale rate re-prices the
entire history rather than leaving wrong numbers baked into old lines.

## What the ledger is not

The spend ledger is not the audit log. `logs/audit.jsonl` is CyClaw's security
evidence stream and stays authoritative for what happened on a request;
`logs/spend.jsonl` is a separate append-only file for what a request cost.
`utils/spend.py` deliberately does not route through `utils/logger.py`'s
`audit_log`, so metrics can reprice cost history without rewriting or polluting
the audit trail.

The spend ledger is also not a policy decision point. Nothing on the `/query`
path reads it to decide anything — it is written after a billed call completes
and is consumed only by the `cyclaw-metrics` CLI. `gate.py`, `graph.py`, and
`mcp_hybrid_server.py` never import the metrics or sequence-detection modules
for a routing decision.

## What is recorded

Each billed external call appends exactly one JSON line. The writer is
`record_external_usage()` in `utils/spend.py`, serialized by a module-level
threading lock so concurrent calls cannot interleave a partial line.

| Field | Meaning |
|---|---|
| `timestamp` | UTC ISO-8601, set at write time |
| `provider` | Lowercased provider tag (`grok`, `claude`); unparseable input becomes `unknown` |
| `model` | The concrete model tag that billed, e.g. `grok-4.5` |
| `input_tokens` / `output_tokens` | Prompt and visible completion counts |
| `cached_input_tokens` | Grok prompt tokens served from cache |
| `cache_creation_input_tokens` / `cache_read_input_tokens` | Claude cache write and read counts |
| `cache_creation_5m_tokens` / `cache_creation_1h_tokens` | Claude cache writes split by TTL, when the vendor reports the split |
| `reasoning_tokens` | Grok reasoning tokens, billed at the output rate |
| `vendor_cost_ticks` | xAI's own `cost_in_usd_ticks`, when the response carries it |
| `usage_missing` | `true` when the vendor returned a billed response CyClaw could not parse usage from |
| `source` | One of `query`, `agentic`, `eval`; anything else normalizes to `unknown` |
| `query_hash` | Optional. The same unsalted SHA-256 content hash the audit log uses, accepted only as 64 lowercase hex characters |
| `route_path` | Optional. Up to 16 graph hops, each matching `^[a-z][a-z0-9_]{0,63}$` |

### What is never recorded

`utils/spend.py` never persists query text, prompt text, message content,
response bodies, API keys, or authorization headers. The optional `query_hash`
is a content address, not a request identity, and it is the only field that can
be joined back to an audit record. The `route_path` and `query_hash` fields are
both validated against strict patterns before being written, so a malformed or
oversized value is dropped rather than stored.

## Where the ledger lives

The path comes from `config.yaml`, which is the single source of truth:

```yaml
logging:
  audit_file: "logs/audit.jsonl"
  spend_file: "logs/spend.jsonl"
```

Relative paths anchor to the repo root, not the current working directory, so
`cyclaw-metrics` reports the same file no matter where it is invoked. When
`logging.spend_file` is absent from a config, the ledger falls back to
`logs/spend.jsonl`, and `cyclaw-metrics` silently omits its Spend section rather
than inventing an empty one.

Ledger writes are best-effort by design. A write failure logs a WARNING and
returns; it never raises into the request path, because a full disk must not
turn a successful paid answer into a failed one.

## Who writes to the ledger

Two call sites record usage today, and they are distinguished on the ledger by
the `source` field rather than by file:

- **`llm/client.py`** — the `/query` online fallback path, recorded as
  `source: "query"`. This is the triple-gated Grok/Claude escalation that a
  human confirmed per request.
- **`agentic/deepagent_github/chat_client.py`** — the out-of-band cloud planner
  used by the agentic layer, recorded as `source: "agentic"`.

Keeping both on one file with a `source` tag means a monthly total is a single
pass over one file, while `utils/sequence_detect.py` can still restrict itself
to `source == "query"` rows so the two planes never mix in a forensic join.

A billed non-JSON response is still recorded. If a provider returns 2xx with a
body CyClaw cannot parse, the line is written with `usage_missing: true` rather
than skipped — quota was consumed either way, and a silently missing line would
understate the bill.

## How dollars are derived

Pricing happens only at read time, in `estimate_usd()`. The resolution order is:

1. **Vendor ticks.** When a row carries `vendor_cost_ticks`, that wins. xAI
   reports cost in ticks at 10,000,000,000 ticks per USD, so this is the
   vendor's own arithmetic rather than CyClaw's approximation. Such a row is
   reported with `usd_source: "vendor_ticks"`.
2. **The dated rate table.** Otherwise CyClaw prices from the hardcoded
   per-million-token rates in `utils/spend.py`, reported as
   `usd_source: "rate_table"`.
3. **No price.** An unknown model yields `usd: None` with `rate_unknown: true`.
   A row whose every token count is absent (as opposed to zero) yields
   `usd: None` with `usd_source: "incomplete"`.

Two provider-specific billing rules are implemented rather than approximated.
Grok's long-context band applies to **all** tokens in a request once the prompt
reaches its long-prompt threshold, not just the tokens past it. Claude's cache
writes are priced by TTL when the vendor reports the 5-minute and 1-hour split,
and an unsplit write is priced at the 5-minute rate.

Output billing also differs by provider, which is why `billed_output_tokens()`
exists: Grok reasoning tokens sit outside `completion_tokens` and must be added,
while Claude's `output_tokens` is already the inclusive billing total.

### Rate staleness

The rate table carries a `PRICED_AS_OF` date and is considered stale after 30
days. `rates_are_stale()` and `warn_if_priced_as_of_stale()` expose that check so
a long-running deployment surfaces "these dollar figures are from an old rate
card" instead of quietly reporting confident, wrong totals. The token counts
remain correct regardless — only the derived dollars go stale, which is the
entire reason dollars are not persisted.

To re-price after a vendor changes rates, update the `_RATES` table and
`PRICED_AS_OF` in `utils/spend.py`. Every historical line re-prices on the next
`cyclaw-metrics` run; no migration or backfill is needed.

## Reading the ledger

The Spend section is part of the standard metrics output:

```bash
python -m metrics
```

The same report is available as `cyclaw-metrics` once the project itself is
installed with `pip install -e .`, since the short name is a console script that
pip only writes when the project is installed.

The Spend section prints a `today` and a `last_7d` window. Each window reports
total input and output tokens, a derived USD figure, a per-provider row count,
and a per-source row count. When any row in the window carried vendor ticks, the
output also shows `table_usd`, `ticked_table_usd`, `vendor_usd`, and
`delta_usd` side by side, so a drift between CyClaw's rate table and the
vendor's own billing is visible rather than hidden behind a single number.

Two counters flag data-quality problems in the window: `usage_missing` counts
billed calls whose usage could not be parsed, and `rate_unknown` counts rows
whose model has no entry in the rate table.

### Verifying the rate table against the vendor

`compare_vendor_cost()` prices a row both ways — rate table versus vendor ticks
— and reports the delta. `ticks_mismatch()` decides when that delta is worth
acting on: sub-tick dust is ignored, a relative disagreement beyond 5% of a
nonzero vendor figure trips, and a large absolute delta trips regardless. This
is how a silently wrong rate entry gets caught instead of accumulating.

An opt-in live probe exercises the real vendor APIs end to end:

```bash
CYCLAW_SPEND_LIVE=1 python tests/spend_live_probe.py
```

That script is deliberately not named `test_*.py`, so pytest never collects it
and CI never spends money. It fails closed unless `CYCLAW_SPEND_LIVE=1` is set,
and it asserts that no forbidden field (query, prompt, content, messages,
api_key, authorization) reached the ledger.

## Joining spend to the audit trail

`utils/sequence_detect.py` joins `logs/audit.jsonl` to `logs/spend.jsonl` on the
shared `query_hash` to surface offline forensic patterns — for example, a
blocked injection attempt followed by an online escalation inside a 15-minute
window. Its output is printed as a Sequences section by `cyclaw-metrics`.

That detector is forensic and CLI-only. It restricts spend rows to
`source == "query"`, counting and dropping agentic rows so the two planes never
mix, and its findings carry only hashes, event names, timestamps, and
provider/model tags — never query text, IP addresses, soul content, or secrets.

The mixed-hash window rule assumes CyClaw's shipped threat model: a
single-operator, loopback-bound host where a sequence within the window is the
operator's own activity. The window is a correlation aid, not an actor
identifier.

## Related documentation

- `docs/online-llm/readme.md` — how the Grok and Claude fallbacks are gated and
  configured, including the triple gate and the per-query provider selection.
- `docs/THREAT_MODEL.md` — the security scope the ledger's redaction rules are
  written against.
- `CLAUDE.md` — the operating manual, including the audit-log privacy rules that
  the spend ledger deliberately parallels.
