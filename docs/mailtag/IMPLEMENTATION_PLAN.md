# Mailtag — Provider-Neutral Email Tagging: Implementation Plan

## 0. Bottom line

| | |
|---|---|
| Status | **DRAFT — research complete, not yet approved, no code written** |
| Repo | `cgfixit/CyClaw` |
| Base branch | `main` @ `57e052ed9222d42a20b95ceb4dec71d21e169f57` (2026-08-27) |
| Feature branch | none yet — cut only after §14 approval |
| Requested by | repository owner, conversational request (not a GitHub issue) |
| FEATURE FREEZE | This is a **new capability**, not a fix. CLAUDE.md §1's operating stance is "does this polish the portfolio signal or fix a real defect?" — a new external-integration capability needs explicit owner sign-off before any code is written, independent of this document's technical soundness. |
| Authority | This document is the design authority for a *future* implementation. It is not itself code, and nothing in it has been applied to the repo. Every file:line citation below was verified against `main` @ the SHA above during a 9-agent research pass on 2026-08-27; **re-verify against current `main` before implementing**, since line numbers drift. |

**One-paragraph summary.** Add a new, fully out-of-band, default-disabled subsystem — package `mailtag/`, thin adapter `gate_mailtag.py` — that lets an operator connect a Gmail, Microsoft 365/Outlook, or iCloud Mail account and tag matching messages (e.g. "everything about package management" → `PKGS`) through a strict **read → plan → approve → apply** workflow. It is never a graph node, never an LLM-callable tool, and never reachable from a `/query` turn — confirmed necessary, not just stylistically preferred, by direct inspection of `graph.py`/`llm/client.py` (§1.2). OAuth token exchange happens entirely inside a CLI command, never inside `gate.py`'s request path, mirroring how `sync/` already delegates Dropbox's OAuth to `rclone` rather than holding the token itself. No new runtime dependency is required (§1.2, §8).

---

## 1. Verified baseline (what actually exists)

### 1.1 HEAD and tree

`main` @ `57e052ed9222d42a20b95ceb4dec71d21e169f57`. The most recently added optional subsystem is `memory/` (package) + `gate_memory.py` (adapter) — this is the precedent this plan mirrors most closely, confirmed by a dedicated research pass over `docs/memory/IMPLEMENTATION_PLAN.md`'s own section structure, `gate_memory.py`, and `tests/test_memory_isolation.py`.

Directly reusable existing infrastructure discovered during research, not assumed:

- `harness/env_keys.py` — allowlisted dotenv secret store (`MANAGED_KEYS`, mode `0600`, atomic `mkstemp`+`os.replace`).
- `macos/cyclaw-keychain-set.sh` + `macos/cyclaw-keychain-env.sh` — a genuine macOS Keychain generic-secret primitive (`security add-generic-password`/`find-generic-password`), parameterized by an arbitrary service name.
- `powershell/CyClaw-CredMan-Set.ps1` + `powershell/CyClaw-CredMan-Env.ps1` — a **direct Windows twin** of the macOS Keychain scripts (P/Invoke `CredWrite`/`CredRead`). This was not assumed; it was independently confirmed by `Read` during research, correcting the original feature request's premise that only macOS has OS-keychain integration.
- `utils/authn_store.py` — the per-subsystem-DB-URL-isolation pattern (`CYCLAW_AUTH_DB_URL`, never falling back to `CYCLAW_DB_URL`), with the rationale stated verbatim in its own module docstring.
- `agentic/registry.py`'s `propose_skill`/`apply_skill` — the closest single-shot (non-multi-iteration) propose/apply governance shape in the repo.
- `tests/conftest.py`'s `MockGrokClient`/`MockClaudeClient` — the `available=True/False` test-double contract to mirror for `MockGmailClient`/`MockGraphClient`/`MockImapClient`.

### 1.2 Gap confirmed (do not assume otherwise)

- **Zero OAuth 2.0 implementation anywhere in the repo.** A literal grep for `oauth`, `code_verifier`, `pkce`, `redirect_uri`, `authorization_code`, `refresh_token` across the whole tree returns nothing relevant. The only "OAuth" CyClaw's own process ever touches is Dropbox's — and it deliberately does **not** perform that flow itself: `sync/cli.py` shells out to the external `rclone` binary, which does its own browser OAuth dance, and the resulting refresh token lives only in `rclone.conf`, never read by CyClaw's Python process (`docs/SYNC_README.md:45,128,381-383`; `config.yaml:671`). This is the direct architectural precedent this plan follows for OAuth: **delegate the whole dance to a CLI-driven, gate.py-independent process, and never let `gate.py` see a refresh token.**
- **Zero email-provider code anywhere in the repo.** Confirmed by grep; the only email-adjacent hits are `utils/logger.py`'s PII-redaction regex and `utils/agent_identity.py`'s commit-email constant.
- **No LLM tool-calling exists anywhere in `graph.py` or `llm/client.py`.** Grepping both files for `bind_tools`, `tool_calls`, `StructuredTool`, `function_call`, a bare `tools=`, and a follow-up sweep for `ToolNode`, `create_react_agent`, `AgentExecutor`, `@tool`, `langchain_core.tools` returns **zero matches, repo-wide**. `local_llm_node` (`graph.py:494-558`) builds one prompt string and makes one `client.generate(prompt)` call; the response extractors in `llm/client.py` (`_extract_content`, `_extract_claude_content`, lines 68-136) parse plain text only — a Claude `tool_use` block would be silently dropped, not executed, because no code path inspects one. All four routers (`score_router`/`guardrail_router`/`user_gate_router`/`pre_action_hook_router`) branch only on state flags and config, never on parsed LLM output (I2, verified by direct inspection, not just cited from `invariant-guard`). **Conclusion, stated plainly: this is not a design choice this plan is free to reconsider — there is no mechanism in this codebase today by which a conversational turn could invoke an out-of-band action, so Mailtag is out-of-band by architectural necessity, not preference.**
- **`mcp_hybrid_server.py` cannot be a vehicle for this either** — it declares `sampling: None` at the protocol level and exposes exactly one tool (`hybrid_search`), with no LLM client in the file at all.
- **Naming collision caught during research, corrected here:** the original feature request's proposed package name `email/` would **shadow Python's own stdlib `email` module** for every file inside that package and for any importer that has the repo root on `sys.path` ahead of the stdlib (which is the normal case for a top-level repo package). `mailtag/imap_icloud.py` needing `import email.utils` or `email.message_from_bytes` for real MIME parsing would resolve to itself, not the standard library, the moment it lives inside a package literally named `email`. **This plan renames the capability `mailtag`** — package `mailtag/`, adapter `gate_mailtag.py`, config block `mailtag:`, doc directory `docs/mailtag/`, env var `CYCLAW_MAILTAG_DB_URL`. This also fits the repo's existing "`X`connect"/short-noun naming convention for connectors (`fsconnect`, `sqlconnect`) better than a generic `email`.
- **"PKGS" is not an existing CyClaw term.** The only repo match for the literal string is the unrelated Python tuple `OUT_OF_BAND_PKGS` in `.claude/skills/invariant-guard/check_invariants.py:35` (the I6 module list). "PKGS" is treated here purely as the example tag name from the original feature request, not a reserved keyword.
- **`config_validation.py`'s boot-time validator set was not checked** for whether a `validate_mailtag_config`-style function would be needed alongside `validate_auth_config`/`validate_personality_config` — flagged for the implementer, not resolved here (§13.4).

