---
name: otel-hardening
description: >
  Re-verify that CyClaw's telemetry-kill contract still holds end to end — the
  canonical env maps in utils/telemetry_kill.py (telemetry vs. update-check,
  visibly separate), the scrubbed credential/declarative-config names, the real
  ONNX Runtime suppression (ORT_DISABLE_TELEMETRY before import + the
  disable_telemetry_events() API at the load seams), and the process-boundary
  delivery surfaces (Docker ENV/compose, macOS/PowerShell launchers, generated
  launchd plists / Windows tasks / cron lines, agentic verifier children, gh
  children) — via a static checker with an INDEPENDENT name→value oracle and a
  category-1-to-5 egress classification of every dependency, provider,
  executable, connector, scheduled job, and launcher. Then a live vendor-doc
  sweep for drift since each control's last review date. Use when asked to
  audit/harden/re-verify telemetry, check for phone-home leaks, after bumping
  any telemetry-capable vendor pin, when adding a dependency or process
  launcher (strict mode fails on an unclassified one), or as a standing sweep —
  CyClaw forbids unsolicited secondary telemetry, and this gap doesn't announce
  itself.
---

# otel-hardening

Re-verify CyClaw's telemetry-kill contract: static half first (deterministic,
offline), live vendor half second (network; propose-then-apply).

