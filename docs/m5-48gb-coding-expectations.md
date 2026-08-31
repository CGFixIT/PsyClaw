# M5 Pro 48 GB coding expectations (local cached 27B)

**Status (2026-08-27):** Operator doctrine for the CyClaw real-repo loop on the
machine this repo is developed on: **MacBook Pro, Apple M5 Pro, 48 GB unified
memory.** Local stack: Ollama `qwen3.8:27b-mlx`, `reasoning_effort: none`,
`num_ctx` 16384. This is not a hardware upgrade guide and not an invariant.

This box is **not** an M5 Max and **not** a base M5. Do not copy Max tok/s
benchmarks (70+ decode on 27B+DFlash, 128 GB residency) onto this SKU.

Canonical loop guide: [`docs/agentic/AGENTIC_README.md`](agentic/AGENTIC_README.md) §9–§10.
Package map: [`agentic/README.md`](../agentic/README.md).

Yes, 48 GB can do more than burst. **The loop is what keeps it on burst.**
A scratchpad helps the window, not the model. Used wrong, it just makes 27B
mistakes durable.

## Operator SKU

Recorded facts only. 14-inch vs 16-inch and the 15/16 vs 18/20 Pro bin are
not pinned in this repo — confirm on the machine with `sysctl -n machdep.cpu.brand_string`
and About This Mac if a later change depends on GPU core count.

| Field | This machine |
|---|---|
| Product | MacBook Pro (Apple silicon, 2026 M5 Pro / Max generation) |
| SoC | **Apple M5 Pro** |
| Unified memory | **48 GB** |
| Memory bandwidth | **307 GB/s** (M5 Pro, both GPU bins) |
| CPU / GPU bins possible | 15-core CPU + 16-core GPU, or 18-core CPU + 20-core GPU |
| Neural Engine | 16-core |
| Not this machine | Base **M5** (10/10, ~153 GB/s, max **32 GB**) |
| Not this machine | **M5 Max** (18/32 or 18/40, ~460 or ~614 GB/s, max **128 GB**) |
| Local coder | `qwen3.8:27b-mlx`, 4-bit, `reasoning_effort: none`, `num_ctx` 16384 |

48 GB on an M5 Pro is a mid-high Pro config (Pro ceiling is 64 GB). It is also
a valid Max *option* on the 40-core GPU SKU — that coincidence is why Max
review units get quoted as if they were this laptop. They are not.

## Which chip this is (M5 vs M5 Pro vs M5 Max)

"MacBook Pro M5 48 GB" in operator speech means **M5 Pro + 48 GB**. Base M5
cannot be configured with 48 GB. M5 Max can be configured with 48 GB on the
40-core GPU SKU, but that is a different SoC.

| Chip | Typical CPU / GPU | Memory bandwidth | Unified memory ceiling | 48 GB available? |
|---|---|---|---|---|
| M5 (base) | 10 / 10 | ~153 GB/s | 32 GB | No |
| **M5 Pro (this box)** | 15/16 or 18/20 | **307 GB/s** | 64 GB | **Yes — this config** |
| M5 Max | 18 / 32 or 40 | ~460 or ~614 GB/s | 128 GB | Yes, on 40-core GPU SKUs |

What that means for CyClaw:

- **Capacity** of the 27B job is the 48 GB and the 16k product caps, not the
  Max GPU. Weights ~18 GB + 16k KV ~1 GB fit with headroom on Pro 48 GB.
- **Decode speed** on Pro 48 GB / 307 GB/s is lower than published M5 Max +
  DFlash2 / MTP screenshots. Do not treat those as this machine's baseline.
- **Max 128 GB** is how you would resident a 70B / 8-bit second model. That is
  a different purchase. This doc does not assume it.
- Raising `num_ctx` on this Pro is still feasible in RAM; it still does not
  turn 27B into an architect.

## Hardware vs the loop you actually run

48 GB is enough RAM for 4-bit Qwen3.8-27B plus more context than the product
gives it. KV on this model is on the order of ~64 KiB/token. 16k context is
~1 GB of cache on top of ~18 GB weights. That is not a memory emergency on
M5 Pro 48 GB.

What you ship and run is the constraint:

