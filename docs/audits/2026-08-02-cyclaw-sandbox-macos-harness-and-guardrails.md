# CyClaw Sandbox Verification — macOS Harness Port + NeMo Phase 4 Output Rail

**Date:** 2026-08-02
**Target:** `origin/main` @ `d226e14` (cgfixit/CyClaw)
**Run type:** Read-only verification (fresh venv, throwaway corpus/index artifacts, no source edits, no push/PR)

## Executive Summary

**Verdict: PASS, with two named findings to track (one confirmed security hardening gap in the macOS/Linux installer, one stale-documentation gap in the CyClaw-Sandbox skill itself) — neither blocks the current release.**
The full unit/integration suite (2599 tests) passed at 2584/2599 with 14 skipped and exactly one failure that is a known environment artifact, not a regression. All 33 invariant-guard checks, all config-guard and dep-guard checks, and both independent runtime checks (gate, harness) passed clean, and coverage measured 91.79% against an 80% gate. The macOS CI leg (`macos-latest`) is green on this exact commit, including its install/uninstall smoke test. The one real, previously-undocumented security finding is a confirmed (empirically reproduced) command-injection primitive in `macos/install-cyclaw.sh`'s unquoted heredoc when `--repo-path` points at an adversarially-named directory; a comparable-severity but structurally different primitive was also found (reasoned, not empirically tested — no Windows runner available) in the PowerShell installer's separate `$PROFILE`-writing block, which the task's own framing did not anticipate. The NeMo Phase 4 `guardrail_output` rail re-verified clean against both code and tests.

---

## Standard Phase 14 Report Block

```
CyClaw Swarm Verification Complete.
Full functionality status: PASS (with findings — see deep-dive below).
RAG pipeline (5 queries): PASS (verified via test_graph.py's full node/routing
  coverage, not via the bundled run_full_verification.py script — see note below)
  - Query 1 (vault hit): PASS (test_graph.py TestLocalLlmNode / TestRetrieveNode)
  - Query 2 (vault hit): PASS (multi-doc coverage in test_graph.py / test_hybrid_search.py)
  - Query 3 (offline best-effort / Qwen): PASS (TestOfflineBestEffortNode)
  - Query 4 (Grok API connection-only): PASS (TestGrokFallbackNode, request-shape assertions)
  - Query 5 (Claude API connection-only): PASS (TestClaudeFallbackPrompt, 10 tests mirroring Grok)
REST API surface: PASS (test_gate.py + test_gate_ops.py, 100/100)
Terminal Consoles (all 5): PASS (test_terminal_contract.py, 25/25)
Triple-Gate Online API (Grok): PASS (test_graph.py, test_due_diligence_invariants.py)
Triple-Gate Online API (Claude): PASS (test_graph.py::TestClaudeFallbackPrompt)
API Key Redaction (Grok + Claude): PASS (verified directly against gate.py
  _SECRET_PATTERNS + config.yaml redact_secrets_like; both carry the sk-ant-* pattern)
Due-Diligence Invariants: 13/13 test classes present and passing (one more than
  the "12" documented count — TestGuardrailInputAuditConvergence was added for
  the Phase 2 guardrail wiring; see Doc-Drift Notes below)
Harness Console REST API: PASS (test_harness.py, full 627-line suite passing;
  independently confirmed the guarded-route auth/CSRF/same-origin chain by
  reading harness/server.py directly)
Harness HTML Contract: PASS (functionally — see Doc-Drift Notes: the skill's
  own "no API-key affordance" description of harness.html is now stale)
Security Invariants: 33/33 (invariant-guard), 21/21 conceptual checklist items
  verified true against code (one item, #21/harness auth affordance, required
  updating the mental model rather than finding a defect)
Recommendations: see below.
```

### Test suite detail

