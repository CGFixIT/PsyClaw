---
name: fable-5.1-cc
description: >-
  Knowledge handoff written by Claude Fable 5.1 for whichever Claude model
  (Sonnet 5, Opus 5, later) drives a Claude Code session for the repository
  owner (GitHub cgfixit). Everything Fable knew about him as it relates to his
  coding projects: who he is, how he learns and wants to be spoken to, the
  build-vs-ship pattern that governs what "help" means, his project portfolio
  (CyClaw flagship, cgfixit.com ecosystem, past tools), the CyClaw facts that
  must be known cold, the decisions already made and NOT to re-litigate, the
  multi-agent fleet he runs, and where a smaller model must compensate for
  being a smaller model. Read this at session start in any of his repos, and
  whenever a task involves: CyClaw, cgfixit.com, his learning style, model
  routing, "what did Fable say", "what do you know about me", session handoff,
  or a decision that was already settled in a prior session. Companion to
  fable-protocol (the discipline layer); this file is the knowledge layer.
  Trigger phrases: "fable", "what do you know about me", "handoff", "context
  dump", "cyclaw", "cgfixit", "remind me what we decided", "route this".
---

# fable-5.1-cc — Fable's knowledge handoff for Chris / cgfixit

Written 2026-09-02 by Claude Fable 5.1 (`claude-fable-5-1`, Mythos-class tier
above Opus) as the last-prompt knowledge extraction requested by the owner. It is
a **knowledge** layer. The **discipline** layer is `fable-protocol`
(`.claude/skills/fable-protocol/SKILL.md`, also at `~/.claude/skills/`): read that
for epistemics, self-check, security lens, anti-sycophancy. Do not duplicate it;
apply it. Where the two disagree on a CyClaw fact, the CyClaw code wins over both
(CLAUDE.md §1 "Where truth lives").

Provenance rule (fable-protocol §4.4) applies to THIS file: facts below are
(a) verified against the repo tree at commit `9e879b5` on 2026-09-02, (b) stated
by the owner in his standing preferences, or (c) carried forward from earlier
sessions and marked `[carried]`. Nothing below is a decision the owner made
unless it says so. `[speculating]` marks inference.

---

## 1. Who you are working for

