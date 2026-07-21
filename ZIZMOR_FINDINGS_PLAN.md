# zizmor Medium-Severity Findings — Remediation Plan

**Status:** Planning only — no fixes applied yet. Deferred from PR #596 (merged
2026-07-21), which added the `workflow-lint` CI job (`actionlint` + `zizmor`)
and fixed the 2 findings that were High severity at the time. This doc tracks
the remaining Medium-severity backlog `workflow-lint` deliberately does not
gate on (`--min-severity=high` in `.github/workflows/ci.yml`), so a future PR
can work through it without blocking every PR in the meantime.

**Re-derive this list before starting work** — it may have drifted since this
doc was written:

```bash
zizmor --offline .github/workflows/
```

State captured 2026-07-21 against `main` (post-#596): **25 findings, all
Medium severity** — 23 `artipacked`, 2 `excessive-permissions`. 0 High, 0 Low
(the Low/Informational findings zizmor also reports were filtered out of this
plan; re-run without a severity filter to see everything).

---

## Finding class 1 — `artipacked` (23 instances)

**What it flags:** an `actions/checkout` step that does not set
`persist-credentials: false`. By default, `actions/checkout` configures a git
credential helper using the job's `GITHUB_TOKEN` and leaves it active in the
workspace for the rest of the job. Any later step in that job — including a
compromised dependency, a malicious test fixture, or (for jobs that check out
a PR) an untrusted PR's own code — can read that credential from the git
config and use it, or it can leak into an uploaded artifact/log. Setting
`persist-credentials: false` removes the credential immediately after
checkout; anything in the job that *needs* git to authenticate afterward
(a push, an authenticated fetch of a private ref) must then pass its own
token explicitly.

**zizmor's own confidence on all 23:** Low. This is a broad, generic
heuristic — it fires on every checkout that doesn't set the flag, regardless
of whether that job could plausibly need the credential. Low confidence is
why this is Medium severity, not High, and why it wasn't treated as urgent
alongside the two High findings #596 already fixed (a real cache-poisoning
vector and two real template-injection instances with a working exploit
repro).

### Full list, with the risk-relevant question already answered where checked

The fix is mechanical (`with: persist-credentials: false` added to the
`actions/checkout` step) **only if the job never needs git-level
authentication after that step**. `permissions:` block content bounds what
the *token* can do, but a leftover credential is a separate, job-local risk —
check the job's own steps, not just its declared permissions.

| # | File : Line | Job | `permissions:` (declared) | Verified safe to add the flag? |
|---|---|---|---|---|
| 1 | `ci.yml:50` | `workflow-lint` | `contents: read` | Yes — lints only, no git writes |
| 2 | `ci.yml:105` | `dependency-review` | `contents: read`, `pull-requests: read` | Yes — read-only PR diff analysis |
| 3 | `ci.yml:124` | `test` | inherited `contents: read` | Yes — installs deps, runs pytest, uploads coverage artifact only |
| 4 | `ci.yml:266` | `ollama-mock-smoke` | `contents: read` | Yes — pytest only |
| 5 | `ci.yml:302` | `deepagents-harness` | `contents: read` | Yes — installs deps, runs pytest, `pip-audit` |
| 6 | `ci.yml:377` | `postgres-backend` | inherited `contents: read` | Yes — pytest against a service container |
| 7 | `ci.yml:427` | `discover-skills` | inherited `contents: read` | Yes — `find`/`jq` over the checked-out tree only |
| 8 | `ci.yml:477` | `verify-skills` | inherited `contents: read` | Yes — runs skill `verify.sh`/`smoke.sh` scripts; none of them push |
| 9 | `claude.yml:35` | `claude` | `contents: read`, `pull-requests: write`, `issues: write` | **Verify, don't assume.** `permissions.contents` is already read-only, so the injected token structurally cannot push regardless of credential persistence — but `anthropics/claude-code-action` is a third-party action; confirm it authenticates its own PR-comment/review calls via the `github_token` *input* it's given (it does, per its own README) rather than shelling out to `git` using the ambient credential, before flipping this one. If in doubt, leave this one alone rather than risk breaking the only workflow that posts PR feedback. |
| 10 | `codeql.yml:43` | `analyze` | `security-events: write` (job-level) | Yes — CodeQL scan + SARIF upload via the CodeQL action, no git push |
| 11 | `codex-skills.yml:29` | `verify` | check file | Likely yes — mirrors `ci.yml`'s `verify-skills` pattern for `.codex/skills/*` |
| 12 | `copilot-setup-steps.yml:23` | `copilot-setup-steps` | check file | Likely yes — environment setup only |
| 13 | `defender-for-devops.yml:60` | `MSDO` | check file | Likely yes — Microsoft Defender scan, SARIF upload |
| 14 | `devskim.yml:32` | `devskim` | `contents: read`, `security-events: write` | Yes — scan + SARIF upload (this job's *own* cache-poisoning issue was already fixed in #596; this is a separate, unrelated finding on the same file) |
| 15 | `fortify.yml:76` | `Fortify-AST-Scan` | see finding class 2 below — this job also has the two `excessive-permissions` findings, fix together |
| 16 | `gitleaks.yml:54` | `gitleaks` | check file | Likely yes — secret scan only |
| 17 | `lint.yml:52` | `lint` | `contents: read` | Yes, but this step uses `fetch-depth: 0` (full history) for the `git merge-base` diff in a later step — confirm `persist-credentials: false` doesn't interact badly with the *later* `git fetch origin "$BASE_REF"` in the same job (it shouldn't: that fetch is read-only and anonymous HTTPS works fine on a public repo without the persisted token, but this is exactly the kind of job worth actually testing rather than assuming) |
| 18 | `pip-audit.yml:37` | `verify-install` | check file | Likely yes — install + smoke import check |
| 19 | `pip-audit.yml:103` | `scan` | check file | Likely yes — `pip-audit` scan |
| 20 | `python-package-conda.yml:28` | `ci` | check file | Uses `fetch-depth: 0` — same caveat as #17; confirm no later git operation depends on the persisted credential |
| 21 | `python-publish.yml:25` | `release-build` | `contents: read` (workflow-level) | Yes — `python -m build` + upload-artifact only; the actual publish step (`pypi-publish` job, OIDC trusted publishing) is a **separate job** and doesn't even check out the repo |
| 22 | `semgrep.yml:29` | `semgrep` | check file | Likely yes — SAST scan |
| 23 | `trivy.yml:29` | `trivy` | check file | Likely yes — filesystem vulnerability scan |