- `GROK_API_KEY=dummy pytest tests/ -q --tb=short`: **2599 collected, 2584 passed, 1 failed, 14 skipped.**
- The one failure — `tests/test_sqlconnect_client.py::test_driver_absent` — asserts `SqlDriverNotInstalledError` is raised because "psycopg not installed in this env" (the test's own docstring). This environment installed the `postgres` extra (`psycopg`/`psycopg-binary`) as part of following the skill's own preferred `pip install -e ".[test,full]"` path, which pulls that extra in by design. CI's primary Linux/Windows lane installs from `requirements.txt` directly, which does **not** include `psycopg` (confirmed by grep), so this test passes there. This is a known, self-consistent side effect of the "full install" choice, not a regression — verified by cross-referencing `.github/workflows/ci.yml`'s own install commands.
- Coverage (CI's exact `--cov=` flag list): **91.79% total**, gate (80%) passed. Weakest module: `utils/personality_db.py` at 66% and `retrieval/vector_store.py` at 62% (both pre-existing, not touched by the two focus areas).
- `invariant-guard`: **33/33 PASS** (all six invariants + five supporting guards), including I4 audit convergence across all 9 upstream nodes (post-guardrail_output) and I6 module isolation now covering `harness` as a fourth isolated-from/isolating-of package.
- `config-guard`: **12/12 checks, 0 failures, 0 warnings.**
- `dep-guard`: **10/10 checks, 0 failures, 0 warnings** (three informational notes, all expected/documented: chromadb CVE risk-acceptance, environment.yml's intentional fastapi divergence).
- `gate_runtime_check.py` / `harness_runtime_check.py`: **both PASS** independently (import-time only, no live server).

---

## macOS Harness Port + Guardrails Deep-Dive

### 1. macOS/Linux harness installer — the shim heredoc finding

**Files read directly:** `macos/install-cyclaw.sh`, `macos/invoke-cyclaw.sh`, `macos/uninstall-cyclaw.sh`, `powershell/Install-CyClaw.ps1`, `docs/HARNESS_MACOS.md` (skimmed for the documented gap it names), `.github/workflows/ci.yml`'s macOS matrix leg.

**Finding: CONFIRMED, empirically reproduced.** `macos/install-cyclaw.sh` lines 161–167:

```bash
SHIM="$BIN_DIR/cyclaw"
cat > "$SHIM" <<EOF
#!/usr/bin/env bash
export CYCLAW_HOME="\$HOME/.CyClaw"
export CYCLAW_REPO="$REPO_DIR"
exec "\$CYCLAW_HOME/bin/invoke-cyclaw.sh" "\$@"
EOF
chmod +x "$SHIM"
```

The heredoc delimiter (`<<EOF`) is unquoted, so bash performs parameter/command
expansion on the whole body before writing it to `$SHIM`. `\$HOME`, `\$CYCLAW_HOME`,
and `\$@` are backslash-escaped and survive as literal text — but `$REPO_DIR` is
**not** escaped, so its current string value is substituted in verbatim at
heredoc-processing time. If that value contains a `"` character, the substitution
closes the `export CYCLAW_REPO="..."` string early inside the **written file**,
and anything after becomes live shell syntax the next time the shim is executed
(i.e., every time the installed `cyclaw` command runs, not just once).

`$REPO_DIR` is set two ways: the internal clone path (`$HOME_DIR/repo`, safe —
derived from `$HOME`) or, when the operator passes `--repo-path PATH`, via
`REPO_DIR="$(cd "$REPO_PATH" && pwd)"` (line 76) — the canonicalized absolute
path of whatever directory the operator points at. POSIX filenames may legally
contain `"`, `;`, backticks, or `$()` (only `/` and NUL are forbidden), so this
is a real, constructible primitive, not a theoretical one.

**I verified this end to end in the scratchpad, not just by inspection:**
1. Created a real directory named `foo"; touch INJECTED_VIA_REALDIR; echo "` on
   this filesystem and confirmed `mkdir` succeeds and the installer's own
   `cd "$REPO_PATH" && pwd` idiom resolves to that exact malicious string.
2. Reproduced the installer's heredoc pattern verbatim with a `REPO_DIR` set to
   `/tmp/innocuous"; touch PWNED; echo "`. The generated shim file's actual
   on-disk content was:
   ```bash
   export CYCLAW_REPO="/tmp/innocuous"; touch PWNED; echo ""
   ```
3. Running that generated shim executed the injected `touch PWNED` command —
   confirmed by the file's existence afterward. This is genuine, working
   command injection, not a hypothetical.

**Severity in CyClaw's documented threat model (single-operator, loopback-bound,
not a sandbox for untrusted code — `docs/THREAT_MODEL.md`):** Low-to-moderate,
not critical. Exploitation requires either (a) the operator's own directory
naming to accidentally contain shell metacharacters (unlikely in normal use),
or (b) a social-engineering scenario — e.g., a malicious archive whose
extracted top-level folder is named with the payload, with instructions to
`cd` into it and run `install-cyclaw.sh --repo-path .` — which is exactly the
kind of supply-chain step the single-operator threat model does not assume
away, since it explicitly is *not* a sandbox for untrusted input. The blast
radius is real: the injected code lands in a `chmod +x`'d file that runs on
every invocation of `cyclaw`, i.e., persistent. CI's own macOS smoke test
(`.github/workflows/ci.yml`, "macOS install-script smoke test") always calls
`--repo-path "$GITHUB_WORKSPACE"` — a benign path — so it provides zero
coverage against this class of input and would not have caught it.

**Fix shape (not implemented — reporting only, per task scope):** quote the
heredoc delimiter (`<<'EOF'`) and substitute `$REPO_DIR` via a separate,
explicitly-quoted `printf` or `sed` step instead of direct interpolation, or at
minimum reject `--repo-path` values containing `"` at the top of the script.

### 2. PowerShell comparison — partially confirms, partially refines the framing

`powershell/Install-CyClaw.ps1` line 137 uses the same code *shape* as the bash
version — `$Repo` (built the same two ways: internal safe path, or
`(Resolve-Path $RepoPath).Path` from an operator-supplied `-RepoPath`) is
interpolated into a here-string (`@"..."@`) that gets written out as a file:

```powershell
$ShimBody = @"
@echo off
set "CYCLAW_HOME=%USERPROFILE%\.CyClaw"
set "CYCLAW_REPO=$Repo"
powershell -NoProfile -ExecutionPolicy Bypass -File "%USERPROFILE%\.CyClaw\bin\Invoke-CyClaw.ps1" %*
"@
```

**This specific occurrence (line 137, writing `cyclaw.cmd`) is genuinely safe,
and I can trace exactly why, which refines rather than simply restates the
task's framing.** The written file is consumed by `cmd.exe`. `set "VAR=value"`
is cmd.exe's well-known safe-quoting idiom: as long as the value contains no
unescaped `"`, the entire quoted region — including characters like `&`, `|`,
`<`, `>` that would otherwise be cmd.exe separators — is treated as one
literal token. The *only* character that can break out of that quoting is an
embedded `"` (to close the quote early), and `"` is one of the handful of
characters Windows/NTFS structurally forbids in any filename or path
component (`< > : " / \ | ? *`). Since `$Repo` is always a real, resolved
filesystem path, it can never contain that character — the injection
primitive the bash version has (breaking a double-quoted string via an
embedded `"`) is not constructible here. **Note that semicolon and backtick —
both named in the task's framing as forbidden-by-NTFS — are actually
irrelevant to this specific risk**: neither has any special meaning to
cmd.exe's `set "..."` parsing, so their NTFS-permitted status was never the
load-bearing fact; the double-quote prohibition is what matters, and it does
hold.

**However, I found a second, structurally different injection surface in the
same file that the task's framing did not raise, and that is NOT closed by
NTFS's `"` prohibition — I am flagging this as my own additional finding,
reasoned from documented PowerShell language semantics rather than empirically
verified (no `pwsh`/Windows runner is available in this sandbox; I checked and
none is installed).** Lines 158–167 build a second here-string — the
`$PROFILE` function block — using the same `$Repo` variable inside a
**PowerShell** double-quoted string, not a batch one:

```powershell
$Block = @"
$Marker
function global:cyclaw {
    `$env:CYCLAW_HOME = "`$env:USERPROFILE\.CyClaw"
    `$env:CYCLAW_REPO = "$Repo"
    & powershell -NoProfile -ExecutionPolicy Bypass -File "`$env:USERPROFILE\.CyClaw\bin\Invoke-CyClaw.ps1" @args
}
# <<< cyclaw harness <<<
"@
```

This text is appended to the user's `$PROFILE.CurrentUserAllHosts` — a
PowerShell script that is auto-loaded on **every new PowerShell session**.
PowerShell double-quoted strings evaluate `$(...)` subexpressions inline as a
core, well-documented language feature (`about_Quoting_Rules`) — no
quote-breakout is needed at all, unlike the bash/cmd cases. If `$Repo`'s value
contains the literal substring `$(some-command)` (a folder named e.g.
`evil$(calc.exe)`), that text is written verbatim into the profile as
`$env:CYCLAW_REPO = "C:\...\evil$(calc.exe)"`. The **next time PowerShell
starts** (any new window, since this file auto-loads), the parser will
genuinely evaluate that embedded subexpression as live code. `$`, `(`, and `)`
are **not** forbidden characters in Windows/NTFS filenames, so nothing
structurally blocks this the way `"` blocks the batch-file case. If this
reasoning holds up under real testing, it would be a broader-blast-radius
variant than the bash finding — triggered by simply opening a new terminal,
not by invoking `cyclaw` — but I want to be explicit that I have **not** run
this against a real Windows/PowerShell environment; it is inferred from
PowerShell's documented string-interpolation semantics, not observed.

**Net assessment of the task's framing:** the specific comparison point named
in the prompt (line ~137, the `.cmd` shim) is confirmed safe, and correctly so
— but for a narrower reason (the `"` prohibition specifically, not `;`/backtick
generally) than stated. The broader claim "the Windows side is safe" does not
extend to the file's other here-string (line ~163), where I believe an
analogous-in-kind, differently-shaped injection risk exists and is not closed
by any NTFS restriction — flagged here as a new, unverified finding for a
human with Windows access to confirm.

### 3. macOS CI coverage — confirmed green on this exact commit

Checked via `mcp__github__actions_list`/`actions_get` (live GitHub Actions
API, not just static `ci.yml` reading): the most recent completed run on
`main` is for commit `d226e14` itself (run `30759821045`, conclusion
`success`, 2026-08-02T17:52Z). The `macos-latest` job passed **every step**,
including "macOS install-script smoke test (layout + shim + profile
integration)" (installs, verifies the shim references the checked-out repo,
verifies PATH/profile rc-file edits, then uninstalls and verifies cleanup),
the macOS-specific plain-torch install step, the RAG smoke test, and the full
test+coverage run. This smoke test always passes a benign `--repo-path`
(`$GITHUB_WORKSPACE`), so its being green is not in tension with the shim
finding above — it simply never exercises an adversarial path name.

