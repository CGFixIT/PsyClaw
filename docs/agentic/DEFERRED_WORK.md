# Agentic layer — deferred work

Work that is designed, verified where possible, and deliberately **not** shipped
yet. Each entry records what exists, why it is on hold, and the concrete trigger
for picking it up — so a later session can resume it without re-deriving the
context.

This is not a wishlist. Nothing goes here without a specific reason for the
delay and a specific condition for lifting it.

---

## D1 — CI smoke test for `real-repo-run` (shipped 2026-08-02)

**Status:** landed. `real-repo-run-smoke` in `.github/workflows/ci.yml`,
backed by `tests/test_agentic_real_repo_run_smoke.py`. The design below is
the as-shipped design; kept for context rather than deleted, per this
document's own "record what exists, why it was on hold" convention.

Confirmed clean against `actionlint` and `zizmor --offline --min-severity=high`
before landing, per the trigger condition below.

**What it is.** A `real-repo-run-smoke` job mirroring the existing
`ollama-mock-smoke` pattern (real socket, mocked content, lightweight deps —
no torch/chromadb). It drives one full `plan → patch → verify` cycle through
the real `agentic.cli` against an instant-answering mock model, a fake `gh`,
and a real local bare git repo, then asserts the run reaches
`pending_decision` with the expected `changed_files`.

**Why it is worth having.** The emulated rehearsal on 2026-08-02 found two real
defects in this exact wiring — the planner timeout never reaching the model
client, and the `ops_runner` ceiling not scaling with it once it did. Both were
invisible to the existing suite because every test builds `LocalProposerClient`
with an `httpx.MockTransport`, which never touches the socket or the timeout.
This job would have caught both on the PR that introduced them.

**Why it is on hold.** Owner decision: a CI-workflow change is its own
reviewable concern and should not ride along with the code PRs it was found
alongside.

**Where the draft lives.** It is NOT in the repo. It was produced in a session
scratchpad and must be regenerated or re-pasted when this is picked up; the
design above is the durable part. Regenerating it is cheap — the verification
harness that produced it is described in the same session's rehearsal notes.

**Known gap in the draft.** `actionlint` and `zizmor` are not installed in the
sandbox where it was written, so the YAML passed a plain `yaml.safe_load` parse
and a full local execution of its own bash/python logic, but NOT this repo's
own CI-lint gate. That check must run before it is committed — the repo's
`workflow-lint` job runs both, and a workflow that fails them blocks every
other job.

**Deliberately scoped out of the draft.** It uses an instant-answering mock,
not a latency-emulating one: the job proves the *wiring*, not model quality or
realistic timing. A CI runner's CPU says nothing about the operator's own
hardware, and downloading a real model would make the job slow and flaky in
exchange for an answer it cannot actually give.

**Trigger to pick this up:** after the code work it was found alongside has
landed (the P0 timeout fixes and the P2 code-shape scanner, both on PR #735 as
of this writing) and that PR is merged. Then: regenerate the draft, run
`actionlint` + `zizmor --offline --min-severity=high` against it, and open it
as its own small PR.
