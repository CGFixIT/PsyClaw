---
name: refactor
description: >-
  Iterative CyClaw refactor loop for architecture cleanup and speed work. Use
  when working in CGFixIT/CyClaw and the user asks Codex to refactor
  architecture, clean up structure, improve code organization, reduce god
  modules, remove duplication, or continuously optimize endpoint and module
  performance with live verification, autoreview, commits, and progress tracked
  in /tmp/refactor-{projectname}.md.
---

# Refactor

Use this skill for Codex-native CyClaw refactor work. It combines the Claude
architecture-refactor and speed-refactor loops into one workflow, but it must
stay grounded in CyClaw's current repo shape, Codex tools, and sandbox rules.

Always apply ponytail discipline during this skill:

- read the real flow first
- prefer deletion over addition
- reuse existing helpers before writing new ones
- fix shared root causes instead of patching one caller
- keep each step independently reviewable

## Operating Rules

- Use the active shell and platform. Bash snippets below are the reference
  shape; adapt them to PowerShell locally without changing semantics or paths.
- Work from repo-native CyClaw commands and `.codex` guidance first. Do not
  rely on Claude-only tool names or hooks.
- Keep one tracker file for the whole loop:
  `/tmp/refactor-{projectname}.md`.
  On Windows, use `$env:TEMP\refactor-{projectname}.md` and keep the same
  headings.
- Make exactly one high-leverage change per loop iteration.
- After each significant step: verify live behavior, do an explicit self-review
  pass, commit, and append results to the tracker.
- Never claim success on speed unless measurements were taken under identical
  conditions and all required checks passed twice in a row.

## Setup

Derive the project name from the working directory and initialize the shared
tracker if it does not exist.

```bash
PROJNAME=$(basename "$PWD")
TRACKER="/tmp/refactor-${PROJNAME}.md"
BASELINE_FILE="/tmp/speed-baseline-${PROJNAME}.json"

[ -f "$TRACKER" ] || cat > "$TRACKER" <<EOF
# Refactor - $PROJNAME
Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Targets:
- Clean, modular architecture
- No circular imports or god-modules
- Clear separation of concerns
- Every measured endpoint and changed hot-path module < 50 ms

## Baseline
(capture architecture findings and first measurement before the first change)

## Progress
EOF
```

On PowerShell, keep the same filenames and headings. Do not create a second
tracker format.

## Baseline Pass

Before the first edit:

1. Read `AGENTS.md`, `.codex/routines/refactor.md`, and the relevant code path.
2. Record architecture findings:
   - god modules
   - circular or tangled imports
   - business logic mixed with framework or I/O glue
   - duplicate abstractions
   - modules that are hard to test in isolation
3. Record a speed baseline with the measurement protocol below.
4. If the user asked for refactor but not performance, still capture one speed
   baseline so cold-start regressions are visible.

## Measurement Protocol

Keep the measurement conditions identical across iterations.

### Cold server startup

```bash
pkill -f "uvicorn gate" 2>/dev/null || true
sleep 1

export GROK_API_KEY=dummy
export CYCLAW_API_KEY=smoke-test-key-ci
uvicorn gate:app --host 127.0.0.1 --port 8787 &
SERVER_PID=$!
sleep 2
```

CyClaw-specific correction: `/soul` requires bearer auth. Measure the real
authenticated path, not a fail-closed `401`.

### Endpoint timings

Run each endpoint 5 times and record the median.

```bash
measure() {
  local label="$1" method="$2" url="$3" data="$4" auth="$5"
  local times=()
  for i in 1 2 3 4 5; do
    if [ -n "$data" ]; then
      ms=$(curl -s -o /dev/null -w "%{time_total}" -X "$method" "$url" \
           -H "Content-Type: application/json" \
           ${auth:+-H "Authorization: Bearer $CYCLAW_API_KEY"} \
           -d "$data")
    else
      ms=$(curl -s -o /dev/null -w "%{time_total}" "$url" \
           ${auth:+-H "Authorization: Bearer $CYCLAW_API_KEY"})
    fi
    times+=("$ms")
  done
  echo "$label: ${times[*]}" | awk '{
    n=NF-1; split($0,a," "); asort(a);
    printf "%s median=%.0fms\n", $1, a[int(n/2)+1]*1000
  }'
}

BASE="http://127.0.0.1:8787"
measure "GET /health"              GET  "$BASE/health"
measure "GET /soul"                GET  "$BASE/soul" "" yes
measure "GET /static/terminal"     GET  "$BASE/static/terminal.html"
measure "POST /query (vault)"      POST "$BASE/query" '{"query":"What is RRF?"}'
measure "POST /query (offline)"    POST "$BASE/query" '{"query":"What is CyClaw?","user_confirmed_online":false}'
```

