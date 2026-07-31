# GitHub Write Enablement — procedure and security review

**Status: NOT ENABLED.** `agentic/writer.py` ships `EXECUTION_ENABLED = False`
and `config.yaml` ships three further gates closed. Nothing in this repository
can open a pull request today. This document is the procedure for changing
that, and the checklist that must be filed first.

It is the GitHub analogue of `FSCONNECT_WRITE_ENABLEMENT_PLAYBOOK.md` +
`FSCONNECT_SECURITY_REVIEW_CHECKLIST.md`, and exists for the same reason: the
code half of an enablement is reviewable in a diff, and the operational half is
not. `DEEP_AGENT_HARNESS_PHASES_6_9.md` independently requires "a separate
human security review for any request to add shell, host filesystem, GitHub
mutation, or source-tree application." A GitHub mutation is the literal trigger.

---

## What P10 shipped, and what it deliberately did not

**Shipped:** `execute_write()` is implemented for `pr_create` (always
`--draft`), and `RepoWorkspaceTools.push_branch()` can push one `claude/`
branch to origin. Both are fully tested, including against a real local git
remote.

**Deliberately not shipped:** the flag flip. P10 implemented the capability and
left it disarmed, because arming it is the step this repo reserves for a human
with a filed checklist — not for the change that builds the machinery.

---

## The gate chain, in the order it is evaluated

A write requires **all six** of these. Five are config or per-call; one is code.