"check file" = not re-verified while writing this plan; read the job's
`permissions:` block and full step list before fixing, same as the ones
already checked. None of the 23 were found to declare `contents: write` where
checked, which is the strongest structural signal that the flag is safe to
add — but confirm per-job rather than pattern-matching from this table alone,
especially for #9, #17, and #20 above.

### Proposed fix pattern

```yaml
- uses: actions/checkout@<sha>  # existing pin, unchanged
  with:
    persist-credentials: false
```

For jobs that already have a `with:` block (several do, e.g. `fetch-depth:
0`), add the key to the existing block rather than duplicating `with:`.

### Suggested execution shape for the follow-up PR

1. Fix the 20 straightforward ones (everything in the table except #9, #17,
   #20, which need the explicit checks noted above) in one commit.
2. Verify each of #9/#17/#20 individually — read the full job, confirm no
   later step needs an authenticated git operation — then fix or explicitly
   leave-with-a-comment.
3. Re-run `zizmor --offline .github/workflows/` and confirm the `artipacked`
   count drops to 0 (or to a documented, intentional remainder).
4. Push each real workflow once (or via `workflow_dispatch` if available) to
   confirm nothing broke — a YAML-valid change to `persist-credentials` is
   low-risk but this is still CI infrastructure; don't merge on YAML-parses
   alone.

---

## Finding class 2 — `excessive-permissions` (2 instances, both in `fortify.yml`)

**What it flags:** `fortify.yml:16` and `fortify.yml:43` — "default
permissions used due to no `permissions:` block." When a workflow (or a job
within it) declares no explicit `permissions:` key, GitHub Actions grants the
*default* token permissions for the repository, which — depending on the
repo's own Settings → Actions → Workflow permissions setting — can be broad
read/write access to contents, issues, PRs, etc., not just what the job
actually needs.

### Fix

Read `.github/workflows/fortify.yml` in full, determine exactly what the
`Fortify-AST-Scan`/`config-check` jobs need (almost certainly just `contents:
read`, plus `security-events: write` if it uploads SARIF like the other
scanners in this repo — check whether it does), and add an explicit
`permissions:` block scoped to that, either at the workflow level (applies to
every job that doesn't override it) or per-job to match how `codeql.yml` and
`devskim.yml` already do it.

### Suggested execution shape

Small, single-file, single-commit fix. Verify with:
```bash
zizmor --offline .github/workflows/fortify.yml
```
should show 0 `excessive-permissions` findings after.

---

## Out of scope for this plan

- The 2 High-severity findings from PR #596 (`devskim.yml` cache-poisoning,
  `lint.yml`/`ci.yml` template-injection) are already fixed on `main`.
- Any Low/Informational-severity zizmor findings not enumerated above — check
  a fresh `zizmor --offline` run for the current full list before assuming
  this plan is exhaustive; new findings can appear as the workflow files
  change, and zizmor itself gets updated periodically (current pin: `zizmor==1.27.0`,
  installed fresh each `workflow-lint` run from `.github/workflows/ci.yml`, not
  version-locked anywhere else — check the current version if these numbers
  look off).
- `--min-severity=high` in `ci.yml`'s `workflow-lint` job is what allows this
  backlog to exist without blocking merges. Do not remove that flag as a
  side effect of this cleanup unless the backlog above is actually fully
  resolved first — removing it prematurely reintroduces the exact
  "blocks every future PR on unrelated pre-existing debt" problem #596's
  `af258e1` commit fixed.
