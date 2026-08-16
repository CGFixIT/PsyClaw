# Security Hardening Review: CyClaw origin/main `4cb9130`

## Evidence Basis

I reviewed the clean isolated `origin/main` snapshot at
`4cb913088f1fa04fbaefea21ecb0dccbbd113e46` with the Standard Codex Security
workflow, the repository's `.github/SECURITY.md`, and `docs/THREAT_MODEL.md`.
The scan is source-derived; it did not execute Telegram, GitHub, database, or
external HTTP operations and it did not modify the source clone.

Two conditional findings survived source and attack-path validation. The
Telegram connector accepts an arbitrary HTTPS API host even though it embeds the
bot token into every request URL (`OPTIONAL-LAYERS-TELEGRAM-API-BASE-001`). The
real-repository loop receives a canonical landed path from the workspace writer
but records the planner's raw alias before verification and final staging
(`SB-008`). Both are default-off/explicitly enabled surfaces, but each weakens a
security boundary that should remain true whenever the feature is enabled.

The same review confirmed several documented local residuals rather than
promoting them to findings: self-asserted external confirmation, a
Content-Length-only body cap, DNS resolution separated from connection, a
negative memory-list limit, regex complexity, no-clobber check-then-replace,
post-buffer response caps, and all-whitespace index reset. Those are recorded in
`context.md` and the proposals' next decisions so the threat-model boundary is
not silently lost.

## Constraints

- The default deployment is a trusted single operator on loopback; optional
  Telegram, agentic, web, memory, and harness layers are disabled by default.
- No implementation was requested or authorized in this scan. The artifacts are
  design proposals, not evidence that either fix has landed.
- Keep core/out-of-band module isolation, strict external-provider gates, and
  human-gated Git-write controls unchanged.
- Source drift from the target revision invalidates these proposals and requires
  a fresh scan or explicit evidence refresh.

## Opportunity Portfolio

The first opportunity is a narrow endpoint-identity control. The second repairs a
governance data-flow mismatch: the writer knows where bytes landed, while the
loop still reasons about how the model spelled the path. Both can be addressed
incrementally without introducing a new service or broadening privileges.

| Opportunity | Evidence | Options | Recommendation | Proposal |
| --- | --- | --- | --- | --- |
| Telegram endpoint integrity | `OPTIONAL-LAYERS-TELEGRAM-API-BASE-001` — any HTTPS `api_base` receives token-bearing Bot API requests | Pin production endpoint; or add an explicit custom/self-hosted mode with separate credentials | Pin the production endpoint now; choose custom mode only if it is a real supported deployment | [Telegram endpoint integrity](proposals/telegram-endpoint-integrity.md) |
| Canonical protected-write paths | `SB-008` — `write_file` returns a resolved target, but `real_repo_loop` records the raw alias | Propagate the canonical result; or refuse symlinked write targets entirely | Propagate canonical results first; consider a no-symlink policy only for a stronger isolation model | [Canonical protected-write paths](proposals/canonical-protected-write-paths.md) |

## Recommendation Summary

I recommend the smallest safe choices: pin `api.telegram.org` for the
production connector and propagate the workspace writer's canonical target into
the existing protected-path, verification, diff, and staging gates. These choices
close the demonstrated source-to-sink paths while preserving current defaults
and avoiding a new network or authorization service.

The attractive part of the custom Telegram mode is legitimate self-hosted Bot
API support, but it introduces a second credential/endpoint lifecycle and should
not be smuggled in as a looser URL validator. The attractive part of rejecting
all symlink writes is stronger containment, but it would break repositories that
use benign symlinked generated content. Those alternatives should win only when
deployment requirements or the threat model justify their migration cost.

## Next Decisions

1. Select the Telegram endpoint policy. If custom Bot API deployments are not a
   supported product requirement, implement the pinned option and rotate any
   token that has ever been used with an untrusted base.
2. Select canonical-result propagation for the real-repo loop and add the
   symlink-alias integration regression before enabling agentic Git writes.
3. If CyClaw will serve an untrusted local/network caller, reopen the deferred
   external-confirmation and ASGI body-cap designs; they are not enforced
   against a hostile caller under the current trusted-operator model.
4. Add the whitespace-only corpus guard as a correctness fix before any
   automated reindexing or sync-to-index workflow is enabled.
