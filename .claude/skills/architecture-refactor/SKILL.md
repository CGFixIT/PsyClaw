---
name: architecture-refactor
description: Iterative architecture refactor loop — refactors code, live-tests after each significant step, runs autoreview, commits, and tracks progress in /tmp/refactor-{projectname}.md. Use when asked to refactor the architecture, clean up structure, or improve code organization autonomously.
---

# Architecture Refactor Loop

Refactor the codebase iteratively until the architecture is clean and coherent. After each significant step: live-test the system, run autoreview, and commit. Track all progress in `/tmp/refactor-{projectname}.md`.

---

## Setup

Determine the project name from the working directory or `CLAUDE.md`:

```bash
PROJNAME=$(basename "$PWD")
TRACKER="/tmp/refactor-${PROJNAME}.md"
```

Initialize the tracker if it doesn't exist:

```bash
cat > "$TRACKER" <<EOF
# Architecture Refactor — $PROJNAME
Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

## Goals
- Clean, modular architecture
- No circular imports or god-modules
- Clear separation of concerns

## Progress
EOF
```

---

## Loop Body

Repeat the following cycle until satisfied with the architecture:

### 1. Assess

Survey the current structure — look for:
- God modules (files >400 lines doing multiple unrelated things)
- Circular or tangled imports
- Business logic mixed with I/O or framework glue
- Duplicate abstractions serving the same purpose
- Modules that are hard to test in isolation

Record findings in the tracker under a new `### Step N — Assessment` heading.

### 2. Plan one step

Pick the single highest-leverage refactor available. Prefer:
- Extracting a well-bounded module over reorganizing everything at once
- Renaming / clarifying interfaces over rewriting implementations
- Deleting dead code over adding new abstractions

Document the chosen step and rationale in the tracker.

### 3. Execute

Make the change. Keep the diff focused — one concern per commit.

### 4. Live-test

Run the project's smoke test to confirm nothing is broken:

```bash
bash .claude/skills/CyClaw-Sandbox/smoke.sh
```

If the smoke script is unavailable, fall back to:

```bash
GROK_API_KEY=dummy pytest tests/ -q --tb=short
```

Record the test result in the tracker.

### 5. Autoreview

```bash
/code-review
```

Address any correctness bugs flagged before moving on. Log findings in the tracker.

### 6. Commit

```bash
git add -p          # stage only the refactor change
git commit -m "refactor: <what changed and why>"
```

Append the commit hash to the tracker entry.

### 7. Update tracker

Add a `### Step N — Done` section with:
- What changed
- Test result (pass/fail + any notable output)
- Autoreview outcome
- Commit hash

Then loop back to **Assess** for the next step.

---

## Stopping Criteria

Stop when all of the following are true:

- No module does more than one clearly named thing
- Imports form a clean DAG (no cycles)
- Every public interface has an obvious, single purpose
- The smoke test passes
- Autoreview finds no correctness issues

Append a `## Final State` summary to the tracker and report done.

---

## Tracker Format

```markdown
# Architecture Refactor — cyclaw
Started: 2026-06-20T12:00:00Z

## Goals
...

## Progress

### Step 1 — Assessment
...

### Step 1 — Done
- Changed: extracted rate_limit logic from gate.py → utils/rate_limit.py
- Tests: PASS (smoke.sh 6/6)
- Autoreview: no issues
- Commit: abc1234

### Step 2 — Assessment
...
```

---

## Notes

- Never squash mid-refactor commits — each step should be independently reviewable.
- If a step breaks tests, revert and pick a smaller scope before retrying.
- `{projectname}` in the tracker path is the literal `basename $PWD`, e.g. `CyClaw` → `/tmp/refactor-CyClaw.md`.