### 1.3 Live signatures (quoted from source — do not invent)

```python
# gate_memory.py — the adapter shape this plan's gate_mailtag.py copies verbatim
def register_memory_routes(app, cfg, audit, enforce_rate_limit, require_api_key) -> None: ...

# gate.py:1211-1219 — registration call site, AFTER register_auth_routes,
# BEFORE the _ALLOW_NON_LOOPBACK_ENV block
from gate_memory import register_memory_routes
...
register_memory_routes(app, cfg=cfg, audit=_audit,
                        enforce_rate_limit=_enforce_rate_limit,
                        require_api_key=require_api_key)

# utils/authn_store.py:33 — the per-subsystem DB-URL pattern to copy
_AUTH_DB_ENV = "CYCLAW_AUTH_DB_URL"   # deliberately NOT falling back to CYCLAW_DB_URL

# harness/env_keys.py — the KeySpec allowlist shape for new secret entries
@dataclass(frozen=True)
class KeySpec:
    name: str; label: str; detail: str; self_auth: bool = False

# tests/conftest.py:199 — the Mock*Client contract to mirror
class MockGrokClient:
    def __init__(self, response="...", available=True): ...
    def is_available(self) -> bool: return self._available
    def generate(self, prompt, **kwargs) -> str: ...
```

Verified scope/permission strings and identifiers from live vendor research (full citations in the Appendix):

```text
Gmail:      gmail.readonly | gmail.labels | gmail.modify   (RESTRICTED except gmail.labels: non-sensitive)
Microsoft:  Mail.Read | Mail.ReadWrite | MailboxSettings.Read | MailboxSettings.ReadWrite  (no admin consent, either account type)
iCloud:     no OAuth scope system — app-specific password is all-or-nothing
```

### 1.4 Prompt assumption corrections

Corrections to the original feature request, each grounded in the research above:

