# Sprint Plan: Arm the Container Supply-Chain Gate

**Context:** PR #993 (`[ci] - cut CI minutes on Trivy, DevSkim, CodeQL`)
**Prepared:** 2026-08-17 · **Team:** 1 engineer (cgfixit), nights/weekends
**Sprint length:** 2 weeks · **Plan to ~70% capacity**

> **Sprint Goal:** No CyClaw image reaches GHCR without a recorded, threshold-enforced
> vulnerability scan of *that exact image*, and no scan can silently no-op.

---

## Premise correction (read first)

`aquasecurity/trivy-action`'s `exit-code` input defaults to `"0"`. Neither Trivy job in
`.github/workflows/trivy.yml` sets it. **The container scan has never failed CI.** It has
always been notify-only (SARIF → GitHub code scanning). PR #993 did not downgrade a gate —
it narrowed *when* the notification is produced, and introduced one bug that can suppress
the notification entirely.

The gate that actually matters does not exist yet: `publish-ghcr.yml` (triggered by
`push: tags: v*`) contains **no Trivy step**, and `trivy.yml` only triggers on
`push: branches: [main]`, `pull_request`, and a weekly cron. The published artifact is
never scanned as a published artifact.

---

## Findings (ranked)

| ID | Finding | Severity | Evidence |
|----|---------|----------|----------|
| **F1** | `publish-ghcr.yml` builds and pushes `ghcr.io/cgfixit/cyclaw` with zero vulnerability scan. `trivy.yml`'s `push` trigger is branch-scoped (`main`) and does not match `refs/tags/v*`. | **High** | `publish-ghcr.yml` L21-25, L80-97; `trivy.yml` L4-5 |
| **F2** | SARIF upload changed from `if: always()` to `if: steps.changes.outputs.docker_changed == 'true'`. GitHub implicitly ANDs `success()` into any `if` lacking a status function → a failing Trivy step that still produced SARIF uploads nothing, and code scanning keeps serving the *previous* run's results for category `trivy-image`. | **Medium** | PR #993 diff, `trivy.yml` L117-121. Codex bot flagged this as P2. |
| **F3** | The `Detect image-relevant changes` step **fails open**. `non_docs=$(git diff … \| grep -vE … \|\| true)` — any failure of the diff (bad base ref, shallow history, checkout-action behavior change) yields an empty string → `docker_changed=false` → every Docker step skipped → **job reports green with zero container scanning**. `fetch-depth: 0` mitigates the known case today; the *shape* is still fail-open, which contradicts INVARIANTS.md Rule 6. | **Medium** | PR #993 diff, `trivy.yml` L74-88 |
| **F4** | `.dockerignore` excludes `data/personality/` and `data/agentic/` but **not** `config.yaml` or `data/corpus/`. `Dockerfile` L57 is `COPY . .`. Docker's `*.md` pattern matches root-level only (`*` does not cross `/`), so `data/corpus/*.md` and `*.txt` are not excluded either. `docs/DOCKER.md` ("What is *not* in the image") and `publish-ghcr.yml` L13 both claim otherwise. | **Medium** | `.dockerignore`, `Dockerfile` L57, `docs/DOCKER.md` L27-38 |

**Credit where due:** `publish-ghcr.yml` already sets `provenance: true` and `sbom: true`.
The published images carry SBOM attestations, so retroactive scanning of already-published
tags is a single `trivy image` invocation — F1 is cheap to close *because* that groundwork exists.
The digest-pinned base image, multi-stage split, non-root UID, and the comment explaining
*why* `cache-to` is isolated are all above the median for a solo project.

---

## Sprint Backlog

