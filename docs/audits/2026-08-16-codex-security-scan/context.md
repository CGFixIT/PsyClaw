# Security hardening context

This is derived design material for the Standard Codex Security scan of CyClaw
`origin/main` at revision `4cb913088f1fa04fbaefea21ecb0dccbbd113e46`.

- Scan ID: `9689563d-6515-484d-82d8-142a2d0825e5`
- Source root: `C:\Users\cgrady\Documents\CyClaw\_optimize_origin_main_20260814_fable_r2`
- Snapshot: clean isolated clone, `main...origin/main`
- Scan mode: Standard, source review only; no source files were modified
- Authoritative security policy: `.github/SECURITY.md`
- Threat-model companion: `docs/THREAT_MODEL.md`

## Threat-model boundary

The documented deployment is a single-operator, loopback-bound, single-tenant
RAG server. Host root and the operator are trusted; the optional Telegram,
agentic, web-search, memory, and harness layers are disabled by default. The
hardening decisions below still account for the two enabled-feature paths whose
security boundary is weakened when an operator deliberately enables them:

1. Telegram accepts any syntactically valid HTTPS API base and places the bot
   token in the URL path. A configuration-influencer path can therefore redirect
   the token and Bot API traffic to an attacker-controlled endpoint.
2. The real-repository loop receives a canonical landed path from
   `RepoWorkspaceTools.write_file`, but `run_real_repo_loop` ignores that return
   value and records the planner's spelling. An in-repository symlink can land a
   write on a protected test/CI/configuration file while the governance gate
   reviews the alias.

The gateway's caller-supplied `user_confirmed_online` flag, Content-Length-only
body cap, DNS validation/connection split, negative memory limit, regex
complexity, no-clobber check-then-replace, response buffering, and all-whitespace
index reset were independently corroborated as hardening candidates. They are
deferred from the primary finding set because the source-backed attack paths
require a hostile local caller, an operator-controlled allowlist/configuration,
or trusted corpus mutation outside the default threat model. They remain listed
in the portfolio's next decisions.

## Finding registry

| ID | Title | Disposition |
| --- | --- | --- |
| `OPTIONAL-LAYERS-TELEGRAM-API-BASE-001` | Telegram API base is not endpoint-pinned before token-bearing requests | Reportable, high confidence; conditional on enabling Telegram and config influence |
| `SB-008` | Canonical protected-write path is dropped before real-repo governance | Reportable, high confidence; conditional on enabling agentic Git writes |
| `GATEWAY-EXTERNAL-CONFIRMATION-001` | `user_confirmed_online` is a self-asserted approval field | Deferred hardening; accepted local trusted-operator residual |
| `GATEWAY-CHUNKED-BODY-001` | Chunked bodies bypass the Content-Length-only cap | Deferred hardening; accepted loopback availability residual |
| `RETRIEVAL-EMPTY-CORPUS-WIPE-001` | Whitespace-only corpus can reset indexes to empty | Deferred correctness/integrity hardening |

The proposals reference only repository-relative source paths and these stable
finding IDs. No implementation plan is included because no option has been
selected.