| Knob | Shipped / recommended value | Why it exists |
|---|---|---|
| Ollama `num_ctx` | **16384** | Prompt + reserved `max_tokens` must fit or Ollama stalls at "0% processing" |
| RAG budget | `max_context_tokens` 8000 + `max_tokens` 4096 + ~1500 headroom = 13,596 | The `/query`-path floor inside the 16k window (raised from 4000 on 2026-08-28; `num_ctx` — not this budget — bounds KV memory, so the bigger budget costs prefill time, not a new memory ceiling) |
| Query deadlines | **720s** local LLM timeout; **780s** graph timeout | The inner timeout must fire first so a stalled Ollama request reports its LLM failure before the outer request deadline; the 60-second margin covers retrieval, routing, audit work, and cold embedding startup |
| Harness chat | **2048** completion tokens, **8** prior turns | A 4096-token chat budget on top of soul+skills+goal+history stalls this box |
| Agentic local prompt | **~8,000 chars** GitHub context (`_MAX_LOOP_CONTEXT_CHARS`) — but a worst-case real-repo-run iteration (plan + read_paths + feedback + instruction) can reach **~39–40k chars ≈ 10k tokens**; see OLLAMA_SETUP.md "The agentic real-repo coding loop needs more headroom" (it sizes `num_ctx` 24576 for that pathway) | Not 200k. `max_handoff_chars: 200000` is the **cloud egress** cap |
| Plan file | `_MAX_PLAN_CHARS` **6,000**; `planner_max_tokens` **3072** | A plan the size of a diff means the planner misbehaved |
| Local model | `qwen3.8:27b-mlx`, `reasoning_effort: none` | Thinking-on burns the 16k window before a patch exists |

The machine can physically hold 64k–128k context. The product is tuned to 16k
so Ollama does not sit at 0% processing. Raising `num_ctx` to 32k or 64k on
this Mac is feasible. It makes TTFT worse and still leaves you with a 27B.
That is "more than burst" on **volume**, not on **judgment**.

Project decision: local 27B is a **supervised executor**. Claude Code / Grok
stays the architect for anything that touches invariants. Burst pattern stays
one implementation file + one test, thinking off, I6 list off-limits
(`gate.py` / `graph.py` / `mcp_hybrid_server.py` / `agentic/` for the 27B
job itself / `soul.md` / `INVARIANTS.md` / `config.yaml`).

You can give Qwen a bigger slice of one file, a test, and a short plan. You
should not give it a whole implementation plan and hope 48 GB turns it into
Claude.

## What this cached model can do on this Mac

**In scope for `qwen3.8:27b-mlx` on the M5 Pro 48 GB box**

- Implement a human-approved `plan.md` that lists at most one implementation
  file and one test file.
- Whole-file replace of a file it was shown via `--read-file` (full content,
  not truncated).
- Follow a numbered "Do this" list that names existing functions/paths.
- Fix the last executor failure line when that line is in the prompt.
- Calibration class: harness-console / test-only bursts in the size of
  PRs #1086, #1132 (scoped to two files), #1148.

**Out of scope for one local job**

- Architecture, new subsystems, provider/runtime swaps (DFlash, oMLX bind,
  pointing `models.local_llm` at a second server).