| Pri | Item | Est | Blocks | Notes |
|-----|------|-----|--------|-------|
| **P0** | **T1** — Merge Codex's fix: `if: always() && steps.changes.outputs.docker_changed == 'true'` on the SARIF upload step | 15 min | — | Unblocks #993 merge |
| **P0** | **T2** — Make F3 fail *closed*: on any diff error, set `docker_changed=true`. Add `set -o pipefail`; branch on `git diff` exit status explicitly rather than on empty output | 45 min | — | Same PR as T1 |
| **P0** | **T3** — Add a Trivy image scan to `publish-ghcr.yml`, between build-and-load and push. Two-step pattern: scan #1 `exit-code: 0` → SARIF (always uploaded); scan #2 `exit-code: 1`, `severity: CRITICAL,HIGH`, `ignore-unfixed: true`, `skip-setup-trivy: true` → **blocks the push** | 3 h | T4 | Separate PR. This is the gate. |
| **P0** | **T4** — Baseline the current image: `trivy image --severity CRITICAL,HIGH --ignore-unfixed ghcr.io/cgfixit/cyclaw:1.9.0`. Count findings. Decide the initial threshold from data, not from hope | 1 h | — | Must precede T3 |
| **P1** | **T5** — Fix F4: add `config.yaml` and `data/corpus/` to `.dockerignore`; correct `docs/DOCKER.md` and `publish-ghcr.yml` L13. Verify with `docker run --rm --entrypoint sh <img> -c 'ls -la /app; ls /app/data'` | 1 h | — | Doc-vs-reality drift is its own class of bug |
| **P1** | **T6** — Create `.trivyignore` with **dated, justified, expiring** entries only. Format: `CVE-ID  # <why> — expires YYYY-MM-DD` | 1 h | T4 | The escape valve that keeps T3 from getting reverted |
| **P1** | **T7** — Add a scheduled `trivy image` scan of the *published* `latest` + newest semver tag (weekly, `exit-code: 0`, SARIF). Catches CVEs disclosed after publish | 1 h | T3 | Base image is digest-pinned to 2026-07-27 — drift is guaranteed |
| **P2** | **T8** — Slim the runtime image. `torch==2.13.0+cpu` in the runtime stage is the single largest CVE surface. Evaluate whether the runtime needs full torch or just the `sentence-transformers` inference path | 6 h+ | — | Stretch. Real work, real payoff, do not start it this sprint if T1-T7 slip |
| **P2** | **T9** — Add `docker scout`/`grype` as a second opinion, or `cosign sign` the published image | 3 h | T3 | Stretch. Nice-to-have for the MSP conversation |

**Sprint load:** ~8 h P0 + ~3 h P1 = **11 h committed**, 9 h stretch. Adjust to your real availability; do not commit the P2 row.

---

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| T4 reveals 100+ HIGH/CRITICAL findings in the torch layer | T3 becomes politically impossible; you revert the gate and learn "scanners are noise" | Start `exit-code: 1` at **CRITICAL only, `ignore-unfixed: true`**. Ratchet to HIGH after T8. A gate you keep beats a gate you revert. |
| `.trivyignore` becomes a permanent dumping ground | The gate is theater | Every entry carries an expiry date. Add a CI check that fails on an expired entry. |
| T8 (slimming) becomes the whole sprint | Nothing ships | It is P2 for exactly this reason. Timebox or defer. |
| Blocking publish on a scan makes releases feel risky | You stop tagging releases | The gate is on `push: tags:` only — a failed scan means no image published, not a broken main. That is the correct failure mode. |

---

## Definition of Done

- [ ] `if: always() && …` on both SARIF upload steps
- [ ] Diff-detection step fails closed (proven by a deliberate bad-base-ref test run)
- [ ] `publish-ghcr.yml` cannot push an image with a CRITICAL, fixable CVE
- [ ] A baseline finding count is written down somewhere durable (this doc, or `docs/audits/`)
- [ ] `.dockerignore` matches what `docs/DOCKER.md` claims — verified by inspecting a built image, not by reading the file
- [ ] `.trivyignore` entries all carry a justification and an expiry
- [ ] `docs/DOCKER.md` "Security posture (must preserve)" table gains a **supply chain** row

---

## Why this is cheaper now than later

The workflow edits are ~4 hours. That number does not change.

What changes is **T4's output**. A base image digest-pinned on 2026-07-27, plus a torch
wheel, accumulates disclosed CVEs monotonically. Arm the gate at 12 findings and you triage
12. Arm it at 180 and you will not triage 180 — you will set `exit-code: 0` and tell yourself
it is temporary.

The second thing that changes is **who is downstream**. Right now the answer is: you. Once a
Metro Atlanta MSP has pulled `ghcr.io/cgfixit/cyclaw:1.9.x` into a law firm, arming the gate
stops being a workflow edit and becomes an incident-response conversation with a customer who
trusted a README that said "zero-trust, production-grade."

---

## Related

- `.github/workflows/trivy.yml`, `.github/workflows/publish-ghcr.yml`
- `Dockerfile`, `.dockerignore`, `docker-compose.yml`
- `docs/DOCKER.md`, `docs/THREAT_MODEL.md`, `INVARIANTS.md`
- PR #993 · Codex bot P2 review comment