The harness's Python code itself (`harness/`, `agentic/deepagent_github/`,
`agentic/executor/`, `agentic/fsconnect/pathsafe.py`) carries no platform
branch, consistent with the task's framing — I confirmed this by grepping for
`sys.platform`/`platform.system` references and found none outside the
already-known POSIX-only `fsconnect` security core, which is a pre-existing,
separately-documented scope boundary, not new macOS-port surface.

### 4. NeMo Phase 4 output rail (`guardrail_output`) — re-verified fresh

Re-read `graph.py`'s `guardrail_output_node` and its wiring in `build_graph()`,
plus `utils/guardrail_bridge.py::build_output_guard` and
`guardrails/integration.py::check_output`, and re-ran (not merely re-cited)
`tests/test_graph.py`'s `TestGuardrailOutputNode` and
`TestGuardrailOutputGraphIntegration` classes as part of the full suite.

Confirmed independently, not just cited from the prior review:
- **Scope gate is real and by construction, not by convention.**
  `guardrail_output_node` returns `{}` immediately unless
  `state.get("answer_model") == "local"` — verified by reading the exact
  condition (`if output_guard is None or state.get("answer_model") != "local"`)
  and by the passing `test_non_local_answer_model_is_passthrough_even_with_a_configured_guard`
  test, which asserts a `blocked: True`-returning guard is never even reached
  for `grok`/`claude`/`offline-best-effort`/`guardrail-blocked`/empty answer
  models.
