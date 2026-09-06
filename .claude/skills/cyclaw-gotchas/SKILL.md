---
name: cyclaw-gotchas
description: >-
  Session-tested traps for working on CyClaw from a Claude Code sandbox, plus
  a driver that does the setup the right way. Load before: installing deps or
  building a venv, running pytest, launching or probing gate.py, opening or
  driving a PR to green, answering a Codex/Copilot review, scheduling a PR
  check-in, running doc-sync or verify-deps, touching the harness chat/agent
  concurrency gates, or when something "hangs", "won't install", "prints no
  test summary", or "says 409 busy". Every command here was run in this
  container; every gotcha names the session, PR, or file that proved it.
---

# cyclaw-gotchas

Paths are relative to the repo root. The driver is
`.claude/skills/cyclaw-gotchas/driver.sh`; `SKILL.md` is its man page plus
the lessons the driver exists to encode. CLAUDE.md §4 is the canonical trap
list for the *codebase*; this file is the trap list for the *session*: the
sandbox, the process, the reviewers, and the runtime behaviours that look
like bugs but are not. Where the two overlap, CLAUDE.md wins; this file
links rather than restates.

## Prerequisites

Nothing to `apt-get`. The default cloud image (Ubuntu 24.04) already has
`/usr/bin/python3.12`, `ruff`, `uv`, `docker`, `curl`, and `git`. What it does
**not** have is any CyClaw dependency in any interpreter, and its egress proxy
denies two hosts CyClaw's install and index paths need:

| Host | Denied? | Consequence |
|---|---|---|
| `pypi.org`, `files.pythonhosted.org` | no | pip works |
| `download.pytorch.org` | **yes** (org policy, 403 on CONNECT) | `torch==2.13.0+cpu` is unresolvable |
| `huggingface.co` | **yes** | `all-MiniLM-L6-v2` cannot download, so the index cannot be built |
| `github.com` (git over the proxy) | no | fetch/push work |

Check the live picture before assuming: `bash .claude/skills/cyclaw-gotchas/driver.sh inventory`.

## Build (the venv)

```bash
bash .claude/skills/cyclaw-gotchas/driver.sh venv
```

What it does, and why each step is the way it is (all learned by failing first,
session 2026-09-06):

1. `python3.12 -m venv /root/.venv-cyclaw-312` -- outside the repo, so no
   `.gitignore` entry and no chance of `ruff`/pytest walking site-packages.
   Bare `python3` is 3.11 and always will be on this image; do not
   `update-alternatives` it (the harness hooks resolve through it).
2. Tries `torch==2.13.0+cpu` from the CPU index with `--retries 1`. On this
   proxy that fails in seconds, not minutes.
3. Falls back to plain `torch==2.13.0` from PyPI **with** its dependency tree.
   `--no-deps` was tried and is a dead end: the Linux wheel dlopens
   `libcudart.so.13` at import and raises before anything else loads. The
   fallback costs ~2 GB and ~7 minutes; disk had 30 GB free.
4. Installs `requirements.txt` + `requirements-test.txt` with the `torch==`
   and `--extra-index-url` lines stripped (same shape as the macOS recipe in
   CLAUDE.md §8), constrained by a torch-stripped copy of `constraints.txt`.

Re-running is idempotent: it exits 0 immediately once
`import torch, chromadb, langgraph, pytest` succeeds.

## Run (agent path)

```bash
D=.claude/skills/cyclaw-gotchas/driver.sh
bash $D serve      # gate.py in the background, pidfile in /tmp/cyclaw-gotchas, waits for /health (took 3-11 s here)
bash $D probe      # /health, POST /query, GET /soul without and with the API key
bash $D stop       # kills exactly the pid it started
```

What `probe` printed in this container, which is the **correct** no-Ollama,
no-index baseline:

```
{'status': 'degraded', 'index_ready': False, 'graph_ready': False, 'mode': 'hybrid'}
{"detail":{"error":"Index not built. Run: python -m retrieval.indexer","code":"INDEX_NOT_FOUND"}} [503]
401
200
```

`degraded` is normal without Ollama. `503 INDEX_NOT_FOUND` is normal here
and will stay that way: the index needs the embedding model, and the model
host is denied. `401` then `200` on `/soul` is the fail-closed key gate
working. Do not "fix" any of these.

`serve` reads `CYCLAW_API_KEY` (default `smoke-test-key`) and `GROK_API_KEY`
(default `dummy`, any non-empty value). Override on the command line for a
different key.

## Test