- NeMo rails through `graph.py`, Numbat CEL, Unslopify as a product change,
  telemetry kill-switch (#1134 / #1128 / #1129 / #1149 class).
- Editing `gate.py`, `graph.py`, `mcp_hybrid_server.py`, `soul.md`,
  `INVARIANTS.md`, `config.yaml`.
- Inventing invariants or rewriting `protected_write_paths` "so the run passes."

The online planner (`real-repo-run-plan --provider grok --confirm-online`)
must emit a burst plan, not a design doc. The prompt contract for that lives
in `PLAN_SYSTEM_PROMPT` (`agentic/real_repo_loop.py`). `PLANNER_SYSTEM_PROMPT`
is the **coder** prompt — the name is a trap. Do not hand the 27B an
architect prompt and call it local autonomy.

Prompt-safety recap (enforced in code, not just this doc): Instruction-only;
untrusted GitHub fence + defuse; no `=== FILE ===` in the plan; `--instruction`
and `--plan-file` injection-scanned; audit hashes; `--confirm-online` for
cloud. See `docs/agentic/AGENTIC_README.md` §10.

**MCP is not the coding loop.** `real-repo-run` is CLI/subprocess. It does
not import or start `mcp_hybrid_server.py`. `agentic/harness_optimizer/mcp`
is a package name for sidecar tool wrappers, not that server. A 27B burst
must not plan MCP tool additions or a VPS/DeepAgent MCP handoff.

## Scratchpad before compact

Claude Code, Cursor, and Codex compact when the window fills. The ones that
do not lose the plot write state **out of the prompt first**.

| Kind | Lives where | Helps 27B? |
|---|---|---|
| In-prompt ReAct notes | Inside the 16k window | No. That is what fills the window. |
| On-disk working set written **before** compact | File the next turn can read a slice of | Yes for continuity. No for IQ. |

On-disk before compact is the only version worth building. Compaction without
a write is amnesia. Compaction after a write is paging.

**Persist**

- file path + function name + what it was doing
- last test command + exact failure line
- "do not touch X"
- the next single edit

**Do not persist as ground truth**

- the 27B's causal story about the bug
- invented invariants
- a plan that violates I6
- secrets, raw query text, `.env` contents

A frontier model's compact summary is lossy but usually directionally right.
A 27B summary of its own reasoning is often confidently wrong, and then every
later turn treats that file as ground truth. That is a zombie loop, not a
longer-horizon agent.

You already have the durable slots. Do not invent a fourth brain.

| Slot | Role |
|---|---|
| `plan.md` via `real-repo-run-plan` | Human-approved, injection-scanned, 6k cap, hashed onto the run |
| `docs/SESSION_NOTES.md` | Current state, files, errors, next step |
| `docs/memories/` + consolidation | Session-end / 12h pass — not a live coding scratchpad |
| `memory/` facts | Human propose/apply only. Auto-extract from LLM output is **not** implemented (memory-poisoning) |

The move: architect writes the scratchpad (or at least approves it). 27B reads
a budgeted slice and edits one file. That is already the CyClaw split. Wiring
Qwen to dump "insights" into memory facts or `soul.md` is what the memory
skill and I5 exist to stop.

## Operator rules (scratchpad / paging)

1. Scratchpad = run-scoped file next to the jailed clone or `docs/SESSION_NOTES.md`, not a new DB and not `docs/memories/`.
2. Schema, not a diary: `goal`, `constraints` (I6 list), `files`, `last_error`, `next_edit`. Cap it (~2k tokens/section, same spirit as session notes).
3. 27B may **append** errors and file paths. It does not get to rewrite constraints or invent facts.
4. Compact **after** the write. Next turn loads: system + I6 list + scratchpad slice + current file + current test. Not the whole transcript.
5. Do not enable raw-query persistence. Do not send the scratchpad to Grok/Claude unless the operator passed `--confirm-online` for that call. Default `send_local_context_to_grok/claude` is false.

## Data handling (cyclaw-advisor notes, not counsel)

A local scratchpad is fine for a single-operator loopback box if it is session
workspace, not a new personal-data or identity store. The moment it auto-writes
model prose into memory facts, `soul.md`, or `audit.jsonl` as raw text, you have
a new persistence surface and a poisoning path.

- Audit hashes queries; it does not store raw query text. A scratchpad that
  copies the prompt to disk reintroduces plaintext the audit log was designed
  to avoid.
- Soul writes need a human reason (I5).
- I3 still applies if that scratchpad later rides a cloud planner call.

Red flags:

- **Critical** — auto-apply 27B scratchpad text into memory facts or `soul.md`.
- **High** — scratchpad stores raw user queries, tokens, or `.env`; or leaves the box on a VPS/cloud handoff with no confirm.
- **Medium** — uncapped scratchpad stuffed back into the 16k window (recreates the stall `num_ctx` was tuned to avoid).
- **Medium** — putting live coding state under `docs/memories/` instead of session notes / the run workspace.

## Bottom line

M5 Pro 48 GB can run more than burst. You chose 16k and one-file jobs because
a 27B at long context is slow, stall-prone, and bad at architecture. That was
the correct product call on this SKU. An M5 Max would change decode speed and
the ceiling for a second resident model. It would not change the 27B's job.

A pre-compact scratchpad is worth doing as **paging**, identical in spirit to
`plan.md` + session notes. It does not mitigate "only a 27B." It mitigates
forgetting the test failure from two turns ago. If you want better plans, keep
planning on Claude/Grok (or a human), keep Qwen as the executor, and make the
scratchpad a short, typed, human-owned working set — not a place the 27B
journals its feelings before the window collapses.