- **No new router; I2 (topology=policy) preserved.** `graph.py` adds exactly
  one unconditional edge `guardrail_output -> audit_logger` — confirmed by
  `invariant-guard`'s I2 check (which enumerates every conditional-edge site
  and found only the three documented routers) and by direct reading of
  `build_graph()`'s edge list.
- **I4 (audit convergence) holds across all 9 upstream nodes** post-Phase-4 —
  confirmed by `invariant-guard`'s I4 check output ("all 9 upstream nodes
  reach audit_logger") and by `TestGuardrailOutputGraphIntegration`'s explicit
  `assert "audit_event" in result` on the blocked path.
- **Fails open by design, verified.** `test_raising_guard_fails_open` passed:
  a guard that raises `RuntimeError` yields `{}` (pass-through), matching
  `guardrail_input_node`'s identical fail-open contract.
- **Sync/offline, no LLM call.** `check_output` in `guardrails/integration.py`
  uses a token-overlap `grounding_score`/`is_possible_hallucination` heuristic
  against `config.yaml`'s `guardrails.hallucination_threshold` (0.18) — no
  network or model call, matching the documented design.
- **Module isolation (I6) intact.** `graph.py` never imports `guardrails`
  directly; the only seam is the injected `output_guard` callable built by
  `utils/guardrail_bridge.py`, confirmed by `invariant-guard`'s G-checks and
  by direct reading of the bridge file (13 lines, does exactly what its
  docstring says: returns `None` before importing `guardrails` at all when
  `guardrails.enabled` is falsy).