```bash
bash .claude/skills/cyclaw-gotchas/driver.sh test                          # whole suite: 5263 passed, 82 skipped, ~4m40s here
bash .claude/skills/cyclaw-gotchas/driver.sh test tests/test_sync_runner.py tests/test_telemetry_kill.py
```

The suite is fully green in this sandbox even though the index cannot be
built, because `tests/conftest.py` mocks the embedding model. Only
`tests/ci_rag_smoke.py`, `.claude/skills/CyClaw-Sandbox/smoke.sh`, `/run`,
and `index-doctor` need the real model; they are unrunnable here and CI's
`ollama-mock-smoke` job is where they get exercised.

The driver clears `pyproject.toml`'s `addopts` before adding its own flags.
Reason: `addopts = "-q --tb=short"` plus a second `-q` on the command line is
`-qq`, and at that level pytest **drops the final "N passed" line**, so a
green run prints only dots. Three runs in one session went unread that way.
If you invoke pytest directly, either omit `-q` or pass `-o addopts=""`, and
read the exit code (`echo "pytest exit=${PIPESTATUS[0]}"` when piped).

## Checks

```bash
bash .claude/skills/cyclaw-gotchas/driver.sh checks
```

Runs the five stdlib guards (`invariant-guard`, `doc-sync`, `config-guard`,
`dep-guard`, verify-deps `check_env_drift.py --strict`) and prints one line
each. `config-guard` reports `1 warning` on a clean tree: that is the known
C9 hybrid-posture warning, not new drift.

## Run (human path)

`python gate.py` in a terminal binds `127.0.0.1:8787` and blocks; Ctrl-C to
stop. Same defaults, no pidfile. Useless from an agent because nothing else
can run in that shell; use `serve`.

## Gotchas

Each entry: the trap, the evidence, the rule. Dated so a later session can
expire it.

### Sandbox and install

- **Nothing is preinstalled, in any interpreter.** CLAUDE.md §4 once said the
  deps live in 3.11's `dist-packages`; on 2026-09-06 `import torch` failed for
  3.10, 3.11, 3.12 and 3.13 alike. Treat the venv step as mandatory every
  session; the container is rebuilt from a generic image, not from this repo.
- **`pkill -f "python gate.py"` kills your own shell, and `pgrep -f gate.py`
  reports it as still running.** Both match the `bash -c` wrapper that is
  executing the command (exit 144 from `pkill`; a phantom "server still
  running" from `pgrep`, both observed 2026-09-06). The driver uses a pidfile
  for exactly this reason. To check liveness, hit the port:
  `curl -s --max-time 2 http://127.0.0.1:8787/health` (000 / connection
  refused means down). To kill without a pidfile, `fuser -k 8787/tcp`.
- **`uv pip install --dry-run --system` fails on this image** with the
  "externally managed" refusal before resolving anything. Not a finding about
  the Dockerfile; run the dry-run inside a venv or report it unverified.
- **The PyTorch and Hugging Face denials are per-organisation policy**, not a
  transient outage. `curl -sS "$HTTPS_PROXY/__agentproxy/status"` lists the
  denied hosts under `recentRelayFailures`. Report them as "unverified in this
  sandbox", never as a CyClaw defect.

### Runtime behaviour that looks like a bug

- **The "too many concurrent requests / second chat hangs" issue is the local
  model being single-stream, and the 409s are the fix, not the bug.**
  `harness/server.py`'s `GenerationGate` docstring: `qwen3.8:27b-mlx` on
  Apple Silicon serves one stream; a second concurrent chat (two tabs, jammed
  Enter, `/loop` plus a typed line) queues behind Metal and looks like a hang,
  so the harness answers `409 CHAT_BUSY` instead. `macos/ollama-mlx.env` pins
  `OLLAMA_NUM_PARALLEL=1` and `OLLAMA_MAX_LOADED_MODELS=1` to match. PR #1247
  (merged 2026-09-02) extended this to `POST /api/agent/run`: a separate
  `agent_run_gate` always rejects a duplicate run (`409 AGENT_RUN_BUSY`, a
  financial-risk control since a run can spend paid planner tokens), and the
  chat gate is shared only when the live-resolved chat backend and
  `deepagent_github.base_url` are the same server after canonicalising
  loopback aliases and default ports. `/loop` has its own
  `LOOP_IN_FLIGHT` claim with a 900 s TTL. Rules: never add parallelism to
  "fix" a 409; an agent run holds the shared gate for its whole life (up to
  3600 s), so chat being busy for an hour during a run is by design; and any
  new route that reaches the local model claims the gate the same
  claim/try/finally way.
- **Open thread on that PR.** Codex's fourth P2 on #1247 (a non-numeric port
  in either base URL makes `parsed.port` raise `ValueError` outside the
  guarded block, so `/api/agent/run` 500s instead of taking the cautious
  `None` path) was **not resolved before merge** and is still open as of
  2026-09-06. Fix it if you are in that file; do not report it as new.
