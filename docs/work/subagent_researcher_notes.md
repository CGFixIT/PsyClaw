# External Agentic Tooling — Research Notes

> Subagent: Researcher. Surveys modern agentic coding tools and maps each to a
> *transferable* lesson for CyClaw. **Confidence labels are explicit.** Sources
> are secondary (blogs/changelogs/docs) gathered June 2026 and listed at the end;
> treat specific version/metric claims as indicative, not authoritative.

## How to read the confidence labels
- **High** — corroborated by first-party docs/changelogs.
- **Medium** — multiple secondary sources agree; first-party detail thin.
- **Low / unverified** — single/secondary source, or a claim CyClaw cannot adopt
  without violating an invariant.

---

## 1. Claude Code (Anthropic) — **High**

**Strengths (cited):** three composable extensibility layers — *hooks* (deterministic
lifecycle scripts that "cannot hallucinate"), *subagents* (isolated context
windows, Markdown+YAML in `.claude/agents/`, nestable ~5 levels), and *skills*
(invocable playbooks that load only when called). Native `gh`/git harness and
parallel execution.

**Transferable to CyClaw:**
- Subagent *context isolation* maps cleanly onto CyClaw's out-of-band model — keep
  heavy/exploratory work off the request path. (CyClaw already uses this session-side.)
- **Hooks as deterministic enforcement** is exactly CyClaw's "topology = policy"
  philosophy. Lesson: gate behavior with code, not prompts.
- Skills-as-files is what CyClaw's `.claude/skills/` already does; the gap is a
  *governed* registry (see SKILLS_REGISTRY_GOVERNANCE.md).

**Conflict:** Claude Code's autonomous multi-agent orchestration and tool
auto-invocation are antithetical to CyClaw's "no LLM decides routing / no
autonomous external writes" invariants. Adopt the *patterns* (isolation,
deterministic hooks), not the autonomy.

## 2. GitHub Copilot SDK / CLI (GA 2026) — **High**

**Strengths (cited):** SDK GA (changelog 2026-06-02) with custom tool
registration, MCP server connection, and a **hook system intercepting pre/post
tool use, session start, MCP tool calls, and permission requests**; CLI
extensions are full Node processes extending the agent harness.

**Transferable to CyClaw:**
- The **permission-request / preToolUse-deny hook** is the industry-standard shape
  of a human-in-the-loop gate — validates CyClaw's writer triple-gate design.
- MCP-as-integration-surface confirms keeping GitHub *reads* behind an explicit,
  allow-listed tool catalog (CyClaw does this in `agentic/gh_client._READ_OPS`).

**Conflict:** Copilot's agent autonomously invokes registered tools. CyClaw must
keep GitHub strictly out-of-band and read-first; **writes stay disabled/stubbed**.

**Decision it informs:** prefer the **`gh` CLI as a subprocess** (zero new runtime
deps) over an SDK/library dependency — keeps `pip-audit`/`osv` surface unchanged.

## 3. Hermes Agent (Nous Research) — **Medium**

**Claims (secondary):** open-source self-improving agent (launched Feb 2026); a
*closed learning loop* that writes its own reusable skills, persists a three-layer
memory, indexes skills under `~/.hermes/skills/`, and uses SQLite FTS5 +
LLM-summarized recall. Runs as a local CLI or messaging gateway.

**Transferable to CyClaw:**
- The **skill = extracted, reusable procedure persisted to the filesystem** model
  directly informs CyClaw's governed `SkillRegistry` (file-as-truth + versioning).
- SQLite-backed local memory matches CyClaw's offline-first `cyclaw_soul.db`.

**Conflict (critical):** Hermes *autonomously* writes and self-modifies skills with
no human gate. CyClaw **cannot** adopt the autonomous loop — it violates Soul
Governance. The transferable core is "persist what worked," but **every write must
pass propose → human reason → injection scan → atomic apply**. CyClaw's registry
is Hermes' idea with the autonomy removed.

## 4. OpenClaw + ClawHub — **Medium**

**Claims (secondary):** ClawHub is a public skill *registry* for OpenClaw
("npm for agent skills"): publish/version/search `SKILL.md` bundles, CLI-friendly
API, **moderation hooks** and vector search; thousands of community skills.

**Transferable to CyClaw:**
- The **registry shape** (named, versioned, searchable skills) is what CyClaw's
  `SkillRegistry` implements locally.
- **Moderation hooks** ≈ CyClaw's injection scan at the write boundary.

**Conflict:** A *public, networked* registry that installs third-party skills is a
supply-chain surface CyClaw rejects. CyClaw's registry is **local-only, governed,
no remote install** — borrow the catalog UX, not the open marketplace.

## 5. Rust-rewritten harnesses (e.g. "Agent Browser" / Rust CLIs) — **Low/unverified**

**Claims (secondary):** Rust-based agent tooling (e.g. a fast headless-browser
automation CLI distributed via ClawHub) is promoted for memory efficiency / native
parallelism / lower local resource use.

**Transferable to CyClaw:** The *principle* — push expensive/parallel work into a
fast, isolated external process rather than the Python request path — is sound and
already how CyClaw treats `rclone`/LM Studio/`gh` (external binaries). **No Rust
rewrite is warranted**; the lesson is "shell out to a hardened external tool," not
"port the harness."

---

## GitHub access mechanism — options weighed
| Option | New runtime dep | CI/SCA impact | Out-of-band | Verdict |
|---|---|---|---|---|
| `gh` CLI subprocess | none (external binary) | none | yes | **Chosen** |
| PyGithub behind flag | +1 dep | new pip-audit/osv surface | yes | deferred |
| Extend MCP server | none | — | **no** (couples to MCP surface) | rejected |

## Net recommendation
Adopt **patterns, not autonomy**: deterministic gates (Claude Code/Copilot hooks),
governed file-as-truth skills (Hermes/ClawHub minus the autonomy and the public
marketplace), and out-of-band external processes (Rust-harness principle, via `gh`).
Every borrowed idea is forced through CyClaw's 5 invariants — see the master plan.

---

## Sources
- [Claude Code: Hooks, Subagents & Skills (ofox.ai)](https://ofox.ai/blog/claude-code-hooks-subagents-skills-complete-guide-2026/)
- [Create custom subagents — Claude Code Docs](https://code.claude.com/docs/en/sub-agents)
- [Steering Claude Code (claude.com blog)](https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more)
- [Copilot SDK is now generally available — GitHub Changelog](https://github.blog/changelog/2026-06-02-copilot-sdk-is-now-generally-available/)
- [Enhancing GitHub Copilot agent mode with MCP — GitHub Docs](https://docs.github.com/en/copilot/tutorials/enhance-agent-mode-with-mcp)
- [GitHub Copilot CLI is now generally available — GitHub Changelog](https://github.blog/changelog/2026-02-25-github-copilot-cli-is-now-generally-available/)
- [Hermes Agent Documentation (Nous Research)](https://hermes-agent.nousresearch.com/docs/)
- [Hermes Agent v0.14: Self-Improving AI with Skills & Memory (ofox.ai)](https://ofox.ai/blog/hermes-agent-self-improving-ai-complete/)
- [openclaw/clawhub — Skill + Plugin Registry (GitHub)](https://github.com/openclaw/clawhub)
- [ClawHub — OpenClaw Docs](https://docs.openclaw.ai/clawhub)
