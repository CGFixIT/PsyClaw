---
name: speed-refactor
description: Iterative speed optimization loop — continuously optimizes code for performance, measures page-load across every page under repeatable test conditions after each change, and continues until every page and module loads or runs in under 50 ms.
---

# Speed Refactor Loop

Optimize the codebase for speed iteratively. After each significant change, measure page-load performance across every page under identical, repeatable conditions. Continue until every page and code module independently runs or loads in under 50 ms.

---

## Setup

```bash
PROJNAME=$(basename "$PWD")
TRACKER="/tmp/refactor-${PROJNAME}.md"
BASELINE_FILE="/tmp/speed-baseline-${PROJNAME}.json"
```

Initialize the tracker if it doesn't exist:

```bash
[ -f "$TRACKER" ] || cat > "$TRACKER" <<EOF
# Speed Refactor — $PROJNAME
Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Target: every page and module < 50 ms

## Baseline
(run measurement before first change)

## Progress
EOF
```

---

## Measurement Protocol

All measurements must be taken under the same repeatable conditions every iteration. Do not compare across different machine states, load levels, or warmup counts.

### Server startup (cold)

```bash
# Kill any existing instance
pkill -f "uvicorn gate" 2>/dev/null; sleep 1

# Start fresh
GROK_API_KEY=dummy uvicorn gate:app --host 127.0.0.1 --port 8787 &
SERVER_PID=$!
sleep 2   # fixed warm-up window — do not vary
```

### Measure every endpoint

Run each endpoint 5 times and record the **median** (not mean — median is stable against outlier spikes):

```bash
measure() {
  local label="$1" method="$2" url="$3" data="$4"
  local times=()
  for i in 1 2 3 4 5; do
    if [ -n "$data" ]; then
      ms=$(curl -s -o /dev/null -w "%{time_total}" -X "$method" "$url" \
           -H "Content-Type: application/json" -d "$data")
    else
      ms=$(curl -s -o /dev/null -w "%{time_total}" "$url")
    fi
    times+=("$ms")
  done
  # print label and sorted times; median is index 2 (0-based) of 5
  echo "$label: ${times[*]}" | awk '{
    n=NF-1; split($0,a," "); asort(a);
    printf "%s median=%.0fms\n", $1, a[int(n/2)+1]*1000
  }'
}

BASE="http://127.0.0.1:8787"
measure "GET /health"          GET  "$BASE/health"
measure "GET /soul"            GET  "$BASE/soul"
measure "GET /static/terminal" GET  "$BASE/static/terminal.html"
measure "POST /query (vault)"  POST "$BASE/query" '{"query":"What is RRF?"}'
measure "POST /query (offline)" POST "$BASE/query" \
  '{"query":"What is CyClaw?","user_confirmed_online":false}'
```

Teardown:

```bash
kill $SERVER_PID 2>/dev/null
```

Record results in the tracker under `### Step N — Measurement`.

### Pass/fail gate

A step passes when **all** measured endpoints report median < 50 ms. If any endpoint is ≥ 50 ms, that endpoint is the target for the next optimization step.

---

## Loop Body

Repeat until all endpoints pass:

### 1. Identify the slowest path

From the latest measurement, pick the endpoint with the highest median. Profile it:

```bash
# Python-level profiling (attach to a running server or use cProfile on the handler)
GROK_API_KEY=dummy python3 -m cProfile -s cumulative gate.py 2>&1 | head -40
```

Or add a quick timing probe inline:

```python
import time
t0 = time.perf_counter()
# ... suspect code ...
print(f"[TIMING] {time.perf_counter() - t0:.4f}s", flush=True)
```

Record the bottleneck and root cause in the tracker.

### 2. Plan one targeted change

Pick the single change with the highest expected speedup-to-risk ratio:

- **Cache** expensive repeated computations (BM25 index load, ChromaDB client init, personality file read)
- **Lazy-load** heavy imports that aren't needed on every request
- **Defer** work that doesn't need to happen in the hot path (logging flushes, telemetry hooks)
- **Reduce I/O** — batch reads, avoid redundant disk hits per request
- **Tighten graph nodes** — short-circuit LangGraph nodes that re-derive already-known state

Do not change multiple things at once — it makes measurement ambiguous.

### 3. Execute

Make the targeted change. Keep the diff minimal and focused.

### 4. Measure

Re-run the full measurement protocol (all endpoints, 5 runs each, median). Compare against the previous step's numbers.

### 5. Smoke-test correctness

Speed wins are worthless if correctness breaks:

```bash
bash .claude/skills/CyClaw-Sandbox/smoke.sh
```

If any smoke check fails, **revert the change** and pick a different approach.

### 6. Commit

Only commit if both the speed gate improved **and** smoke tests pass:

```bash
git add -p
git commit -m "perf: <what changed, measured improvement>"
```

Example: `perf: cache BM25 index at startup — /query median 210ms → 38ms`

### 7. Update tracker

```markdown
### Step N — Done
- Target: POST /query (was 210 ms median)
- Change: moved BM25Retriever init to module-level singleton
- Result: /query 38 ms, /health 4 ms, /soul 6 ms, /static 3 ms — ALL PASS ✓
- Smoke: 6/6
- Commit: abc1234
```

Loop back to **Identify the slowest path**.

---

## Stopping Criteria

Stop when the measurement run shows **every endpoint** at median < 50 ms for **two consecutive iterations** (confirms the result isn't a one-off).

Append a `## Final State` section to the tracker:

```markdown
## Final State
Completed: <timestamp>
All endpoints < 50 ms for 2 consecutive measurement runs.

| Endpoint              | Step 1 (ms) | Final (ms) |
|-----------------------|-------------|------------|
| GET /health           | 12          | 3          |
| GET /soul             | 45          | 7          |
| GET /static/terminal  | 8           | 2          |
| POST /query (vault)   | 340         | 41         |
| POST /query (offline) | 280         | 35         |
```

---

## Notes

- **Never vary warmup time** between runs — the 2 s sleep must be constant or measurements are incomparable.
- **Median over mean** — a single GC pause or cold-cache miss inflates mean; median is stable.
- **Revert if correctness breaks** — a 50 ms page that returns wrong data is worse than a 200 ms page that returns correct data.
- **Module-level load time counts** — if `import gate` takes 300 ms, that's a cold-start problem worth fixing even if per-request latency is fast.
- **`{projectname}`** in tracker/baseline paths is `basename $PWD`, e.g. `CyClaw` → `/tmp/refactor-CyClaw.md`.