| # | Gate | Where | Ships as | Fails closed? |
|---|---|---|---|---|
| 0 | `agentic.enabled` (the layer's master switch) | `config.yaml` | `false` | yes |
| 1 | `EXECUTION_ENABLED` | `agentic/writer.py` | `False` | yes |
| 2 | `agentic.mode == "write"` | `config.yaml` | `"read"` | yes |
| 3 | `agentic.writes_enabled` | `config.yaml` | `false` | yes |
| 4 | a non-empty human `reason` | per call | — | yes |
| 5 | `confirm is True` | per call | — | yes |

Gate 0 was, until an external review of this document's own claim caught it,
enforced only by the CLI's own `_disabled_noop()` short-circuit — the prose
here said "the CLI no-ops entirely," which was true for the CLI but silently
NOT true for a direct call into `plan_write()`/`execute_write()`, a
programmatic boundary the CLI does not gate on anyone's behalf. It is now
enforced inside `_require_gates()` itself, ahead of gates 2–5, so it holds
regardless of caller.

`plan_write()` runs gates 0, 2, 3, 4, 5 (everything but the code-level
`EXECUTION_ENABLED`, which only `execute_write()` checks, first, before even
looking at the plan). `execute_write()` runs all six, including a FRESH
`confirm` its own caller must supply -- `plan_write()`'s `confirm` does not
carry forward via the plan dict, deliberately: a boolean baked into
hand-buildable, JSON round-trippable data would be exactly as forgeable as
manufacturing it internally, which is what an earlier version of this function
did. That matters: before P10 the numbered gates lived only in the planner, so
once the flag flipped, *holding a plan dict* would have become the authority to
write. A plan is data — hand-buildable, JSON round-trippable, able to cross a
process boundary. It is no longer authority.

---

## Enablement procedure

Do these in order. Each step is verifiable, and every step before the last is
reversible by editing one line back.

1. **Confirm `gh` is authenticated as the identity you intend.**
   `gh auth status`. The write path passes no credential of its own — it
   inherits whatever `gh` resolves. Whoever `gh` says you are is who opens the
   PR.
2. **Confirm push credentials exist for `git`, separately.** `push_branch()`
   runs under a four-name environment allowlist that deliberately excludes
   `GH_TOKEN`/`GITHUB_TOKEN`, so it authenticates only via a HOME-resident
   credential helper. `gh auth setup-git` configures one. **A token-only
   environment with no helper will fail** — that is expected, not a bug. It is
   not widened, because that environment is shared with the executor that runs
   model-proposed check commands, and a GitHub token there is an exfiltration
   path.
3. **Dry-run first.** With gates 2–3 still closed, call `plan_write(...)` and
   read the `would_run` argv. Confirm `--draft` is present, `--repo` is your
   repo, and `--head` is the `claude/` branch you expect.
4. **File the checklist below.** Signed, dated, kept with the repo.
5. **Open the config gates** (`agentic.enabled: true` is presumably already on
   if you got this far; `mode: "write"`, `writes_enabled: true`). Still nothing
   executes — gate 1 (`EXECUTION_ENABLED`) is code, not config.
6. **Flip `EXECUTION_ENABLED` to `True`.** This is the last step and the only
   irreversible-in-effect one.
7. **Rehearse the rollback before you rely on it:** set it back to `False` and
   confirm `execute_write` refuses with `failed_gate: "execution_enabled"`.

---

## Security review checklist

*Flipping the write flag without a completed, filed copy of this checklist is
an unauthorized change.*

- [ ] **A. Reachability.** No `/ops/*` endpoint, no harness route, and no CLI
      subcommand reaches `execute_write`. Verified: `utils/ops_runner.py`'s
      `_AGENTIC_ACTIONS` carries no write action, and `agentic/cli.py` exposes
      no write subcommand. If either changes, this checklist is void and must be
      re-run — that would make a GitHub mutation network-triggerable.
- [ ] **B. Draft-only.** `_build_write_argv`'s `pr_create` branch still ends in
      `--draft`, and `tests/test_agentic_writer.py` still asserts the argv as an
      exact list. That assertion is the only thing pinning draft-ness.
- [ ] **C. Head branch is explicit.** `--head` is required and constrained to
      `claude/*`. Without it `gh` infers the head from the process's working
      directory, which on the ops_runner path is the operator's own checkout.
- [ ] **D. Repo targeting.** `execute_write` refuses a plan whose `repo` differs
      from the configured one. The config is authoritative; the plan is advisory.
- [ ] **E. Plan integrity.** `execute_write` rebuilds the argv from the plan's
      own `params` and refuses on mismatch with `would_run`. It never executes
      the list it was handed.
- [ ] **F. No retry.** A write is attempted exactly once. A timeout is reported
      as INDETERMINATE rather than retried, because both of `run_read`'s retry
      branches fire after the request has already left the machine and could
      duplicate an accepted mutation.
- [ ] **G. Push scoping.** `push_branch` rejects every branch outside
      `claude/*`, enforced by test rather than convention — nothing else in the
      repo statically prevents a push elsewhere.
- [ ] **H. Known gap, accepted or closed:** `agentic/executor`'s verification
      checks run operator-supplied argv with cwd pinned to the clone. A
      checks-file entry of `{"argv": ["git", "push", ...]}` bypasses the
      `claude/*` scoping and the `allow_git_write_tools` gate entirely. The
      argv are operator-supplied, but that is not the mitigation it first
      appears to be: the *code that argv executes* runs against a worktree
      containing model-authored writes, so `{"argv": ["pytest"]}` — the
      shipped default profile — already executes model-authored content via
      normal test collection. The actual mitigation is narrower: the
      checks-file itself is local-only and operator-authored (no `/ops/*`
      route or harness API accepts raw argv; the harness sends check-profile
      **names** against a fixed allow-list — see
      `docs/THREAT_MODEL.md`'s outbound/execution-surface sections). Decide
      explicitly: accept that scope, or close before enabling.
- [ ] **I. Blast radius understood.** With the flag on and gates 2–3 open, this
      code can push a `claude/*` branch and open a draft PR against the
      configured repo, as the authenticated `gh` identity. It cannot push to
      `main`, cannot force-push, and cannot delete anything.
- [ ] **J. Master switch enforced in code, not just the CLI.**
      `_require_gates()` checks `agentic.enabled` first, ahead of every other
      gate. A direct call into `plan_write()`/`execute_write()` — bypassing the
      CLI's own `_disabled_noop()` — cannot skip it. (This was NOT true before
      an external review caught the gap; see the gate-chain section above.)
- [ ] **K. Confirm is never inherited from a plan.** `execute_write()` requires
      its own caller to supply a fresh `confirm=True`; it neither reads a
      `confirm` field off the plan (none exists) nor manufactures one
      internally. (Also not true before the same review.)

**Sign-off:** ______________________  **Date:** ____________

*(no sign-off, no flag flip)*

---

## What this does not change

`pr_comment` and `issue_comment` remain plan-only — describable, not
executable, refused by name at the execution boundary. The deepagent tool
surface still hard-refuses GitHub writes independently
(`deepagent_github/permissions.py`). `config.yaml`'s
`deepagent_github.allow_github_writes` remains `false` and remains a separate
question from this one.
