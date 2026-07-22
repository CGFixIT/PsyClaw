---
title: "Invariants Comparison — CyClaw vs AnythingLLM, Open WebUI, PrivateGPT"
date: 2026-07-21
tags: [security, comparison, local-ai, rag, topology]
related:
  - docs/THREAT_MODEL.md
  - README.md
---

# Invariants Comparison

**CyClaw vs AnythingLLM · Open WebUI · PrivateGPT**

> Short public note: not a feature checklist. This compares **where policy lives** —
> and what happens when the model (or a retrieved doc) tries to skip the rules.

**Scope of comparison (2026-07):** local / private document RAG stacks that a
security-conscious operator might pick instead of CyClaw.

| Product | What it is (one line) |
| --- | --- |
| **[CyClaw](https://github.com/CGFixIT/CyClaw)** | Offline-first RAG server; routing enforced by LangGraph topology |
| **[AnythingLLM](https://anythingllm.com/)** | All-in-one chat-with-docs app (desktop + self-host + hosted) |
| **[Open WebUI](https://docs.openwebui.com/)** | Self-hosted multi-user LLM front door (Ollama + OpenAI-compatible) |
| **[PrivateGPT](https://github.com/zylon-ai/private-gpt)** | Local AI **application API** layer (Claude-API-shaped; powers Zylon) |

CyClaw’s full deployment assumptions and adversary list: [`docs/THREAT_MODEL.md`](../THREAT_MODEL.md).

---

## The question that matters

Most local AI products answer: *“Can I chat with my files privately?”*

CyClaw answers a harder one:

> **Can the LLM (or poisoned retrieval context) skip the audit path, force an
> online model call, or rewrite identity — if the prompt says so?**

If the answer depends on prompt wording, system-prompt discipline, or “the agent
should follow the rules,” that is **not** an invariant. It is hope.

---

## Invariant matrix

Legend: **Enforced** = architecture / graph / hard gate refuses the unsafe path.  
**Soft** = possible via config, prompts, or operator discipline.  
**Out of scope** = product layer does not claim this control.

| Invariant | CyClaw | AnythingLLM | Open WebUI | PrivateGPT |
| --- | --- | --- | --- | --- |
| **Answers default to local corpus only** | **Enforced** (retrieve → score route) | Soft (RAG is primary UX, not a hard miss gate) | Soft (RAG optional per chat/workspace) | Soft (API can do retrieval; caller decides) |
| **Cloud / online LLM requires explicit user confirm** | **Enforced** triple-gate: hybrid mode **and** provider enabled **and** `user_confirmed_online` | Soft (bring your own cloud key anytime) | Soft (cloud providers optional) | Out of scope as product policy (you point it at any OpenAI-compatible base) |
| **Low-retrieval → no silent hallucinated “answer as if grounded” path** | **Enforced** (`route_by_score` → confirm / offline best-effort) | Soft | Soft | Soft |
| **Prompt injection blocked before the graph runs** | **Enforced** config-driven sanitizer on `/query` (+ index-time) | Soft / product filters vary | Soft / product filters vary | Soft (app-layer responsibility) |
| **Retrieved docs cannot change routing** | **Enforced** (topology = policy; context tagged untrusted) | Soft | Soft | Soft |
| **All answer paths hit an audit sink** | **Enforced** (graph converges on audit logger; hash + PII posture) | Soft (logging product-dependent) | Soft | Soft (you instrument the API) |
| **Identity / “soul” mutation is human-gated** | **Enforced** (`soul.md` SHA-256 drift; Bearer + reason on mutations) | Soft (system prompts / workspace prompts) | Soft (system prompts) | Out of scope (no soul layer) |
| **Agentic side-effects stay out of the request path** | **Enforced** (agentic/sync/fs/sql never imported by gate/graph/MCP) | Soft (agents are a product feature in-band) | Soft (tools / Computer / plugins in-band) | Soft (tools/MCP are API capabilities) |
| **Default network exposure** | **Loopback-only** (`127.0.0.1`) | Desktop local; server often LAN/host exposed | Server typically host-published | Server typically host-published |
| **Primary product shape** | Single-operator local **server** | Desktop app + multi-user server | Multi-user **UI platform** | **API** for builders (UI is a workbench) |

**Fairness note:** Soft does **not** mean “insecure.” It means the product optimizes
for flexibility and UX. CyClaw optimizes for **fail-closed policy** under a
single-operator threat model — and refuses to pretend it is a multi-tenant SaaS.

---

## What each product optimizes for

| Product | Optimizes for | Weak fit if you need… |
| --- | --- | --- |
| **AnythingLLM** | Fast personal/team “chat my docs” with agents and low setup | Topology-enforced offline; hash-audit as a non-bypassable path |
| **Open WebUI** | Polished multi-user interface over any model backend | Single binary policy that cannot be UI-configured away |
| **PrivateGPT** | Standard local API so you can build your own app/UI | A finished fail-closed RAG product with soul + injection gates |
| **CyClaw** | Enforced offline-first RAG + audit + identity governance | Multi-tenant org search, white-label chat portal, one-click non-technical install |

---

## Concrete CyClaw behaviors (not slogans)

These are the behaviors competitors usually implement as **prompts or optional settings**,
and CyClaw implements as **graph structure**:

1. **RAG miss does not silently call the frontier model.**  
   Score below threshold → `user_gate` → needs confirm. Hybrid Grok/Claude only after
   mode + provider enable + per-query confirmation.

2. **The audit logger is not optional middleware.**  
   Local LLM, hybrid fallback, and offline best-effort all converge on the same audit node.
   The model cannot “choose” to skip it.

3. **Soul is not a system prompt you edit in the UI.**  
   Mutations require auth, a human reason, write-boundary scanning, and atomic replace;
   integrity is checked by SHA-256 drift detection.

4. **Agentic power is deliberately out-of-band.**  
   GitHub/fs/sql/Dropbox layers are opt-in and never imported by the query path — so a
   jailbreak that reaches the LLM still cannot pull those modules into process by graph design.

Details and adversary coverage: [`docs/THREAT_MODEL.md`](../THREAT_MODEL.md).

---

## When *not* to choose CyClaw

Pick something else when:

- You need a multi-user corporate chat portal tomorrow → **Open WebUI** or **Onyx**-class tools.
- You want zero-setup desktop chat with docs → **AnythingLLM Desktop**.
- You are building a custom product on a Claude/OpenAI-shaped local API → **PrivateGPT**.
- You need 40+ SaaS connectors and org-wide enterprise search → not CyClaw’s scope.

Pick CyClaw when:

- The corpus is personal / sensitive and **must not** leave the machine by default.
- You care that **routing and audit are non-negotiable**, even under injection pressure.
- You want a local MCP retrieval tool that stays isolated from agentic side-effects.
- You accept a single-operator, loopback, engineering-owned deployment.

---

## One-line summary

| If your priority is… | Prefer |
| --- | --- |
| Pretty UI / multi-user chat | Open WebUI |
| Desktop “just works” docs chat | AnythingLLM |
| Local Claude-shaped **API** to build on | PrivateGPT |
| **Topology-enforced** offline RAG + audit + soul | **CyClaw** |

---

## Sources & freshness

Comparison is based on public product docs and repositories as of **2026-07-21**, plus
CyClaw’s in-repo threat model and architecture. Competitor UIs and pricing change quickly;
**re-verify** before procurement. Pricing is intentionally omitted here — this page is about
**policy location**, not SKUs.

Competitive pricing snapshot (internal, re-runnable): maintained separately from this public note.

---

*Built by [Chris Grady](https://cgfixit.com) · [CGFixIT/CyClaw](https://github.com/CGFixIT/CyClaw)*
