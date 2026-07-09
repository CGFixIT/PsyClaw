# Online LLM Fallbacks

CyClaw is designed to answer from the local vault first. Grok and Claude are
optional online fallbacks for the rare case where the vault does not have enough
information and a human chooses to send the question outside the machine.

## What Changed

CyClaw now has two optional online choices after a vault miss:

- **Send to Grok** uses the xAI Grok API.
- **Send to Claude** uses the Anthropic Claude API.

Both are off by default. They only run when CyClaw is in hybrid mode, the
specific provider is enabled in `config.yaml`, the matching API key exists in
the environment, and the user explicitly confirms the online send.

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
  mode: "offline"  # change to "hybrid" to allow online fallback choices
```

Grok has its own provider switch:

```yaml
models:
  grok:
    enabled: false
```

Claude has its own provider switch:

```yaml
models:
  claude:
    enabled: false
    base_url: "https://api.anthropic.com/v1"
    model: "claude-sonnet-5"
```

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

- **Offline Best Effort**: keep everything local and answer as well as possible.
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

After changing settings, use `/health` to confirm CyClaw can see the expected
provider configuration. A missing API key should show as unavailable, not as a
secret value.