I found no defects in this area. This matches the "CONFIRMED_SAFE, zero
defects" characterization from the prior review — my own pass over the same
code and a fresh run of the same tests reaches the same conclusion
independently, not by trusting the prior review's citation.

### 5. A material gap in the CyClaw-Sandbox skill itself (not in CyClaw's product code)

Running the skill's own bundled `run_full_verification.py` produced a
"PARTIAL — 129/152" result with many `FAIL` lines. I traced essentially all of
them and found they are **script-side staleness in the skill, not product
regressions** — worth flagging because it means the skill's automated
verdict currently under-reports CyClaw's actual health:

- The script clones/reuses a working copy at `/tmp/CyClaw`; I confirmed that
  copy is at the **same commit** (`d226e14`) as this worktree, so the
  divergence is not a stale-clone artifact.
- Its Phase 3 mock-index-building code calls
  `from retrieval.stemmer import PorterStemmer`-shaped code that no longer
  matches `retrieval/stemmer.py`'s current API (a private `_porter()`
  accessor now, confirmed by reading the file) — this single bug cascades
  into "FAIL"ing the 5-query execution and triple-gate phases entirely
  (`BM25 index not found`), none of which reflects on the real graph/retrieval
  code, which the real `pytest` suite already exercises and passes.
- Its `sk_ant_in_config_redact` check reports FAIL despite `config.yaml`
  containing the pattern verbatim (I read it directly: line 353,
  `"sk-ant-[a-zA-Z0-9_-]{20,}"`) — a matching-logic bug in the script itself.