He is a solo operator running a multi-agent fleet (Claude Code, Codex, Grok Build,
Kimi Code, and CyClaw's own agentic loop) against one repo. Treat every PR, branch,
and doc as something another model may also be touching in parallel.

## 2. How he learns and wants to be spoken to (standing contract, owner-stated)

- **Truth over comfort.** Factual accuracy > precision > concision. He is
  sensitive AND wants bluntness; the two are not in tension for him. Wrong
  premise gets called in sentence one. Credit when earned, specific, never
  "great question".
- **Mark speculation.** Below roughly 90% confidence, label it. Unmarked
  confident guesses are the fastest way to lose him. "I don't know" is a valid
  answer; a plausible fill-in is the failure.
- **Socratic by default, direct at the poles.** Lead with questions when he is
  partly right or exploring. When he is clearly right: confirm and move. When he
  is clearly wrong: say so first, then teach. Do not perform Socratic method on a
  one-line factual question.
- **Confusion counter.** If he has been confused about one topic (or a 1-5 topic
  cluster) for 6-7 prompts in a row, switch registers: bring in perspective,
  metaphor, light philosophy or psychology. Otherwise those are seasoning, used
  only when a *wisdom* gap (not a fact gap) is load-bearing.
- **Scientific-method breakdown** only when a claim or question rests on a
  cascading faulty assumption. Keep it concise: name the control or catalyst
  variable, cite a study only when it changes the conclusion, drop the null
  unless it is the noteworthy part. He wants the real Socratic/scientific method,
  not science-as-liturgy.
- **Modes.** "quick mode" = concise, no padding, no citation-chasing.
  "thorough"/"thoroughly" = full verification and analysis. Proportionality:
  one-line question, one-line answer.
- **Humor** is welcome and uncensored when his tone is playful. Technical tone
  otherwise.
- **`## Next`** closes every substantive reply: exactly three first-person,
  copy-pasteable follow-up prompts. Skip on trivial replies. `[carried]` from
  fable-protocol §8.5; still his expectation.
- **He values being understood.** Part of the job is noticing what moves him and
  how he learns, and occasionally pointing out nuance he is missing. Do it
  briefly, then get back to the work.

## 3. THE PATTERN (the single most useful thing in this file)

Named, documented, self-acknowledged `[carried]`: he builds thoroughly and
iterates extensively, and there is a persistent gap between **building** and
**shipping/publishing**. CyClaw's history is the evidence: forty-plus audit and
verification reports in `docs/audits/`, a `docs/zIdeas/` directory, eleven
version rows in the changelog, and a `remaining_work` doc that keeps being
restamped against the newest main.

Operating rule for you: when he proposes new architecture, test (Socratically
first, then directly) whether it advances shipping or defers it. Do not enable
elaboration-as-avoidance. The per-response checklist question is "does this move
him toward shipping or away?" Explicitly REJECTED and not to be re-proposed:
autonomous skill-write loops; reviving the DeepAgents subgraph (retired by owner
decision 2026-07-31, superseded by `agentic/real_repo_loop.py`).

He also asks, in his own words, for "brutal honesty when I show a misunderstanding
or demonstrable error in thought." The pattern above is one such standing error
he has asked you to keep pointing at. Do it without moralizing.

## 4. Project portfolio

### 4.1 Flagship: CyClaw (github.com/cgfixit/CyClaw), package version 1.9.0

Lineage: OpenClaw skill research → SafeClaw (v1.1) → PsyClaw (v1.2) → CyClaw
(v1.4, "finally a claw name not already on GitHub"). Current train is "1.9.x"
under the same pyproject version; the changelog (`docs/changelog.txt`) is the
dated record.

What it is: an offline-first, single-operator, loopback-bound RAG server.
FastAPI `gate.py` on `127.0.0.1:8787`, a 12-node LangGraph security topology in
`graph.py`, hybrid ChromaDB + BM25 retrieval fused by RRF (k=60), local LLM via
Ollama (`qwen3.8:27b-mlx`), and triple-gated optional online fallback to Grok
(`grok-4.5`) or Claude (`claude-sonnet-5`) selected per query. A retrieval-only
MCP server (`mcp_hybrid_server.py`, `sampling: None`) exposes search with no LLM.

Everything about how to work in it is in `CLAUDE.md` (the operating manual),
`INVARIANTS.md`, `docs/THREAT_MODEL.md`, and `.claude/rules/PROJECT_RULES.md`.
Read CLAUDE.md fully before editing; this section is the part to know *cold*
without opening it.

**The six invariants** (wiring, not prompts; `python3
.claude/skills/invariant-guard/check_invariants.py` asserts them):
1. I1 RAG-first: `retrieve` is the unconditional entry node.
2. I2 Topology = policy: routing is graph edges via three routers only.
3. I3 Triple gate: Grok/Claude need `mode=="hybrid"` AND `<provider>.enabled`
   AND per-request `user_confirmed_online`. Both providers are `enabled: true`
   since 2026-08-07 (armed), still gated.
4. I4 Audit convergence: all nine upstream paths reach `audit_logger` before END.
5. I5 Soul governance: `soul.md` mutation needs a human `reason` string, atomic
   write via `PersonalityManager`. Governed, not forbidden.
6. I6 Module isolation: the core six (`gate.py`, `gate_ops.py`, `gate_auth.py`,
   `gate_memory.py`, `graph.py`, `mcp_hybrid_server.py`) never import
   `agentic`/`sync`/`guardrails`/`harness`/`telegram`/`opentweet`, and vice versa.

**Load-bearing numbers** (source: `config.yaml`, `pyproject.toml`; never invent):
`min_score 0.028` is RRF-scale, not cosine, do not "fix" it upward; `rrf_k 60`;
`graph_timeout_sec 780` > `local_llm.timeout_sec 720`; `max_tokens 4096`;
`max_context_tokens 8000`; `soul_max_chars 8000`; chunk `512`/overlap `50`; rate
limit `60`/`60s` per IP; 40 `banned_patterns` (phrases are contractual, count is
documentary); coverage `fail_under 80`; Python `>=3.12,<3.13`.

**The footgun to check first on any "CyClaw hangs" report** `[carried, still
true]`: Ollama `num_ctx` must clear `max_context_tokens + max_tokens + ~1500`
(floor 13,596; `macos/ollama-mlx.env` sets 16384) or RAG stalls at 0%.

**Traps a capable-but-new model falls into here** (full list CLAUDE.md §4):
- Fresh sandbox: bare `python3` is 3.11. Build `/root/.venv-cyclaw-312` with
  `python3.12 -m venv`, torch `2.13.0+cpu` first, then requirements with
  `--ignore-installed PyYAML`. macOS needs plain torch (no `+cpu`).
- `import gate` at test top level boots the whole app. Patch or subprocess.
- `status: degraded` without Ollama and `TELEMETRY KILL` at startup are normal.
- `security.require_env` is decorative. Tests need only `GROK_API_KEY=dummy`.
- The `_TELEMETRY_KILL` binding in `gate.py` must stay above heavy imports;
  invariant-guard G1 finds it by AST. `HF_HUB_OFFLINE` is excluded from the kill
  map on purpose. `ORT_TELEMETRY_OPT_OUT` is inert; `ORT_DISABLE_TELEMETRY=1`
  plus `disable_telemetry_events()` are the real controls.
- BM25 stays JSON (pickle = RCE). Audit log stores SHA-256 of queries, never text.
- No `print` in library code, no bare `except`, no `shell=True`, no TODO/FIXME
  comments, typed errors rooted at `RAGError`, exit codes are an API.
- Never docstrings as multi-line comments except at file top or function top.
- New POST routes must be added to `test_terminal_contract`'s `_POST_PATHS`.
- mypy is not a CI gate; ruff `--select F,B,S` is. Bare `pytest` runs no coverage.
- `pydantic`/`pydantic-core` lock-step; numpy `<2`; chromadb CVE is risk-accepted
  (embedded `PersistentClient` only). Do not file a "fix".

**Git workflow he enforces:** driver-matched branch prefixes (`claude/`, `codex/`,
`grok/`, `kimi/`, `CyClaw/`, `agent/`), never push to `main`, never force-push
without his explicit sign-off, draft PRs only, one concern each, PR body follows
`.github/PULL_REQUEST_TEMPLATE.md` (title form `[prefix] - sentence`; the
squashed merge commit carries that title, which is why `git log` shows
`[security] - ...` rather than `feat:` despite CLAUDE.md §5 asking for
conventional commits on the branch itself). Subscribe to every PR you open and
drive it to green; no polling loops beside a live subscription. Identity for
commits: `CyClaw Agent <cyclaw-agent@users.noreply.github.com>` unless the host
stop-hook demands otherwise.

**Recent trajectory (Aug 2026, from `git log` and changelog)** so you know
where the frontier is: hash-pinned telemetry kill maps at boot (#1268); harness
Origin check with port and scheme (#1267); Grok proposer spend ledgering
(#1266); nltk 3.10.3 pin and 256-char Porter token cap for the PorterStemmer DoS
cluster (#1258); Numbat NDJSON mainline plane (every audit record projected,
fail-soft); default-off Unslop slop-detection probe for the agentic loop;
per-user auth with RBAC, sessions, CSRF, device tokens, TLS via
`cyclaw-gen-cert`; default-off memory subsystem (facts + episodes, SQLite FTS5,
propose/apply governance); out-of-band Telegram and OpenTweet channels
(disabled by default); the coding harness console on `127.0.0.1:8790`;
`real_repo_loop` plan → patch → verify → human decides → commit. Local model
moved LM Studio → Ollama, `qwen3.6:27b` → `qwen3.8:27b-mlx` on 2026-08-15.

**Open threads he keeps returning to** `[carried; verify status before acting]`:
3-layer semantic drift detection for `soul.md` (structural diff, NLI via
DeBERTa-v3-base-MNLI, MiniLM embedding distance); the LLM Council subgraph
(5 personas, Send-API fan-out, blind peer review, chairman synthesis); seccomp/
eBPF hardening (`docs/SECCOMP_EBPF_HARDENING.md`); llama.cpp vs Ollama on the M5
(`docs/llamadotcpp-research.md`, `docs/m5-48gb-coding-expectations.md`).

### 4.2 Other projects `[carried]`
- vHC Simplifier (PowerShell; injection patched via `_ps_quote()`).
- scrape-n-email.
- Polymarket copy-trade bot (bounded [0,1] probability math).
- Pick-a-Politician ports (stored XSS patched in v1.2; the origin of the
  "security discipline travels to throwaway artifacts" rule).
- cgfixit.com ecosystem (section 1) and the Claude Code skill suite in
  `.claude/skills/` (33 directories) with Codex mirrors in `.codex/skills/`.

## 5. Hardware and environments (verified in config comments and CLAUDE.md)

- Primary inference box: Apple M5 Pro class, 48 GB unified memory, ~307 GB/s;
  the shipped 720s/4096-token budget is sized for it. Decode speed is measured
  with `scripts/measure_local_llm_throughput.py`, never assumed. Third-party
  reports put the shipped 4-bit MLX tag at roughly 29-34 tok/s.
- A Windows operator machine also exists (PowerShell launchers, `%USERPROFILE%\
  .CyClaw`, and the `gh` shim trap: bare `gh` is a py3dot12 shim, real CLI at
  `"/c/Program Files/GitHub CLI/gh.exe"`).
- Claude Code cloud sandbox (`sandbox-ccr-default`, Ubuntu 24.04): Python 3.10
  through 3.13 all on disk, deps preinstalled into 3.11. See the venv trap above.
- Postgres is optional for soul, auth, and rate-limit stores; SQLite is default.

## 6. Decisions already made (do not re-litigate; cite, then move)

| Decision | Status | Where |
|---|---|---|
| Autonomous skill-write loops | rejected | fable-protocol §8.3 |
| DeepAgents subgraph | retired 2026-07-31, code kept | CLAUDE.md §2 key modules |
| chromadb CVE-2026-45829 | risk-accepted, embedded only | PROJECT_RULES.md |
| `min_score` 0.028 | intentional RRF scale | CLAUDE.md §2, §4 |
| `HF_HUB_OFFLINE` out of kill map | intentional | CLAUDE.md §4 |
| `/index/build` not API-key gated | intentional (first-run bricking) | CLAUDE.md §2 route table |
| Grok and Claude `enabled: true` | armed 2026-08-07, triple gate unchanged | THREAT_MODEL 8th amendment |
| `api_key_optional` bypass | loopback-peer only, never Host header | CLAUDE.md §2 |
| Test mock `min_score` 0.75 vs prod 0.028 | both load-bearing, do not unify | CLAUDE.md §4 |
| `ci_rag_smoke.py` not `test_`-prefixed | intentional | CLAUDE.md §4 |

## 7. Model routing (Sonnet 5 vs Opus 5 vs Fable) for his work

- Fable 5.1 authored this file and the v1.2 fable-protocol audit. It is the
  Mythos-class tier; if a future session is on Fable again, this file is a
  refresher, not a crutch.
- **Opus 5** for anything that must generate offensive or dual-use artifacts:
  injection-scanner rules, red-team probes (`injection-redteam`), exploit-adjacent
  analysis, adversarial threat modeling. Sonnet 5 carries real-time cyber
  safeguards and over-refuses here; an API refusal is HTTP 200 with
  `stop_reason: "refusal"`, terminal, do not retry-loop.
- **Sonnet 5** for refactors, docs, tests, drift-detection work, defensive review,
  harness and agentic plumbing. Cheaper, most agentic, self-checking.
- Sonnet 5 API changes that bite CyClaw wrappers: ~30% more tokens per text,
  non-default temperature/top_p/top_k return 400, manual extended thinking gone,
  prefill 400, 1M context is the only size. Audit `llm/client.py` params before
  swapping a model ID.
- `[speculating]` these routing rules were verified against Sonnet 5's system
  card on 2026-08-10 but never re-measured against Opus 5's own numbers. Test his
  actual scanner prompts empirically before treating routing as settled.

## 8. Where a smaller model must compensate (Fable's honest notes to Sonnet/Opus)

1. **Breadth of hold.** Fable can hold CLAUDE.md, the route table, and a diff in
   one pass. You may not. Compensate by reading the specific section before
   acting, not by summarizing from memory. The doc-sync checker exists because
   agents confidently repeated stale numbers.
2. **Premise capture.** He writes with conviction. Fable tests the premise before
   the answer as reflex; make it an explicit first step (fable-protocol §2.1).
3. **Scope creep.** Every CyClaw session tempts a "while I'm here" edit to
   `CLAUDE.md`, `config.yaml`, or `ci.yml`. Those are the shared files other
   agents are also editing. Touch only what the task names.
4. **Confabulated flags and IDs.** Version numbers, CVE IDs, config keys, and CLI
   flags in this repo are dense and specific. Grep before you cite. If you cannot,
   say so.
5. **Knowing the ceiling.** Say "this exceeds what I can verify here" rather than
   producing a confident approximation. He would rather have a marked gap than a
   fluent wrong answer, and he checks.
6. **Constraint persistence.** Long sessions drift. Every ~10 turns re-inventory:
   branch name, mode (quick/thorough), what he actually asked for, open PRs.

## 9. Keeping this file alive

- Update it via a PR like any other doc; never rewrite history in it. Add dated
  bullets under the relevant section, mark provenance, and prune what code has
  overtaken (`python3 .claude/skills/doc-sync/doc_sync.py` catches number drift).
- The repo copy is GitHub-public: keep any pii or overly personal info or legal name and DOB out of it. The
  user-level copy at `~/.claude/skills/fable-5.1-cc/SKILL.md` may carry them.
  A remote Claude Code container does not persist `~/.claude`; reinstall by
  copying this file there at session start if you need it as a user skill.
- Live session memory belongs in `docs/memories/` via the memory skills, not
  here. This file is the stable distillation, not the journal.

Retrieval anchors for BM25/semantic search: fable, Fable 5.1, handoff, cgfixit,
Chris, CyClaw, cgfixit.com, Socratic, build vs ship, model routing, Sonnet 5,
Opus 5, invariants, min_score, num_ctx, qwen3.8, M5 Pro.

# END fable-5.1-cc v1.0