### Module-load timings

Cold import time counts. Measure:

- `gate`
- every Python module changed in the current step
- any module suspected of dominating startup cost

Reference command:

```bash
python -X importtime -c "import gate" 2> /tmp/import-gate.txt
```

Treat module import times >= 50 ms as open performance work.

### Teardown

```bash
kill $SERVER_PID 2>/dev/null || true
```

Record results under `### Step N - Measurement`.

## Loop

Repeat until the stopping criteria are met.

### 1. Assess the highest-leverage problem

Use the latest tracker entry plus the code:

- If any measured endpoint is >= 50 ms, target the slowest one first.
- If endpoint timings are acceptable but architecture is still tangled, target
  the most reviewable structural cleanup.
- If both are bad, pick the single change with the best speedup-to-risk or
  clarity-to-risk ratio.

Typical CyClaw speed targets:

- repeated retriever initialization
- repeated personality or config reads
- heavy imports in the hot path
- redundant filesystem I/O
- graph nodes recomputing already-known state

Typical CyClaw architecture targets:

- extraction from `gate.py`, `graph.py`, or `utils/personality.py` only when a
  real single-purpose seam exists
- interface clarification or rename instead of rewrite
- dead-code deletion before new abstractions

### 2. Plan one focused change

Write a short tracker entry:

- target
- suspected root cause
- chosen change
- why this is the smallest high-leverage move

Do not batch unrelated cleanup into the same step.

### 3. Execute

Make the change with the smallest coherent diff.

### 4. Verify correctness first

Prefer the narrowest meaningful check:

```bash
python -m tests.ci_rag_smoke
GROK_API_KEY=dummy pytest tests/ -q --tb=short
GROK_API_KEY=dummy pytest tests/test_graph.py -q --tb=short
```

For live endpoint verification, use the repo-native run guidance from
`.codex/skills/cyclaw-command-run/` or `.codex/skills/cyclaw-run-cyclaw/`.

If the touched area has no runnable local test path, say so in the tracker and
use the strongest static check available.

### 5. Measure again

Re-run the full measurement protocol after the change. Compare against the
previous step, not memory.

### 6. Autoreview

Do an explicit review pass before commit:

- inspect the diff for correctness regressions
- check for unchanged invariants and isolation rules
- use `.codex/routines/pr-review.md` standards
- fix any bug you find before committing

### 7. Commit

Commit only when verification passed and either:

- the speed gate improved, or
- the architecture is materially cleaner without behavior drift

Use direct messages such as:

```bash
git add -p
git commit -m "refactor: extract rate-limit wiring from gate"
git commit -m "perf: cache retriever startup path for query latency"
```

### 8. Update the tracker

Append:

```markdown
### Step N - Done
- Target: ...
- Change: ...
- Result: ...
- Tests: PASS/FAIL ...
- Autoreview: no issues / issues fixed
- Commit: abc1234
```

Then loop.

## Stopping Criteria

Stop only when all are true:

- no obvious circular imports remain
- no module does more than one clearly named thing in the touched scope
- public interfaces changed in the loop have obvious single purposes
- smoke or targeted tests pass
- self-review finds no correctness issue
- all measured endpoints are < 50 ms for two consecutive iterations
- `gate` and each changed hot-path module are < 50 ms cold import for the same
  two consecutive iterations

Append:

```markdown
## Final State
Completed: <timestamp>
All required endpoint and module checks passed twice consecutively.
```

## Guardrails

- Preserve the five CyClaw invariants: RAG-first retrieval, topology as policy,
  triple-gated external fallback, audit convergence, and human-gated soul
  mutation.
- Do not weaken security controls to hit a latency target.
- Do not mutate `data/personality/soul.md` unless the user explicitly asked.
- Keep optional `sync/`, `agentic/`, `agentic/fsconnect/`,
  `agentic/sqlconnect/`, and `guardrails/` layers out of the core request path.
- Never squash mid-loop commits.
- If a step breaks tests or smoke checks, revert that step and retry smaller.

## Final Response

Report:

- the current loop state
- the last committed step
- measurements before/after
- checks run
- remaining blockers or unverified areas