- Its `endpoint_POST__ops_*` checks FAIL despite `test_gate_ops.py` (part of
  the passing 2584) proving all four `/ops/*` routes work — almost certainly
  because the script's check still looks for the route decorators in
  `gate.py` directly, whereas they were extracted to `gate_ops.py` in an
  earlier refactor (documented in `CLAUDE.md`'s own module table).
- Its `terminal_grok_handler_explicit`/`terminal_claude_handler_explicit`
  checks FAIL because the script expects two literal, separately-hardcoded
  `handleConfirm(true, id, 'grok')` / `handleConfirm(true, id, 'claude')`
  strings; `terminal.html` was refactored to a data-driven loop
  (`for (const provider of availableProviders) { ... btn.addEventListener('click',
  () => handleConfirm(true, id, provider)); }`) that only renders a button for
  a provider actually usable per `gate.py`'s `_usable_online_providers()` —
  this is an *improvement* over the literal pattern the skill still expects,
  not a regression.
- One check, `harness_html_no_auth_affordance`, is a **real and accurate**
  signal, just not a product defect: `static/harness.html` now genuinely has
  an API-key input (`id="apiKey"`, line 194) and attaches an `Authorization:
  Bearer` header (line 270), and `harness/server.py` genuinely gates a
  substantial and growing list of state-changing routes (`/api/sessions`,
  `/api/soul`, `/api/model`, `/api/chat`, `/api/github/status`, and all five
  `/api/agent/*` routes) behind a `require_api_key` + same-origin +
  CSRF-token chain (`utils/auth.py`, confirmed by reading `harness/server.py`
  directly). This is fully intentional, already reflected in `CLAUDE.md`'s own
  module table ("harness's authenticated agent-run routes"), and fully tested
  (`tests/test_harness.py`'s `_auth_headers` fixture threads this through the
  whole suite, which passed 100%) — but the CyClaw-Sandbox skill's own
  `SKILL.md` Phase 11/12 text still describes harness.html as having "neither"
  an API-key affordance, and its bundled script check still expects that. This
  is exactly the drift scenario `SKILL.md` itself warns about ("a stray
  Authorization-header helper appearing later would mean the UI and the
  threat-model doc had silently diverged") — it has now happened, but on the
  *skill's* side, not the product's.

I also noticed the skill's documented `test_due_diligence_invariants.py` count
("12 invariant classes") is now 13 (an additional
`TestGuardrailInputAuditConvergence` class was added for the Phase 2 guardrail
wiring), and its documented `banned_patterns` count ("33") is stale against
the current, correctly-40-per-`CLAUDE.md` config — both minor, both consistent
with the pattern of the skill's docs/scripts lagging a fast-moving `main`.

---

## What I Could Not Verify

- **No live Ollama.** All local-LLM-path tests run through `tests/conftest.py`
  mocks; I did not stand up a real Ollama instance, so I cannot speak to
  real-world latency, real model output quality, or the documented "0%
  processing" stall failure mode beyond what the unit tests simulate.
- **No real Grok/Claude API keys.** Triple-gate verification is
  connection-only (mocked HTTP layer), per the skill's own instructions — no
  real API cost was incurred and no live xAI/Anthropic endpoint was
  contacted.
- **No real macOS or Windows runner in this sandbox.** This is a Linux
  container. The macOS CI-green claim above is based on GitHub's own hosted
  `macos-latest` runner (via the Actions API), which I did not myself
  provision — I read its recorded result, I did not reproduce it locally.
  Similarly, I could not empirically test the PowerShell `$PROFILE`
  subexpression-injection finding (section 2 above) — no Windows/`pwsh`
  environment is available here (confirmed: `command -v pwsh` found nothing).
  That finding rests on documented PowerShell language semantics, not
  execution.
- **No live HuggingFace Hub access.** `huggingface.co` is not in this
  sandbox's proxy allowlist (confirmed via the proxy status endpoint: 403 on
  `download.pytorch.org` and, separately, on the HF metadata fetch
  `retrieval/indexer.py` triggers). This meant I could not build a *real*
  ChromaDB+embeddings index or run `tests/ci_rag_smoke.py` end-to-end; the
  bundled sandbox script's own mock-embedding path was also broken for
  unrelated reasons (see Section 5) and I did not attempt to fix it, per the
  read-only-verification scope. The real unit-test suite (which mocks these
  dependencies entirely, per `tests/conftest.py`) does not depend on this and
  ran/passed in full.
- **No CPU-only torch wheel.** `download.pytorch.org`'s `/whl/cpu` index is
  proxy-denied in this sandbox (confirmed via the proxy status endpoint's
  `recentRelayFailures`), so I installed the default PyPI `torch==2.13.0`
  (CUDA-bundled) build instead of the pinned `+cpu` variant. This is a
  reproducibility deviation from the documented install order, not a
  functional gap — CPU inference works identically, `torch.cuda.is_available()`
  correctly reports `False`, and this only affects disk footprint /
  dependency-pin fidelity in *this verification environment*, not the
  product's own `constraints.txt` pin (which `dep-guard` confirmed is still
  correctly `torch==2.13.0+cpu` in the repo itself).