- **`/health` `degraded`, `TELEMETRY KILL` on stdout, `503 INDEX_NOT_FOUND`,
  `needs_confirm: true` on `/query`** are all normal states, not errors
  (CLAUDE.md §4 "Environment & install" and `.claude/commands/run.md`).

### Tests and checks

- **A green run can print no summary** (see Test above). Read the exit code.
- **Run the fast, scoped tests first, then the whole suite once.** The suite
  takes ~4m40s here; `-x` on the first pass finds the first failure without
  waiting for all of it.
- **Ruff, invariant-guard, and the doc-sync checker are cheap; run them
  before every push.** `ruff check --select E,F,I,B,C4,UP,S <touched files>`
  is seconds; the F/B/S subset is what blocks merge (`lint.yml`).
- **A new skill directory with a `verify.sh` must be classified in
  `ci.yml`'s `discover-skills` step** (`heavy|yaml|stdlib` arm), or that job
  fails closed for the whole workflow. Same commit as the skill.
- **A new skill must also appear in CLAUDE.md §9** or doc-sync D1 flags it,
  and gets a thin `.claude/commands/<name>.md` wrapper (`.claude/README.md`).

### Git, PRs, and the review bots

- **A merged PR's branch is finished.** If the session's designated branch
  already merged, restart it from `origin/main` with
  `git checkout -B <branch> origin/main`; never stack new commits on merged
  history (this session, after #1317 merged).
- **The stop hook nags on every unpushed commit and dirty tree.** Commit per
  concern and push before ending a turn; do not accumulate.
- **After a squash-merge GitHub deletes the head branch, and the stale
  tracking ref makes the stop hook report phantom "N unpushed commits".**
  Once the designated branch is reset onto `origin/main`, `git push` is not
  a fast-forward against the old remote branch and `--force-with-lease`
  answers `rejected (stale info)` because the remote ref no longer exists
  (2026-09-06, after #1319). Fix: `git fetch --prune origin`, then a plain
  `git push -u origin <branch>` recreates it. No force push needed.
- **`mergeable_state: "unstable"` means checks pending or failing, not a
  conflict.** `"dirty"` is the conflict state. Read the check runs before
  acting on "unstable".
- **Only claim a check-in is armed if you created it.** On 2026-09-06 the
  #1317 timer was deleted at merge and no replacement was armed for #1318,
  while the session reported one was. `update_trigger` on a deleted id
  returns not-found; create a new `send_later` and keep **one** timer that
  names every open PR, re-scoped as PRs merge.
- **Trim `list_pull_requests` with `fields`** (`number,title,head`); the full
  payload is large and mostly body text.
- **Codex P2s are bug reports; verify against the code, then fix.** On #1318
  the bot was right that the caller (`repo_workspace._clone`) does not retry,
  so "skip the timeout retry for `repo_clone`" had silently discarded the
  operator's `gh_retries`. The fix was to make the retry able to succeed
  (clear the partial `dest` first), not to remove it. General rule: before
  disabling a retry, read what the caller does on failure; a fail-fast that
  deletes a configured budget is a regression.
- **A PR body claim the bot can check will be checked.** "The caller retries
  at a higher level" was false and was caught. Only write verifiable claims.
- **Reply on the thread, then resolve it, on PRs you opened.** The reply needs
  the Claude Code attribution footer. Leave a thread open only while a
  question to the reviewer is pending. Check merged PRs for threads left open
  (see #1247 above).
- **Bot review triggers:** the owner comments `@codex review` to start a
  Codex pass; `@codex apply fixes` hands the fix to Codex; `@copilot resolve
  the merge conflicts ...` with explicit write permission is how conflicts on
  a branch this sandbox cannot force-push get resolved (#1194 history).
- **Two branches editing `ci.yml`, `config.yaml`, `CLAUDE.md`, or the pin
  manifests must be trial-merged before opening the second PR** (CLAUDE.md
  §4 Git & PR). `git checkout -B _trial origin/main && git merge --no-ff
  <a> && git merge --no-ff <b>` and grep for `<<<<<<<`.

### Docs and doc-sync

- **An owner's recent edit is intent, not drift.** The root README said
  "multi-operator" while the threat model said "single-operator". `git log
  -L<line>,<line>:README.md` showed the owner wrote it two days after the
  threat model's stance was last touched. The right move was to ask, then
  amend the threat model (fifteenth amendment), not to revert the README.
  Always check authorship and date before "fixing" a scope or policy claim.
- **Fan out README audits to read-only subagents, one file group each,
  "report only confirmed drift with file:line and the minimal fix", then
  re-verify every finding yourself before editing.** Four groups over 38
  READMEs returned 11 findings; all 11 held up on re-read, and the re-read
  cost seconds each.
- **Docstrings are docs.** When a README derives from a package docstring,
  fix both in the same commit or the drift regrows (`agentic/harness_optimizer`).
- **`doc_sync.py` covers structured facts only** (skills list, entry points,
  config numbers, routes, node count, hook claims). README prose, the threat
  model stance, and skill bodies are manual-pass territory.

### verify-deps in this sandbox

- **Steps 1-3 (dep-guard, `extract_pins.py`, `check_env_drift.py --strict`)
  and the currency sweep run fine**; the PyPI JSON API is reachable and its
  per-version `vulnerabilities` array carries OSV advisories, so no
  `pip-audit` install is needed.
- **The install dry-runs cannot pass here** (PyTorch index denied); report
  them "unverified in this sandbox" and say why. `docker compose config
  --quiet` does work without a daemon.
- **Every advisory hit on the current pins (5 chromadb, 1 nltk) is a dated
  acceptance in `SECURITY.md`** with no upstream fix version. Re-reporting them
  is noise; a *new* id is the signal.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No matching distribution found for torch==2.13.0+cpu` after five proxy retries | CPU index denied by the egress proxy | `driver.sh venv` falls back to PyPI torch automatically |
| `OSError: libcudart.so.13: cannot open shared object file` on `import torch` | plain torch installed with `--no-deps` | reinstall without `--no-deps`; the CUDA libs are required at import on Linux |
| pytest prints dots and `[100%]` but no `N passed` line | `-q` doubled with `addopts` → `-qq` | use `driver.sh test` or `-o addopts=""`; read the exit code |
| `ModuleNotFoundError: pytest` / `torch` under `python3` | `python3` is 3.11 with nothing installed | use `/root/.venv-cyclaw-312/bin/python -m pytest` |
| shell exits 144 after `pkill -f "python gate.py"`, or `pgrep -f gate.py` says a stopped server is running | both patterns match the invoking `bash -c` shell | `driver.sh stop` (pidfile); check liveness with `curl` on the port |
| `POST /query` → `503 INDEX_NOT_FOUND` after a successful serve | no index; model host denied | expected here; test retrieval via the unit suite, not the live route |
| `uv pip install --dry-run --system ...` → "externally managed" | system interpreter refuses | run inside a venv, or report unverified |
| `discover-skills` job: `unclassified skill '<name>'` | new `verify.sh` without a profile arm in `ci.yml` | add the skill to the `stdlib` (or `yaml`/`heavy`) case |
| `update_trigger` → "requested resource was not found" | the timer was deleted earlier | `send_later` a new one; do not assume one exists |
| stop hook: "N unpushed commits" on a branch you just reset to `origin/main`; `push --force-with-lease` → `rejected (stale info)` | GitHub deleted the merged head branch; local tracking ref is stale | `git fetch --prune origin` then plain `git push -u origin <branch>` |
| harness `409 CHAT_BUSY` / `AGENT_RUN_BUSY` / `LOOP_IN_FLIGHT` | single-stream local model, gate held by another turn or an agent run | wait or stop the other work; never add parallelism |

## Guardrails

- This skill changes nothing by itself. `driver.sh` writes only under
  `/tmp/cyclaw-gotchas` and `/root/.venv-cyclaw-312` (override with
  `CYCLAW_SCRATCH` / `CYCLAW_VENV`); `serve` creates `logs/` in the repo
  exactly as `gate.py` would.
- CLAUDE.md §3's six invariants and §7's escalation tiers still govern. A
  gotcha here is never a licence to skip a guard; it is a reason you will not
  be surprised by one.
- Never "fix" a 409 from the harness gates, a `degraded` health, or a
  `503 INDEX_NOT_FOUND` by changing code. They are controls and fail-soft
  states, not defects.

## Maintaining this file

Add a gotcha only with evidence: the session date, the PR number, or the
file:line that proved it. Expire one the same way (strike it with the date
and what changed). Keep CLAUDE.md §4 as the codebase trap list and link to it;
keep this as the session trap list. `bash .claude/skills/cyclaw-gotchas/verify.sh`
checks the driver parses, the frontmatter name matches the directory, every
repo path cited here still exists, the wrapper and CLAUDE.md row are present,
and `driver.sh inventory` runs with nothing installed.