1. "Priority: Gmail, then Microsoft 365, then iCloud" is kept, but for a *stronger* reason than stated: iCloud has no OAuth scoping at all, so a read-only "plan" phase can only be enforced by Mailtag's own client-side discipline, never by the credential itself. It should ship last and carry an explicit extra warning.
2. `gmail.readonly` for discovery, elevating to `gmail.modify` for apply, is correct — but there is **no scope narrower than `gmail.modify` that can apply a label to a message**. `gmail.labels` exists and is non-sensitive, but it only covers CRUD on label *objects*; attaching a label to a message is `users.messages.modify`, which requires `gmail.modify` (or full `mail.google.com`). This plan therefore does label-object creation at **apply** time too (never during plan), so the plan phase makes zero provider writes on any account.
3. Outlook categories are the right analog to a Gmail label, but with a real structural difference no Gmail-side concept has: creating/renaming/deleting an entry in the *master category list* (`/me/outlook/masterCategories`) requires `MailboxSettings.ReadWrite`, a **separate** permission from `Mail.ReadWrite`, which only covers applying an *existing* category name to a message. A tool that wants to auto-create a `PKGS` category needs both.
4. iCloud's own IMAP tagging mechanism is materially riskier than the original proposal implied. Whether iCloud's server accepts arbitrary client-defined IMAP keywords (RFC 3501's optional `\*` PERMANENTFLAGS mechanism) is **unconfirmed by any source found**, and the closest comparable modern provider (Fastmail) explicitly rejected building its own labels feature on IMAP keywords, citing poor third-party client support, using real IMAP folders instead. This plan defaults iCloud tagging to **IMAP folder + COPY** (never keyword/flag) until a live probe against a real iCloud account confirms keyword persistence — see §5.3 and §10.
5. The original proposal's `email/` package name is renamed to `mailtag/` (§1.2).
6. "Store refresh tokens only in the OS credential store or a locally encrypted vault" is honored, but note CyClaw has **no existing local-encryption primitive** (no `cryptography` dependency, no `keyring` binding) — see §3.2 and §8 for the concrete, dependency-free design this plan uses instead.

---

## 2. Goals and non-goals

### 2.1 Goals

- Let an operator connect a Gmail, Microsoft 365/Outlook, or iCloud Mail account to CyClaw.
- Search/classify messages against operator-defined criteria (sender, subject, keyword rules; local-model assist for ambiguous cases).
- Produce a reviewable **plan** before any provider-side write.
- Apply an **approved, unexpired** plan by adding exactly one tag (Gmail label / Outlook category / iCloud folder) to each matched message — nothing else.
- Full local audit trail; every applied change is reversible via an explicit undo path.
- Ship `enabled: false`, fully disarmed, following the exact convention every other optional subsystem (`memory`, `telegram`, `opentweet`, `guardrails`) already uses.

### 2.2 Explicit non-goals

- **Never** send, compose, forward, or reply to mail.
- **Never** permanently delete a message (`users.messages.delete`/`batchDelete`, which needs the full `mail.google.com` scope this plan never requests).
- **Never** move a message out of its existing location as a side effect of tagging. Gmail/Outlook tags are additive by nature (multi-valued); iCloud tagging uses `COPY`, not `MOVE`, for the same reason.
- **Never** create a provider-side rule, filter, or forwarding address.
- **Never** change an account's own OAuth consent scopes from inside a running plan/apply cycle — scope elevation is a distinct, explicit CLI step (§4.5 pattern).
- **Never** expose search, plan, or apply as a tool the conversational LLM can invoke — there is no mechanism in this codebase for that today (§1.2), and this plan does not add one.
- Not a general-purpose email client. No inbox browsing UI beyond what the plan preview needs.

---

## 3. Architecture

### 3.1 Package layout

```
mailtag/
  __init__.py
  models.py            # Pydantic-free dataclasses: Account, MailRef, MailMetadata, TagPlan, PlanCandidate
  provider.py           # MailProvider Protocol (search/get_metadata/list_tags/ensure_tag/apply_tag/verify_still_valid)
  providers/
    gmail.py            # httpx-based Gmail REST client (labels.*, messages.modify/batchModify)
    graph.py             # httpx-based Microsoft Graph REST client (messages, masterCategories)
    imap_icloud.py       # imaplib/email(stdlib)-based IMAP client (folder-copy tagging)
  oauth.py              # PKCE + loopback-listener authorization-code flow (Gmail, Microsoft) — CLI-only
  token_store.py         # Per-platform refresh-token/app-password persistence (Keychain / CredMan / SQLite-600)
  store.py              # SQLite (Postgres via CYCLAW_MAILTAG_DB_URL) — accounts, plans, tag journal
  classifier.py          # Deterministic rules first; local LLM (llm.client.LocalLLMClient) for ambiguous cases only
  planner.py             # discover -> classify -> build TagPlan; NEVER calls a provider write method
  applier.py             # re-validate -> ensure_tag -> apply_tag -> audit; the only module allowed to write
  audit.py               # thin wrapper over utils.logger.audit_log + utils.numbat_emitter action-plane events
  cli.py                 # python -m mailtag.cli: connect/accounts/plan/apply/reject/undo
gate_mailtag.py           # thin FastAPI adapter, registered onto gate.py's app (see §4.2)
```

Import discipline (I6, extended — see §7):

- `mailtag/*.py` may import `utils.errors`, `utils.logger`, `utils.sanitizer`, `utils.numbat_emitter`, `llm.client` (as a plain library call — **never** via `graph.py`) — exactly the same allowances `agentic/` and `fsconnect/` already use.
- `mailtag/*.py` must **never** import `gate`, `graph`, `mcp_hybrid_server`, `gate_ops`, `gate_auth`, `gate_memory`, `agentic`, `sync`, `guardrails`, `harness`, `telegram`, `opentweet`.
- `gate_mailtag.py` may import `mailtag.*` **only lazily, inside request handlers**, mirroring `gate_memory.py` exactly.
- `gate.py` may import `gate_mailtag` (the adapter) but never `mailtag` (the package) directly.

### 3.2 Data model (SQLite default; Postgres via `CYCLAW_MAILTAG_DB_URL`)

```sql
-- mailtag_accounts: one row per connected mailbox
CREATE TABLE mailtag_accounts (
    account_id      TEXT PRIMARY KEY,      -- random hex, like agentic's run_id
    provider        TEXT NOT NULL,         -- 'gmail' | 'microsoft' | 'icloud'
    label           TEXT NOT NULL,         -- operator-chosen name, e.g. "personal"
    mailbox_address TEXT NOT NULL,
    scopes_granted  TEXT NOT NULL,         -- space-separated, as returned by the provider
    token_backend   TEXT NOT NULL,         -- 'macos_keychain' | 'windows_credman' | 'sqlite_600'
    encrypted_token TEXT,                  -- NULL when token_backend is an OS keychain (token lives there instead)
    connected_at    TEXT NOT NULL,         -- ISO 8601 UTC
    disabled        INTEGER NOT NULL DEFAULT 0
);

-- mailtag_plans: one row per propose_plan call (mirrors agentic/registry.py's single-shot shape,
-- NOT real_repo_run_store.py's multi-iteration JSON-on-disk shape -- tagging is single-shot)
CREATE TABLE mailtag_plans (
    plan_id             TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL REFERENCES mailtag_accounts(account_id),
    tag_name            TEXT NOT NULL,
    criteria_json       TEXT NOT NULL,     -- sender/subject/keyword rules as submitted
    candidate_ids_json  TEXT NOT NULL,     -- provider message IDs matched at plan time
    sample_preview_json TEXT NOT NULL,     -- small human-readable sample for the approval UI
    confidence_threshold REAL NOT NULL,
    reason              TEXT NOT NULL,     -- required, non-empty (I5-style human reason)
    status              TEXT NOT NULL,     -- 'pending' | 'approved' | 'applied' | 'rejected' | 'expired'
    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,     -- created_at + mailtag.plan_expiry_sec
    decided_at          TEXT,
    applied_at          TEXT
);

-- mailtag_tag_journal: append-only undo ledger, one row per message actually tagged
CREATE TABLE mailtag_tag_journal (
    entry_id     TEXT PRIMARY KEY,
    plan_id      TEXT NOT NULL REFERENCES mailtag_plans(plan_id),
    account_id   TEXT NOT NULL,
    message_id   TEXT NOT NULL,
    tag_applied  TEXT NOT NULL,
    prior_tags   TEXT NOT NULL,   -- provider-side label/category state before this change, for undo
    applied_at   TEXT NOT NULL,
    undone_at    TEXT
);
```

**Refresh-token / app-password storage — no new dependency (§8).** Three backends behind `token_store.py`, chosen per-account at connect time and recorded in `token_backend`:

1. **macOS** (default when available): shell out to a new, narrow wrapper mirroring `macos/cyclaw-keychain-set.sh`/`cyclaw-keychain-env.sh`, keyed by `account_id` as the Keychain service name. Reuses the existing, audited `security add-generic-password`/`find-generic-password` primitive.
2. **Windows** (default when available): shell out to the **already-existing** `powershell/CyClaw-CredMan-Set.ps1`/`CyClaw-CredMan-Env.ps1`, the same way.
3. **Everywhere else (Linux, or explicit opt-out)**: the `encrypted_token` column above holds the raw value, and the **file itself** is the protection boundary — SQLite file created mode `0600`, matching the exact risk posture CyClaw already accepts today for `CYCLAW_API_KEY`/`GROK_API_KEY`/`ANTHROPIC_API_KEY` in `harness/env_keys.py`'s dotenv. A refresh token is not a materially different secret class than those. Adding real at-rest encryption (e.g. the `cryptography` package) is a **High-tier decision requiring explicit sign-off** (new runtime dependency, CLAUDE.md §7) — flagged as an open decision in §13.4, not adopted by default here, per YAGNI: nothing in the existing threat model treats file-mode-600 as insufficient for a comparably sensitive secret.

Linux's freedesktop.org Secret Service (`libsecret`) requires a live D-Bus session bus and a running keyring daemon, commonly absent on a headless single-operator server — CyClaw's realistic deployment shape per `docs/THREAT_MODEL.md` — so it is correctly excluded as a default rather than half-supported.

### 3.3 Config block (`config.yaml`, shipped exactly as shown)

```yaml
# ===========================
# Mailtag subsystem (optional, default-off) [v0.1 foundation]
# ===========================
# Provider-neutral email tagging via a strict read -> plan -> approve -> apply
# workflow. Design doc: docs/mailtag/IMPLEMENTATION_PLAN.md. Never a graph node,
# never LLM-callable -- reached only via gate_mailtag.py's HTTP surface or
# `python -m mailtag.cli`. Every leaf below ships false/safe.
mailtag:
  enabled: false
  database_url: null            # postgresql://... ; falls back to CYCLAW_MAILTAG_DB_URL, then SQLite
  max_messages_per_plan: 200
  plan_expiry_sec: 900          # 15 min; apply is refused once a plan is older than this
  default_confidence_threshold: 0.85
  gmail:
    enabled: false
    client_id_env: EMAIL_GMAIL_CLIENT_ID
    client_secret_env: EMAIL_GMAIL_CLIENT_SECRET   # required when gmail.enabled is true
  microsoft:
    enabled: false
    client_id_env: EMAIL_MICROSOFT_CLIENT_ID       # public client; no secret needed (see Appendix)
  icloud:
    enabled: false               # ships last; see §1.4 and §10 for the extra warning this needs
```

New `harness/env_keys.py` `MANAGED_KEYS` entries (client credentials are per OAuth-app-registration, not per-account, so a flat `KeySpec` fits the existing shape exactly):

```python
KeySpec(name="EMAIL_GMAIL_CLIENT_ID", label="Gmail OAuth client ID",
        detail="Google Cloud Console 'Desktop app' OAuth client. Shared across every connected Gmail account."),
KeySpec(name="EMAIL_GMAIL_CLIENT_SECRET", label="Gmail OAuth client secret",
        detail="Google's Desktop-app flow still requires this in the token exchange even under PKCE; "
               "Google's own docs describe it as not confidential for an installed app."),
KeySpec(name="EMAIL_MICROSOFT_CLIENT_ID", label="Microsoft Graph OAuth client ID",
        detail="Entra public-client app registration. No client secret needed (PKCE, public client)."),
```

### 3.4 Trust model

```
CLI process (python -m mailtag.cli connect gmail --account personal)
    -> opens ephemeral 127.0.0.1:0 listener, opens browser, exchanges code for tokens
    -> writes account_id + tokens to mailtag's own store, via token_store.py
    -> gate.py's process is NEVER involved in this step (mirrors sync/'s rclone delegation)

gate.py (127.0.0.1:8787)
    -> gate_mailtag.py registered onto app, lazy-imports mailtag.* only inside handlers
    -> /mailtag/plan, /mailtag/plan/{id}/apply etc. -- API-key gated, rate-limited, audited
    -> NEVER touches OAuth; only reads/writes plan and account METADATA (never a raw token)

graph.py / mcp_hybrid_server.py
    -> unchanged. Zero awareness that mailtag exists. Confirmed necessary by §1.2, not a stylistic choice.
```

---

## 4. Insertion points (literal)

### 4.1 `config.yaml`

**Where:** insert the `mailtag:` block (§3.3) after the `memory:` block (currently ends line 221) and before `policy:` (currently starts line 223), matching how `memory:` itself was inserted after `personality:` and before `policy:`.
**Rules:** every leaf `false`/`null`/safe by default; `*_env` indirection for every secret name, never a literal secret in YAML.

### 4.2 `gate.py`

**Where:** one new import beside line 98 (`from gate_mailtag import register_mailtag_routes`), one new call beside lines 1211-1219 (immediately after `register_memory_routes(...)`, before the `_ALLOW_NON_LOOPBACK_ENV` block).
**How:** identical injection signature to every existing registration call — `register_mailtag_routes(app, cfg=cfg, audit=_audit, enforce_rate_limit=_enforce_rate_limit, require_api_key=require_api_key)`.
**Rules:** this is the *only* place `gate.py` ever mentions `mailtag`.

### 4.3 `gate_mailtag.py` (new file)

Route surface, mirroring `gate_memory.py`'s 404-when-disabled / 200-with-`enabled:false`-for-status convention:

| Method | Route | Auth | Notes |
|---|---|---|---|
| GET | `/mailtag/status` | none | rate-limited; always 200 + `{enabled, providers: {gmail, microsoft, icloud}, accounts_connected}` |
| GET | `/mailtag/accounts` | **API key** | rate-limited; 404 when disabled; never returns a token |
| POST | `/mailtag/accounts/{id}/disconnect` | **API key** | requires non-empty `reason`; best-effort provider-side revoke + always deletes the local record |
| POST | `/mailtag/plan` | **API key** | requires non-empty `reason`; body: `{account_id, criteria, tag_name, confidence_threshold}`; read-only against the provider, zero writes |
| GET | `/mailtag/plan/{id}` | **API key** | view a plan's status and preview |
| POST | `/mailtag/plan/{id}/apply` | **API key** | requires non-empty `reason` + `confirm: true`; refuses an expired plan; the only route that writes to a provider |
| POST | `/mailtag/plan/{id}/reject` | **API key** | requires non-empty `reason`; no provider call |
| POST | `/mailtag/plan/{id}/undo` | **API key** | requires non-empty `reason`; reverses every journal row for that plan, best-effort per message |

**Note on the OAuth connect flow deliberately having no HTTP route:** keeping it entirely CLI-driven avoids needing a redirect listener bound anywhere near `gate.py`'s loopback-only port, sidesteps `TrustedHostMiddleware` interaction entirely, and mirrors the `sync/`+`rclone` precedent exactly (§1.2). `GET /mailtag/status`'s response can still name the exact CLI command to run for console discoverability.

### 4.4 `mailtag/` package (new)

Per §3.1/§5.

### 4.5 `harness/env_keys.py`

**Where:** three new `KeySpec` entries appended to `MANAGED_KEYS` (§3.3).
**Rules:** unchanged module — no code change beyond the tuple entries; `read_status`/`mask` already generalize to any allowlisted name.

### 4.6 `schemas/api.py`

New Pydantic request models, each opening with `model_config = ConfigDict(extra='forbid', strict=True)` per the mandatory convention (line 5's docstring): `MailtagPlanRequest`, `MailtagApplyRequest`, `MailtagDisconnectRequest`, `MailtagRejectRequest`, `MailtagUndoRequest` — each carrying a required non-empty `reason: str`.

### 4.7 `tests/conftest.py`

New `MockGmailClient`/`MockGraphClient`/`MockImapClient`, each with the exact `__init__(self, response=..., available=True)` / `is_available(self)` / and provider-appropriate `search`/`apply_tag` methods recording `last_call` — mirroring `MockGrokClient` byte-for-byte in shape (`MockClaudeClient(MockGrokClient)` shows this needs almost no new code for a second provider).

### 4.8 Everything that enumerates subsystems by name

- `CLAUDE.md`: Key Modules table row for `gate_mailtag.py`/`mailtag/`; the route table rows from §4.3; §8's coverage-sources sentence (18th/19th/20th entries); §3's I6 module-isolation row extended to include `gate_mailtag.py`.
- `pyproject.toml`: `[tool.coverage.run] source` gets `gate_mailtag` and `mailtag`; `[tool.hatch.build.targets.wheel]` packages gets `mailtag`; `force-include` gets `gate_mailtag.py`; `[tool.hatch.build.targets.sdist]` include gets both.
- `.github/workflows/ci.yml`: new `--cov=gate_mailtag` and per-submodule `--cov=mailtag.providers.gmail` etc. (ci.yml's convention is finer-grained than pyproject's, per `memory`'s own precedent — they are not the same list).
- `.gitignore`: a `data/mailtag/` block mirroring the `data/memory/` block added for `memory`.
- `docs/THREAT_MODEL.md`: a new amendment (§7). **Note:** the current highest ordinal in the `### <Ordinal> amendment` sequence is `### Thirteenth amendment` (groundedness evaluator); there is also a stray, differently-formatted `## Ninth amendment (2026-08-15) — security.api_key_optional` heading physically located after `## 7. Reporting`, reusing an already-used ordinal — a pre-existing doc-sync defect this plan does **not** fix (§13.4). This plan's amendment should be numbered `Fourteenth`.
- `.claude/rules/PROJECT_RULES.md`'s coverage-count bullet is **already stale** (says "16 sources", omits `opentweet`) independent of this plan — worth fixing in the same PR that adds Mailtag's entries, flagged here rather than fixed now.

---

## 5. Module responsibilities

- **`models.py`** — plain dataclasses, no provider-specific fields leak past `provider.py`'s Protocol boundary.
- **`provider.py`** — the `MailProvider` Protocol:
  ```python
  class MailProvider(Protocol):
      async def search(self, query: MailQuery) -> list[MailRef]: ...
      async def get_metadata(self, ids: list[str]) -> list[MailMetadata]: ...
      async def list_tags(self) -> list[str]: ...
      async def ensure_tag(self, name: str) -> None: ...          # create-if-missing; APPLY-TIME ONLY
      async def apply_tag(self, ids: list[str], tag: str) -> TagApplyResult: ...   # APPLY-TIME ONLY
      async def verify_still_valid(self, ids: list[str]) -> list[str]: ...          # pre-apply revalidation
  ```
  No `get_body` in the minimal-privilege default path — full-body retrieval is opt-in per §2.1's data-minimization goal and would be a separate, explicitly-gated method (`get_body_if_enabled`) not called by the default classifier.
- **`providers/gmail.py`** — httpx calls only (`users.messages.list/get`, `users.labels.create`, `users.messages.modify`/`batchModify`). No `google-api-python-client` SDK — see §8.
- **`providers/graph.py`** — httpx calls only (`/me/messages`, `/me/outlook/masterCategories`). No `msal` SDK — see §8.
- **`providers/imap_icloud.py`** — stdlib `imaplib` + stdlib `email` (module, imported from *outside* the `mailtag` package's own namespace, which is exactly why §1.2 renamed the package away from `email`). Default tag mechanism: dedicated IMAP folder (e.g. `PKGS`) + `COPY` (never `MOVE`) per §1.4's Fastmail-precedent finding. `verify_still_valid` for iCloud additionally re-issues `CAPABILITY` to catch a dropped/expired session.
- **`oauth.py`** — PKCE (`secrets.token_urlsafe`, `hashlib.sha256`, `base64.urlsafe_b64encode` — all stdlib) + a single-shot `http.server.HTTPServer` bound to `127.0.0.1:0` with a hard 300s watchdog timeout, `webbrowser.open` to launch consent. Used only from `cli.py`'s `connect` subcommand, never from a request handler.
- **`token_store.py`** — the three-backend abstraction from §3.2.
- **`store.py`** — SQLite/Postgres connect-and-migrate, mirroring `utils/personality_db.py`'s `connect()` shape and `_AUTH_DB_ENV`-style env var resolution.
- **`classifier.py`** — deterministic rules first (sender/subject/keyword match against `criteria_json`); for ambiguous candidates only, a plain library call to `llm.client.LocalLLMClient.generate(...)` — **never via `graph.py`, never a LangGraph node** (§1.2). Every subject/body/sender snippet fed to that prompt is scanned through `utils.sanitizer.check_input` first and wrapped as untrusted content, mirroring E3's exact precedent for MCP `hybrid_search` input scanning — email content is attacker-reachable text and must be treated with the same discipline.
- **`planner.py`** — `propose_plan(...)`: read-only against every provider method it calls (`search`, `get_metadata`, `list_tags`) — never `ensure_tag`/`apply_tag`. Returns a `TagPlan` row; **never writes to the provider**, matching the internal governance pattern's "propose never writes" rule found in three independent places (`agentic/registry.py`, `real_repo_loop.py`, `fsconnect/writer.py`).
- **`applier.py`** — `apply_plan(plan_id, reason, confirm)`: (1) load the plan row, refuse if `status != 'approved'` or `now > expires_at`; (2) `verify_still_valid` against live provider state (the pre-apply revalidation analog to `real_repo_loop`'s re-reading `protected_write_paths` from live config rather than trusting the persisted record); (3) `ensure_tag`; (4) `apply_tag` in provider-appropriate batches; (5) write a `mailtag_tag_journal` row **before** the provider call (`tag_intent`) and one **after** (`tag_applied`) — the exact two-phase-audit pattern from `agentic/fsconnect/writer.py`, so a crash mid-batch leaves a detectable orphaned intent rather than a silent unaudited write.
- **`audit.py`** — every state transition (`plan_created`, `plan_approved`, `plan_rejected`, `tag_intent`, `tag_applied`, `plan_undone`) goes through `utils.logger.audit_log` (SHA-256 hashing of any query-shaped text, no raw message content persisted) and the action-plane `emit_numbat_event`, matching `agentic/`'s existing dual-write.
- **`cli.py`** — `connect <provider> <label>`, `accounts`, `plan ...`, `apply <plan_id>`, `reject <plan_id>`, `undo <plan_id>`. Exit codes follow the closed `0`/`2`/`3`/`4` contract (`agentic.cli`'s convention): `0` ok, `2` operation failed, `3` env/config error, `4` write refused.

---

## 6. Isolation test design

`tests/test_mailtag_isolation.py`, mirroring `tests/test_memory_isolation.py`'s three checks exactly:

1. `test_no_toplevel_import_of_mailtag` — AST-parametrized over `gate.py`, `graph.py`, `mcp_hybrid_server.py`, `retrieval/hybrid_search.py`, and `gate_mailtag.py` itself: no top-level `import mailtag` anywhere except inside a function body in `gate_mailtag.py`.
2. `test_mailtag_package_does_not_import_forbidden` — every `mailtag/**/*.py` file, AST-checked, never imports `gate`, `gate_ops`, `gate_auth`, `gate_memory`, `graph`, `mcp_hybrid_server`, `agentic`, `sync`, `guardrails`, `harness`, `telegram`, `opentweet`.
3. `test_gate_may_import_gate_mailtag_only` — `gate.py` may import `gate_mailtag` but never `mailtag` directly, with the soft cross-check that if `register_mailtag_routes` appears in `gate.py`'s source, `gate_mailtag` must appear in its imports.

---

## 7. I1–I6 impact analysis

| Invariant | Impact | Evidence |
|---|---|---|
| I1 RAG-first | **None.** `retrieve` remains the unconditional entry point; Mailtag never precedes or participates in it. | `graph.py` untouched by this plan. |
| I2 Topology = policy | **None.** No new graph node, no new router, no LLM-influenced branch. Confirmed there is no mechanism for this to be otherwise (§1.2). | Direct grep + full read of `graph.py`, zero tool-calling primitives found. |
| I3 Triple-gated external fallback | **None.** Mailtag's own provider calls are gated by its own five-part chain (§10), which is deliberately modeled on, but independent of, I3 — it does not touch `mode`/`grok.enabled`/`claude.enabled`/`user_confirmed_online` at all. | `graph.py`'s `user_gate_router` untouched. |
| I4 Audit convergence | **None to the graph.** Mailtag has its own, parallel audit convergence (every state transition through `utils.logger.audit_log`, §5) — it does not add a node to the nine upstream paths that already converge at `audit_logger`. | New `audit.py` module; no `graph.py` edit. |
| I5 Soul governance | **None** — Mailtag never touches `soul.md` or `PersonalityManager`. It borrows I5's *spirit* (mutation requires a non-empty human `reason` string, atomic write) for its own apply step, but this is precedent-following, not an I5 change. | `applier.py`'s `reason` requirement mirrors `utils/personality.py`'s `apply_evolution` contract. |
| I6 Module isolation | **Extended, not violated.** `mailtag/` joins the isolated-out-of-band set exactly like `memory/`, `agentic/`, `sync/`, `telegram/`, `opentweet/`. Verified both directions in §6. | New `tests/test_mailtag_isolation.py`, mirroring `test_memory_isolation.py`. |

**Sharp edges to not introduce:**

- Do not let `gate_mailtag.py`'s handlers call `mailtag.oauth` — the OAuth flow is CLI-only by design (§4.3); a handler that started an OAuth listener mid-request would need to bind a second port on a loopback-only service and interact badly with `TrustedHostMiddleware`, which this plan deliberately avoids rather than solves.
- Do not let `classifier.py` construct a LangChain tool schema "for consistency with the rest of the codebase" — there is no such consistency to match (§1.2); a plain `generate(prompt)` call is correct and sufficient.
- Do not let `applier.py` trust `candidate_ids_json` from the persisted plan row without `verify_still_valid` — the plan's own SHA is not a security boundary in this codebase's existing patterns (no run record anywhere carries a cryptographic hash-chain, per `docs/work/FSCONNECT_SQL_ROADMAP.md`'s own open-work note), so re-fetching live state at apply time is the actual anti-drift mechanism, not a hash comparison.

---

## 8. Dependency-ordered implementation tasks

1. `mailtag/models.py`, `mailtag/provider.py` (Protocol only, no implementation) — establishes the contract every provider and every test double implements against.
2. `mailtag/store.py` + the three-table schema (§3.2) + `CYCLAW_MAILTAG_DB_URL` resolution mirroring `utils/authn_store.py`'s `connect()`.
3. `mailtag/token_store.py`, three backends. macOS/Windows backends are thin subprocess wrappers around **existing** scripts (no new shell script logic beyond parameterizing service/account name); SQLite-600 backend needs no new code beyond a mode-600 file check mirroring `harness/env_keys.py`'s `_FILE_MODE`.
4. `config.yaml`'s `mailtag:` block (§3.3, §4.1) + `harness/env_keys.py`'s three new `KeySpec` entries (§4.5) — config exists before any provider code runs against it.
5. `mailtag/providers/gmail.py` (httpx only — confirmed zero new runtime dependency needed: PKCE is pure stdlib, `google-auth-oauthlib`/`google-api-python-client` are not required if the token exchange and label/message REST calls are hand-rolled against `httpx`, which is **already a CyClaw dependency** via `llm/client.py`'s shared `_post_with_retry`).
6. `mailtag/oauth.py` (Gmail path first) + `mailtag/cli.py connect gmail`.
7. `mailtag/planner.py` + `mailtag/classifier.py` (Gmail-only, deterministic rules first) + `mailtag/applier.py`.
8. `gate_mailtag.py` + `gate.py`'s two-line insertion (§4.2) + `schemas/api.py` models (§4.6).
9. `tests/test_mailtag_isolation.py` (§6) + `MockGmailClient` in `tests/conftest.py` (§4.7) + unit tests for `planner`/`applier`/`classifier` against the mock.
10. `docs/mailtag/README.md` (short operator doc, mirroring `docs/memory/README.md`'s shape — not written by this plan; a task for the implementer) + this document's own `IMPLEMENTATION_PLAN.md` kept current.
11. `docs/THREAT_MODEL.md` Fourteenth amendment (§4.8).
12. `CLAUDE.md`/`pyproject.toml`/`ci.yml`/`.gitignore` updates (§4.8) — done together, last, since they depend on every file name above being final.
13. **Gate 1 (approval checkpoint):** Gmail-only, `mailtag.gmail.enabled` flippable, everything else still `false`. Do not proceed to Microsoft/iCloud until this slice is reviewed and merged.
14. `mailtag/providers/graph.py` + `MockGraphClient` + Microsoft's `oauth.py` path (no client secret needed, per Appendix) — repeats steps 5-9 for Microsoft.
15. `mailtag/providers/imap_icloud.py` + `MockImapClient` — repeats steps 5-9 for iCloud, **gated on the live-account keyword-persistence probe in §10 having been run first** (folder+COPY does not need the probe; a future keyword-based mode does).

---

## 9. Affected / new tests

| Test file | Purpose |
|---|---|
| `tests/test_mailtag_isolation.py` | I6, three checks (§6) |
| `tests/test_mailtag_store.py` | schema creation, `CYCLAW_MAILTAG_DB_URL` resolution, no fallback to `CYCLAW_DB_URL`/`CYCLAW_AUTH_DB_URL` |
| `tests/test_mailtag_token_store.py` | backend selection logic; SQLite-600 file-mode assertion; keychain/CredMan backends mocked (no live OS calls in CI) |
| `tests/test_mailtag_planner.py` | `propose_plan` never calls a mocked provider's `ensure_tag`/`apply_tag`; confidence-threshold routing to the classifier |
| `tests/test_mailtag_classifier.py` | injection-scan is invoked on every subject/body snippet before it reaches a prompt; `MockLocalLLM` never sees unscanned text |
| `tests/test_mailtag_applier.py` | expired-plan refusal; `verify_still_valid` called before every `apply_tag`; two-phase audit rows (`tag_intent` before `tag_applied`) present even on a simulated mid-batch failure |
| `tests/test_mailtag_undo.py` | undo reverses journal rows; a message already re-tagged by the user is skipped, not clobbered |
| `tests/test_gate_mailtag.py` | route-level: 404 when disabled, 200+`enabled:false` for `/mailtag/status`, reason-required on every mutating route, rate-limit + API-key dependency order matches the existing `gate_ops`/`gate_memory` pattern |
| `tests/test_terminal_contract.py` | extend `_POST_PATHS` if a console surface is ever added for Mailtag (none in this plan's v1 scope) |

`pyproject.toml`'s `[tool.coverage.run] source` and `ci.yml`'s `--cov=` flags per §4.8.

---

## 10. Security checklist (implement-time)

- [ ] `mailtag.enabled` ships `false`; every nested provider flag ships `false` independently.
- [ ] Every mutating route requires Bearer `CYCLAW_API_KEY` (via the injected `require_api_key`) **and** a non-empty `reason` string.
- [ ] `POST /mailtag/plan/{id}/apply` additionally requires `confirm: true` and refuses an expired plan (`now > expires_at`) before touching a provider.
- [ ] `planner.py` never imports or calls `ensure_tag`/`apply_tag` on any provider — enforced by a unit test using a `MockProvider` whose those two methods raise if called during `propose_plan`.
- [ ] Every subject/body/sender string reaching `classifier.py`'s LLM call passes through `utils.sanitizer.check_input` first, wrapped as untrusted content.
- [ ] `applier.py` writes a `tag_intent` audit row before, and `tag_applied` after, every provider write call (two-phase audit).
- [ ] No refresh token or app-specific password is ever included in an audit record, a numbat event, or an HTTP response body — `mailtag_accounts.encrypted_token` is never read by any `GET` handler.
- [ ] `GET /mailtag/accounts` returns account metadata only (id, provider, label, mailbox address, scopes, connected_at) — never `encrypted_token` or `token_backend`'s underlying secret.
- [ ] Gmail: the plan phase requests only `gmail.readonly`; `gmail.modify` is requested (incremental authorization) only at first apply, per account.
- [ ] Microsoft: the plan phase requests only `Mail.Read` (+`MailboxSettings.Read` if previewing existing categories); `Mail.ReadWrite`/`MailboxSettings.ReadWrite` requested only at first apply.
- [ ] iCloud: **before implementing any keyword/flag-based tagging**, run a live probe (`CAPABILITY` then `STORE +FLAGS` with a client-invented keyword, then reconnect and `FETCH FLAGS`) against a real iCloud test account and record the result in this document's §1.4/Appendix. Until that probe passes, iCloud tagging implementation is folder+`COPY` only.
- [ ] `token_store.py`'s SQLite-600 fallback creates the file with mode `0600` at creation time (matching `harness/env_keys.py`'s `_FILE_MODE` pattern), not via a post-hoc `chmod` that a race could beat.
- [ ] `invariant-guard` re-run clean after `gate.py`'s two-line insertion.
- [ ] `tests/test_mailtag_isolation.py` passes both directions before the first PR in §12 is opened.

---

## 11. Rollout / operator story

1. Operator sets `mailtag.enabled: true` and `mailtag.gmail.enabled: true` in `config.yaml`, restarts `gate.py`.
2. Operator registers a Google Cloud "Desktop app" OAuth client (one-time, outside CyClaw), sets `EMAIL_GMAIL_CLIENT_ID`/`EMAIL_GMAIL_CLIENT_SECRET` via `harness/env_keys.py`'s existing `/api` panel or `macos/setup-cyclaw-keys.sh`-style flow, restarts.
3. `python -m mailtag.cli connect gmail --account personal` — browser opens, operator consents to `gmail.readonly` only, CLI confirms connection and prints the mailbox address.
4. `python -m mailtag.cli plan --account personal --tag PKGS --keyword "dependabot,pip,npm,apt,brew" --reason "quarterly inbox cleanup"` — prints a plan ID and a preview (counts, sample senders/subjects).
5. Operator reviews the preview, then `python -m mailtag.cli apply <plan_id> --reason "reviewed, looks right" --confirm` — CyClaw requests the `gmail.modify` elevation (a second, one-time browser consent for that account), applies the label, prints a summary.
6. `GET /mailtag/status` or the CLI's `accounts` subcommand shows connection state at any time; `undo <plan_id>` reverses a run if needed.

---

## 12. PR shape

Following CLAUDE.md's "one reviewable concern each" convention, staged as independent, sequentially-mergeable draft PRs (never bundled):

1. **Scaffolding** — `mailtag/models.py`, `provider.py`, `store.py`, `token_store.py`, config block, `KeySpec` additions, isolation test. No provider code, no routes yet. Reviewable in isolation; nothing is reachable (`mailtag.enabled` stays false and nothing calls into it).
2. **Gmail provider + CLI connect/plan/apply** — the first end-to-end vertical slice, `gmail.enabled` flippable independently of the rest.
3. **`gate_mailtag.py` HTTP surface** — routes only, against the store/provider from PR 2.
4. **Microsoft provider** — repeats PR 2's shape for Graph.
5. **iCloud provider** — gated on the §10 keyword-persistence probe; folder+COPY only for v1.
6. **Docs + CLAUDE.md/pyproject.toml/ci.yml/.gitignore sync** — last, once every file name from PRs 1-5 is final.

Each PR body states its Invariant/Governance Impact per §7's table (scoped to what that PR actually touches), per the repo's PR template.

---

## 13. Self-check (plan gate)

### 13.1 Invariant violations introduced?

None. See §7's full table. The one new isolation surface (I6, extended) is additive and tested the same way every prior extension (`memory`, `telegram`, `opentweet`) was.

### 13.2 Invented APIs? — verification table

| Claim used in this plan | Verified against |
|---|---|
| `register_memory_routes(app, cfg, audit, enforce_rate_limit, require_api_key)` signature | `gate_memory.py:59-65`, `gate.py:1211-1219` |
| `gate.py` never contains LLM tool-calling primitives | Direct grep of `graph.py`/`llm/client.py` for `bind_tools`/`tool_calls`/`StructuredTool`/`function_call`/`tools=`/`ToolNode`/`create_react_agent`/`AgentExecutor`/`@tool`/`langchain_core.tools` — zero matches, this research pass |
| `CYCLAW_AUTH_DB_URL` never falls back to `CYCLAW_DB_URL` | `utils/authn_store.py:6-16,33` |
| `MANAGED_KEYS`/`KeySpec` shape | `harness/env_keys.py` (dataclass definition + tuple) |
| `MockGrokClient(response=..., available=True)` contract | `tests/conftest.py:199-221` |
| Windows Credential Manager scripts already exist | `powershell/CyClaw-CredMan-Set.ps1`, `powershell/CyClaw-CredMan-Env.ps1` (Read-verified this session) |
| No OAuth code exists anywhere in the repo | repo-wide grep, this research pass |
| `gmail.labels` cannot apply a label to a message; needs `gmail.modify` | `developers.google.com/workspace/gmail/api/auth/scopes` + a Google developer forum thread, cross-checked via WebSearch (WebFetch was egress-blocked this session — see Appendix caveats) |
| Outlook category-taxonomy CRUD needs `MailboxSettings.ReadWrite`, separate from `Mail.ReadWrite` | `learn.microsoft.com/en-us/graph/api/outlookuser-post-mastercategories`, fetched via `raw.githubusercontent.com` mirror this session |
| iCloud IMAP keyword persistence is unconfirmed; Fastmail uses folders instead | WebSearch synthesis this session; **not** independently fetched from a primary source — see Appendix |
| RFC 8252 loopback + PKCE removes the need for a confidential client secret | WebSearch synthesis this session (`rfc-editor.org` was egress-blocked) — see Appendix |

### 13.3 Deviations from user prompt (documented)

1. Package renamed `email/` → `mailtag/` (stdlib collision, §1.2).
2. `agentic/`-style multi-iteration run-record persistence (as used by `real_repo_loop.py`) was considered and **rejected** in favor of `agentic/registry.py`'s simpler single-shot propose/apply shape, since tagging is not an iterative verify-and-retry loop.
3. The original proposal's phrase "not embedded directly in the conversational agent" is upgraded here from a design preference to an architectural fact: there is no mechanism in this codebase by which it *could* be embedded (§1.2).
4. Local encryption-at-rest for the SQLite fallback token column was **not** adopted by default (no `cryptography` dependency added) — flagged as an explicit open decision in §13.4 rather than silently decided either way.

### 13.4 Open decisions for approver

1. **New runtime dependency for at-rest token encryption?** This plan's default is "no" (mode-600 file, matching existing `CYCLAW_API_KEY` risk acceptance). Adding `cryptography` for Fernet-based encryption of the SQLite fallback column is a High-tier decision (CLAUDE.md §7) this document does not make unilaterally.
2. **iCloud priority.** Ship it at all in v1, or defer entirely until the keyword-persistence probe (§10) is run against a real account? This plan's task ordering (§8, step 15) already gates iCloud behind that probe, but the approver may prefer to cut iCloud from scope entirely for the first release.
3. **`validate_mailtag_config`?** Whether `utils/config_validation.py` needs a boot-time validator for the new `mailtag:` block (mirroring `validate_auth_config`) was not confirmed against that file's current contents — an implementer should check before assuming none is needed.
4. **The pre-existing `THREAT_MODEL.md` duplicate-"Ninth amendment" numbering defect** (§4.8) is out of this plan's scope to fix, but the approver may want it fixed in the same PR that adds the Fourteenth amendment, since both touch the same document.
5. **Whether Microsoft's `Mail.ReadWrite` needs tenant-admin pre-clearance** in practice: the base permission's `AdminConsentRequired` flag is `No` for both personal and work/school accounts, but Entra tenants can independently classify a permission as higher-impact and restrict end-user self-consent above a threshold (unverified against any specific tenant this session) — operators connecting a work/school account may need their own IT admin's help regardless of what this plan's default flow assumes.

---

## 14. Approval gate

**This plan is not authorization to write code.** Per CLAUDE.md §1's feature-freeze stance, an explicit go-ahead from the repository owner is required before PR 1 of §12 is opened, independent of this document's technical completeness. Re-verify every file:line citation in §1.3/§13.2 against current `main` at that time — this document was grounded against `57e052ed9222d42a20b95ceb4dec71d21e169f57` and line numbers drift with every merge.

---

## Appendix — Provider API Reference (as verified 2026-08-27)

**Environment note:** this sandbox's egress proxy blocked direct `WebFetch` to nearly every primary vendor domain (`developers.google.com`, `learn.microsoft.com`, `support.apple.com`, `rfc-editor.org`, `datatracker.ietf.org`, and others all returned `EGRESS_BLOCKED`). Facts below were obtained via `WebSearch`'s live synthesis of those same pages (which it could reach) plus, for Microsoft, direct fetches of the docs-authoring GitHub repos' raw markdown (`raw.githubusercontent.com` was reachable). Each claim was cross-checked across 2-3 independently phrased queries. Treat this as high-confidence secondary verification — a session with unblocked `WebFetch` should re-pull the primary pages directly before any of the exact strings below are hard-coded into shipped code.

### Gmail

| Scope | String | Sensitivity | Covers |
|---|---|---|---|
| Labels-only | `https://www.googleapis.com/auth/gmail.labels` | Non-sensitive | CRUD on label *objects* only — cannot apply a label to a message |
| Read-only | `https://www.googleapis.com/auth/gmail.readonly` | Restricted | All reads, no writes |
| Modify | `https://www.googleapis.com/auth/gmail.modify` | Restricted | All read/write incl. Trash, **excludes** permanent delete |
| Metadata | `https://www.googleapis.com/auth/gmail.metadata` | Restricted | Headers/labels/history only, no body |
| Full | `https://mail.google.com/` | Restricted | Everything, including permanent delete |

- Restricted scopes require Google app verification **plus** an annual third-party CASA security assessment for production use beyond 100 test users.
- `users.messages.modify` (`POST .../messages/{id}/modify`, body `{addLabelIds, removeLabelIds}`, ≤100 IDs per list per call) is the only way to attach a label to a message, and it needs `gmail.modify` — `gmail.labels` alone is insufficient.
- Labels are genuinely multi-valued (tags, not folders); a message keeps `INBOX`/`UNREAD`/etc. alongside any number of custom labels unless explicitly removed.
- Google's "Desktop app" OAuth client type supports PKCE + loopback redirect (`http://127.0.0.1:{port}` or `localhost`) and continues to be supported for that client type even as Google restricted/deprecated it for other native app types. Google's docs describe the accompanying `client_secret` as *not confidential* for an installed app rather than eliminating it — PKCE, not secrecy of that value, is the actual security boundary. (Lower-confidence: whether the 2026 Cloud Console still issues a `client_secret` field for new Desktop-type clients was not independently re-confirmed live.)

### Microsoft 365 / Outlook (Graph)

| Permission (Delegated) | Identifier | Admin consent | Covers |
|---|---|---|---|
| `Mail.Read` | `570282fd-fa5c-430d-a7fd-fc8dc98a9dca` | No (either account type) | Read mail |
| `Mail.ReadWrite` | `024d486e-b451-40bb-833d-3e66d98c5c73` | No (either account type) | CRUD mail, incl. PATCHing `categories`; **excludes send** |
| `MailboxSettings.Read` | `87f447af-9fa4-4c32-9dfa-4a57a73d18ce` | No | Read the master category list |
| `MailboxSettings.ReadWrite` | `818c620a-27a9-40bd-a6a5-d96f7d610b4b` | No | Create/edit/delete master-category entries |

- Outlook's `categories` property is a multi-valued String collection on any item type (message/event/contact); applied via `PATCH /me/messages/{id} {"categories":[...]}`, which needs `Mail.ReadWrite`.
- Creating/recoloring/deleting an entry in `/me/outlook/masterCategories` needs the **separate** `MailboxSettings.ReadWrite` permission — categories are modeled as a mailbox *setting*, not a `Mail.*` resource, unlike Gmail where label CRUD and application sit under closely related scopes.
- MSAL's public/native client flow supports PKCE with a `http://localhost` redirect and explicitly must **not** present a client secret when redeeming a code (public clients).
- A tenant's own admin-configurable consent policy can still restrict end-user self-consent above an "impact" threshold independent of a permission's base `AdminConsentRequired` flag — not verified against any specific tenant.

### iCloud Mail

- IMAP `imap.mail.me.com:993` (SSL), SMTP `smtp.mail.me.com:587` (STARTTLS). No POP3.
- Authentication is an **app-specific password** (2FA on the Apple ID is a hard prerequisite to generate one); up to 25 concurrent, revocable independently, all-or-nothing account access — no scoping mechanism exists at all.
- No self-service OAuth client registration for third-party developers (unconfirmed against a primary Apple developer-portal source; based on secondary synthesis) — "Sign in with your Apple Account" for Mail is reserved for a small Apple-vetted allowlist.
- **Whether iCloud's IMAP server persists arbitrary client-defined keywords is genuinely unresolved** — no authoritative source found either way, and no live probe was possible from this sandbox. Apple's own Mail.app uses a small closed set of IANA-registered keywords for colored flags (evidence *some* keyword persistence exists), but a comparable modern provider (Fastmail) explicitly avoided building its own labels feature on IMAP keywords, citing poor third-party support, using real folders + `COPY` instead. This plan follows Fastmail's precedent as the default (§1.4, §10) until a live probe says otherwise.

### OAuth 2.0 for native apps (cross-cutting)

- RFC 8252 §7.3 (Loopback Interface Redirection): a native app should redirect to `http://127.0.0.1:{port}` (or `[::1]`) when the platform allows opening a loopback port — the standard basis for a CLI/desktop OAuth flow with no public HTTPS endpoint.
- RFC 8252 §8.4: authorization servers should treat native apps as public clients that cannot keep a secret confidential.
- RFC 7636 (PKCE) exists specifically to close the authorization-code-interception risk this loopback pattern is exposed to, and removes the need for a static secret to prove flow continuity for a public client.
- Refresh-token storage ordering, best to worst: OS-native secret store (macOS Keychain / Windows Credential Manager via DPAPI / Linux Secret Service via `libsecret`+D-Bus) → an app-managed encrypted-at-rest file → a plaintext file with owner-only permissions. **Known pitfall:** an OS-keychain item can become unreadable after certain credential-reset events (macOS: login password reset without the old password invalidates the login keychain; Windows: DPAPI data becomes undecryptable after an admin-forced reset or a move to a different machine/profile) — any keychain-backed design needs an explicit re-authentication fallback, not just a happy-path read.
- Linux's Secret Service backend needs a live D-Bus session bus and a running keyring daemon — commonly absent on a headless server, which is CyClaw's realistic deployment shape. Treat the encrypted-file/mode-600 tier as Linux's practical default, not an afterthought.
