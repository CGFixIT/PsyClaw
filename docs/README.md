# `docs/` — documentation map

Pointers only — each linked document is the authority for its area
(`config.yaml` owns every number; when docs and code disagree, code wins).
This index exists so agents and humans can find the right document without
grepping.

## Start here

| Document | Owns |
|---|---|
| `THREAT_MODEL.md` | Security scope: single-operator, loopback-bound, single-tenant — plus every amendment (executor, telegram, armed external providers). Read before any security-touching change. |
| `../CLAUDE.md` / `../AGENTS.md` | Operating contract for agents (invariants, traps, commands). |
| `../INVARIANTS.md` (repo root) | Which guarantee is enforced by code vs. convention, and the test pinning each. |
| `changelog.txt` | Change history over time. |

## Subsystem deep-dives

| Document | Owns |
|---|---|
| `SYNC_README.md` | Dropbox corpus sync design + setup. |
| `channels/TELEGRAM_DESIGN.md` | Telegram channel architecture, T1–T4 phase gates. |
| `channels/OPENTWEET_DESIGN.md` | OpenTweet X channel: weekly OOB poster, draft-default, I6 isolation. |
| `agentic/AGENTIC_README.md`, `agentic/SKILLS_REGISTRY_GOVERNANCE.md`, `agentic/GITHUB_WRITE_ENABLEMENT.md` | Agentic layer governance (binding). |
| `HARNESS_POWERSHELL.md` / `HARNESS_MACOS.md` | Coding-harness OS walkthroughs (install glue). Slash-command usage (`/goal`, `/loop`, `/skills`, `/tools`, `/web`) lives in [`../harness/README.md`](../harness/README.md). |
| `AUTHENTICATION_DESIGN.md` | Per-user auth: all six stages landed — `utils/authn.py` (1), sessions + `/auth/*` (2), credential on `/query` when `auth.enabled` (3), TLS via `gate._serve` + `cyclaw-gen-cert` (4), the re-keyed bind guard (5), and RBAC roles `admin`/`operator`/`audit` with HTTP user admin (6). |
| `memory/` | Optional memory subsystem plan and README. |
| `spend/` | Online-LLM token ledger: what `logs/spend.jsonl` records, why dollars are derived at read time, and how `cyclaw-metrics` reports them. |
| `DOCKER.md`, `SECCOMP_EBPF_HARDENING.md`, `POSTGRES_BACKEND.md` | Deployment: containers, hardening (see also `../deploy/README.md`), Postgres backends. |
| `online-llm/`, `NeMo/`, `security-philosophy/` | Provider notes, guardrails background, telemetry-kill reference env, Numbat 0.2.0 secondary-evaluator note. |

## Working / historical trees

| Tree | Convention |
|---|---|
| `audits/` | Dated audit and report documents land here. |
| `memories/` | Live agent memory (the only sanctioned location — `.claude/memory/` is legacy). |
| `work/` | Active planning docs (e.g. `work/MACOS_LAUNCHD_INTEGRATION_PLAN.md`, session notes). |
| `plans/` | Living forward-looking roadmaps (e.g. `plans/NUMBAT_AND_ALWAYS_ON_ROADMAP.md`). Plans, not authorities — the code and `config.yaml` still win. |
| `analysis/`, `zIdeas/`, `zWork/`, `! How-To-Guides/`, `screenshots/` | Working material and archives; not authorities. |

Writing rules for anything added here: every `##` section self-contained
(the corpus is chunked section-by-section), numbers cited from
`config.yaml`/`pyproject.toml` rather than restated, dated reports to
`audits/`.