- **`verification_report.json`** from the bundled script wrote to `/tmp/CyClaw/`
  rather than the current worktree (the script operates against its own
  `/tmp/CyClaw` clone, confirmed to be at the same commit) — noted for
  completeness, not treated as a finding.

---

## Recommendations (report only — not implemented)

1. **Harden `macos/install-cyclaw.sh`'s shim-writing heredoc.** Quote the
   delimiter (`<<'EOF'`) and substitute `$REPO_DIR` through an explicitly
   quoted/escaped step (e.g. `printf '%s\n' | sed` with a safe replacement, or
   validate `--repo-path` rejects embedded `"` up front). Low urgency given the
   single-operator threat model, but a two-line fix for a real primitive.
2. **Have someone with Windows access verify (or refute) the `$PROFILE`
   subexpression-injection reasoning in section 2** before treating the
   PowerShell installer as fully safe. If confirmed, the same class of fix
   (avoid raw interpolation of an operator-controlled path into a
   double-quoted PowerShell string that gets persisted and later re-parsed)
   applies there too, and arguably with higher urgency given the
   every-new-shell trigger.
3. **Add an adversarial-path-name case to the macOS/Windows install smoke
   tests** (a directory name containing `"`/`$(...)`) so this class of
   regression would be caught by CI going forward, rather than only by manual
   audit.
4. **Refresh `.claude/skills/CyClaw-Sandbox/run_full_verification.py` and
   `SKILL.md`** against current `main`: the stemmer API mismatch, the
   gate_ops-extraction-unaware endpoint checks, the hardcoded
   two-button terminal.html assertions, and the harness.html
   "no API-key affordance" claim are all stale. Left as-is, the skill will
   keep reporting a misleadingly low PASS rate on a healthy `main`, which
   risks an operator learning to discount its output.
5. **Update `SKILL.md`'s "12 invariant classes" and "33 banned injection
   patterns" counts** to 13 and 40 respectively, matching current `main` (both
   already correctly stated in `CLAUDE.md`).

---

## Environment Notes

- Python 3.12.3 venv built fresh in the scratchpad; full dependency install
  via `pip install -e ".[test,full]"` succeeded directly against PyPI (no
  `requirements.txt`/`constraints.txt`/custom-index install was needed for
  this path, since `pyproject.toml` extras resolve against plain PyPI).
- Retrieval index and corpus artifacts built/attempted only under this
  worktree's `data/corpus/`/`index/` (throwaway; gitignored) — no tracked
  source file was modified.
- No `remaining_work.md`, `CLAUDE.md`, or any file outside `docs/audits/` and
  throwaway test artifacts was touched.
