# Online LLM Fallbacks

CyClaw is designed to answer from the local vault first. Grok and Claude are
optional online fallbacks for the rare case where the vault does not have enough
information and a human chooses to send the question outside the machine.

## What Changed

CyClaw now has two optional online choices after a vault miss:

- **Send to Grok** uses the xAI Grok API.
- **Send to Claude** uses the Anthropic Claude API.

Both providers ship **enabled** in `config.yaml`, and `app.mode` ships
`"hybrid"` (armed since 2026-08-07). The triple gate still applies per query:
a provider only runs when CyClaw is in hybrid mode, that provider is enabled
in `config.yaml`, the matching API key exists in the environment, and the
user explicitly confirms the online send — no online **fallback/generation**
call happens without that confirmation. (The one other online-touching path
is the opt-in `/health` provider probe, `api.health_probe_external_providers`,
which ships `false` — see "Quick Local Check" below.)

## API Keys

Use environment variables for API keys. Do not paste keys into the terminal UI,
commit them to git, or write them into `config.yaml`.

For Grok:

```powershell
$env:GROK_API_KEY = "your-grok-api-key"
```

For Claude:

```powershell
$env:ANTHROPIC_API_KEY = "your-claude-api-key"
```

If the key is missing, CyClaw stays local and reports that the online provider
is unavailable.

## Config Settings

Online fallback is controlled in `config.yaml`.

The main switch is:

```yaml
app:
  mode: "hybrid"  # SHIPPED DEFAULT — online fallback choices reachable; set "offline" to hard-disable them
```

Grok has its own provider switch:

```yaml
models:
  grok:
    enabled: true    # shipped default; set false to disable Grok regardless of mode
    base_url: "https://api.x.ai/v1"
    model: "grok-4.5"   # shipped default; pin grok-4.3 only if you prefer cost/window
```

Claude has its own provider switch:

```yaml
models:
  claude:
    enabled: true    # shipped default; set false to disable Claude regardless of mode
    base_url: "https://api.anthropic.com/v1"
    model: "claude-sonnet-5"
```

Model ids drift; re-check `config.yaml` and the vendor model catalogs before
enabling either provider in production.

Privacy defaults stay conservative:

```yaml
policy:
  fallback:
    require_user_confirm: true
    send_local_context_to_grok: false
    send_local_context_to_claude: false
```

That means CyClaw asks first, and by default it does not send local vault
context to either online provider.

## When to Use Online Fallback

Use online fallback only when all of these are true:

- The vault missed or gave a weak answer.
- The question is safe to send outside the local machine.
- The user understands that an external provider may receive the question.
- A valid API key is configured for the selected provider.
- The terminal asks for confirmation and the user chooses Grok or Claude.

Prefer offline best effort when the question includes private client material,
credentials, internal notes, sensitive business details, or anything that should
stay local.

## What the Buttons Mean

When CyClaw cannot confidently answer from the vault, the terminal offers:

- **No — Stay Offline** (labelled just **Stay Offline** when no online
  provider button is available): keep everything local and answer as well as
  possible.
- **Send to Grok**: send the question to Grok if hybrid mode and Grok are enabled.
- **Send to Claude**: send the question to Claude if hybrid mode and Claude are enabled.

Choosing an online provider is a one-query decision. It does not permanently
turn on online mode.

## Safety Rules That Still Apply

- Retrieval happens before any local or online model is used.
- Online providers are never used without explicit user confirmation.
- All paths still write to the audit log.
- Soul/personality changes still require a human reason and are not performed by
  online fallback.
- CyClaw still binds to `127.0.0.1` for local use.

## Quick Local Check

After changing settings, `/health` shows `mode` and local-backend health. It
does **not** report Grok/Claude status unless you opt in with
`api.health_probe_external_providers: true` (ships `false` — `/health` is
unauthenticated and unrate-limited, so probing there is operator-triggerable
third-party egress). With the probe enabled, a missing key surfaces as
`grok_api`/`claude_api` unavailable — never as the key value.