**The contract, stated precisely (issue #1135).** CyClaw disables unsolicited
secondary telemetry and analytics before every supported process or
telemetry-capable dependency initializes. Intentional, policy-gated feature
traffic — triple-gated cloud models, Telegram, OpenTweet, rclone/Dropbox,
databases, the one-time embedding bootstrap — is documented separately
(SECURITY.md egress classification) and is never mislabeled or blocked as
telemetry. **No environment variable here is a general network kill switch**:
these values silence vendor telemetry readers; they do not close sockets.

## Where the contract lives

| Piece | File | What |
|---|---|---|
| Canonical maps | `utils/telemetry_kill.py` | `TELEMETRY_KILL` (21) + `UPDATE_CHECK_OPT_OUT` (4, ancillary — never counted as telemetry) + `SCRUBBED_ENV_KEYS` (5 tracing credentials/destinations + `OTEL_CONFIG_FILE`/`OTEL_EXPERIMENTAL_CONFIG_FILE`, removed outright because declarative OTel config outranks the SDK-disable values) |
| Pure child builder | same | `build_telemetry_safe_env(base)` — copy, overlay, scrub; shares the `_enforce` core with `apply_telemetry_kill()` so parent/child cannot drift; `scheduler_env_overlay()` for generated jobs; `python -m utils.telemetry_kill --export {shell,powershell}` for launchers |
| ONNX API half | `utils/onnx_telemetry.py` | `suppress_onnx_telemetry()` — getattr-guarded, idempotent, absent-safe; called at `retrieval/vector_store.py` (both chromadb client sites) and `guardrails/integration.py` (`force_import=True` before `LLMRails`) |
| Conditional HF pair | `retrieval/embeddings.py` | `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` only once the model is confirmed cached — never unconditional |
| Delivery surfaces | Dockerfile + docker-compose, `macos/invoke-cyclaw.sh`, `powershell/Invoke-CyClaw.ps1` + `Install-CyClaw.ps1` shim, all plist/task/cron generators, `agentic/executor/runner.py`, gh spawn sites, `sync/cli.py` | canonical env exists BEFORE the interpreter/tool starts |
| Independent oracle + inventory | `.claude/skills/otel-hardening/check_otel.py` | the second copy of every name→value pair, and the category 1–5 classification of every component |
| Reference doc | `docs/security-philosophy/cyclaw_telemetry_kill.env` | `export KEY=value`, three sections (unconditional / ancillary / conditional), checker- and pytest-enforced |

## Steps

1. **Static contract check** (offline, no side effects — the checker AST-parses
   and never imports the production module):

   ```bash
   python3 .claude/skills/otel-hardening/check_otel.py --strict --as-of $(date +%F)
   ```

   T1 shapes · T2 exact value oracle (telemetry + update-check; missing /
   extra / mismatched all FAIL) · T3 scrub oracle · T4 staleness (code stamps
   + per-inventory-row `reviewed` dates, 120d window; `--as-of` makes it
   deterministic for tests) · T5 vendor pin drift (pyproject + constraints;
   includes langsmith) · T6 installed-transitive info · T7 conditional HF
   wiring · T8 reference-.env format+values · T9 helper wiring (`_enforce`
   shared core) · T10 Docker delivery · T11 launcher delivery · T12
   programmatic-bypass sweep + child-env builders still route through the
   canonical helpers · T13 classification inventory (schema + every declared
   component resolves to a category; unclassified ⇒ WARN, FAIL under
   `--strict`; unbounded telemetry-capable transitives are standing INFO
   findings) · T14 ONNX seams.

2. **Mutation self-test** — a checker that cannot fail proves nothing:

   ```bash
   bash .claude/skills/otel-hardening/verify.sh
   ```

   21 scenarios; every rule has a mutation that must flip it, and each
   mutation asserts it actually changed the file (two historical silent-no-op
   sed bugs are why).

3. **Live vendor sweep** (network) — for each category-1 row in
   `check_otel.py`'s `INVENTORY` whose `reviewed` date is old, or whose pin
   drifted (T5), re-read the official source URL recorded on the row and
   confirm the control still exists with the same name, value semantics, and
   enforcement timing. Current per-vendor questions:

   | Vendor | Re-verify |
   |---|---|
   | chromadb (**pinned 1.5.9**) | The `CHROMA_OTEL_*` names are this version's legacy surface and `otel_init()` early-returns on granularity `none`. Current Chroma docs use different names — record by version; do NOT blindly replace the legacy names while the pin stays 1.5.9. `Settings(anonymized_telemetry=False)` still governs only the PostHog path. |
   | langsmith/langchain | 4-name precedence (`get_env_var` lru_cache latch) unchanged? Upload-without-API-key still only warns? `LANGSMITH_RUNS_ENDPOINTS` still a fan-out destination? |
   | onnxruntime | `ORT_DISABLE_TELEMETRY` still the documented pre-init env control (Privacy.md)? `disable_telemetry_events()` still the API? Any new event class before init that the env var misses? |
   | huggingface_hub | `HF_HUB_DISABLE_TELEMETRY` OR `DISABLE_TELEMETRY` OR `DO_NOT_TRACK` still computed in `constants.py`? |
   | nemoguardrails | `NEMO_GUARDRAILS_NO_USAGE_STATS` / `DO_NOT_TRACK` still honored (0.24.0: `telemetry.py`)? Sink still `events.telemetry.data.nvidia.com`? |
   | gh / PowerShell | `GH_TELEMETRY` value set unchanged? `POWERSHELL_TELEMETRY_OPTOUT` still read once at startup? |

4. **Close a real gap** (additive, low-risk only): update BOTH copies of the
   contract — `utils/telemetry_kill.py` AND `check_otel.py`'s oracles — plus
   the reference `.env`, `tests/test_telemetry_kill.py`'s independent
   `_EXPECTED_*` literals, Dockerfile/compose, and the inventory row
   (`reviewed` date, evidence) in the SAME commit. Add a verify.sh mutation
   for any new rule. Then re-run steps 1–2 and
   `GROK_API_KEY=dummy pytest tests/test_telemetry_kill.py tests/test_telemetry_env_delivery.py tests/test_onnx_telemetry.py tests/test_reference_env.py -q`.

5. **Classify anything new.** A new dependency, provider, executable,
   connector, scheduled job, or process launcher gets an `INVENTORY` row (or
   an alias to one) with exactly one category:
   1 unsolicited telemetry with an official control (control pairs must exist
   in the oracles — never invent one) · 2 ancillary update/version-check
   egress · 3 intentional policy-gated functional egress (controls stay
   empty) · 4 local-only observability/storage · 5 absent/no mechanism found
   (evidence + date for the negative finding). Categories 3–5 never get
   controls invented for them.
   Worked example: `agentic/netconnect/` (passive LAN inventory, 2026-08) is
   category 4 — stdlib-only, so T13's dependency discovery never sees it; a
   first-party connector like this needs its row added by hand.

## Guardrails (restating the invariants this skill touches)

- **G1 ordering is wiring, not style**: `gate.py`'s `_TELEMETRY_KILL =`
  assignment name/position is an invariant-guard AST anchor; every entry
  point applies the kill before heavy imports. `utils/telemetry_kill.py`
  stays **stdlib-only** — never import onnxruntime (or anything heavy) there;
  the API half lives in `utils/onnx_telemetry.py` at the seams.
- **I6 module isolation** is untouched by this skill: the kill module is
  shared `utils/`, imported by both core and out-of-band packages; never make
  it import `agentic/`/`sync/`/`guardrails/`/`harness/`/`telegram/`/`opentweet/`.
- **I3 and the egress classification**: category-3 traffic (cloud fallbacks,
  channels, sync) is gated by CyClaw policy — never "fix" it with the kill
  map, and never weaken a gate to make a kill var simpler.
- The checker must keep **zero side effects**: AST-parse, never import the
  production module; no network in the static half or any test.

## Gotchas

- **`ORT_TELEMETRY_OPT_OUT` is an inert legacy marker** — read by nothing,
  kept only for reference-.env parity. Tests and the checker must never count
  it as ONNX protection; the real pair is `ORT_DISABLE_TELEMETRY` (pre-init
  env, effective for the non-Windows 1DS path since v1.29.0) + the runtime
  API. **Windows caveat**: ETW telemetry is collected only by an external
  trace session, the API cannot undo an init-time event, and absolute
  suppression needs a `--no_telemetry` private build — never claim it.
- **pwsh reads `POWERSHELL_TELEMETRY_OPTOUT` once, at its own startup.** The
  parent must carry it first — that is why the cmd shim and the generated
  task `.cmd`s set it before the `powershell` line; setting it inside a
  running host silences nothing already sent.
- **`HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` stay conditional.** Unconditional
  breaks first-run bootstrap (huggingface_hub latches at import; the real
  in-process gate is `local_files_only`). Do not "complete" the dict.
- **Do not add speculative vendor variables.** Undocumented names are the
  advertise-false-protection anti-pattern; every control needs an official
  source URL on its inventory row.
- **Numbat / Telegram / OpenTweet / rclone never enter the kill map.** Numbat
  is local NDJSON (disable via `numbat.enabled: false`; every event carries
  hostname/username/uid — a second sensitive LOCAL log); the channels are
  first-party httpx feature traffic with no vendor-SDK telemetry key to set.
- **cmd.exe cannot express an empty env var**: `set "NAME="` deletes it. The
  two blank `CHROMA_OTEL_*` values ride generated `.cmd`s as deliberate
  deletion directives (see `utils/win_schtasks.py`); do not "fix" by skipping
  them.
- **`NO_PROXY="*"` bypasses proxies — it does not fail-close networking.**
  The executor's env scrub is best-effort software control (THREAT_MODEL §4
  wording), and its allowlist is exactly why the canonical overlay must be
  re-applied there (`build_telemetry_safe_env`), or children run
  telemetry-live.
- **verify-skills CI is `continue-on-error`** — a red check_otel/verify.sh leg
  does not block a merge; only invariant-guard does. Treat a red leg as real
  work anyway.
- The `--export` eval in the launchers is positioned AFTER the `.env`
  sourcing on purpose: canonical values must overwrite a hostile dotenv, the
  same overwrite semantics `apply_telemetry_kill()` has in-process.
